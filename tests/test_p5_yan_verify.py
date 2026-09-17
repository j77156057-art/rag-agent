"""P5 独立验证（严过关 / software-qa-engineer-5）：每项目宿主分桶 + 引擎 GPU 软租约。

原则：
* 只新增测试，**不改任何源码**；
* 不启动真引擎（Popen / 嵌入全部 monkeypatch）；
* 不读写仓库真实的 ``.docmind_state.json`` / 会话 / chroma（项目上下文一律用 patch 模拟）。
"""
import os
import sys
import threading
import time
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import config  # noqa: E402
import desktop_bridge as db  # noqa: E402
import game_workbench as gw  # noqa: E402
import gpu_coordinator as gpu  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

FAKE_A = 0x000A11
FAKE_B = 0x000B22
FAKE_DEFAULT = 0x000DEF


def _clear_hosts():
    with db._HOST_LOCK:
        db._HOST_STATE.clear()
    api._DESKTOP_HOSTS.clear()


# ============================================================ A. 宿主分桶 & 参数顺序
class A_HostBucketingTests(unittest.TestCase):
    def setUp(self):
        _clear_hosts()

    def tearDown(self):
        _clear_hosts()

    def test_both_arg_orders_land_same_bucket(self):
        """set_host(hwnd, pid) 与 set_host(pid, hwnd) 必须落到同一桶。"""
        db.set_host(FAKE_A, 'prjA')          # 旧式顺序 hwnd 在前
        s1 = db.hosts_snapshot()
        _clear_hosts()
        db.set_host('prjA', FAKE_A)          # 团队约定 project 在前
        s2 = db.hosts_snapshot()
        self.assertEqual(s1, {'prjA': FAKE_A})
        self.assertEqual(s2, {'prjA': FAKE_A})

    def test_int_hwnd_str_pid_is_unambiguous(self):
        """hwnd=int 且 pid=str：绝不把 int 误当 project_id。"""
        db.set_host(FAKE_A, 'prjA')
        self.assertEqual(db.hosts_snapshot(), {'prjA': FAKE_A})
        self.assertNotIn(None, db.hosts_snapshot())
        self.assertEqual(db.host_hwnd('prjA'), FAKE_A)
        self.assertIsNone(db.host_hwnd())          # None 键未登记
        # 反向顺序同样正确
        db.set_host('prjB', FAKE_B)
        self.assertEqual(db.host_hwnd('prjB'), FAKE_B)

    def test_legacy_single_arg_equals_global_default(self):
        """旧式单参 set_host(hwnd) == 无项目键；host_hwnd()/get_host() 取回。"""
        self.assertIsNone(db.get_host())
        db.set_host(999)
        self.assertEqual(db.get_host(), 999)
        self.assertEqual(db.host_hwnd(), 999)
        self.assertEqual(db.host_hwnd('anything'), 999)   # 未登记项目回落默认
        self.assertEqual(db.hosts_snapshot(), {None: 999})
        db.set_host(None)                                  # 清除默认
        self.assertIsNone(db.get_host())
        self.assertEqual(db.hosts_snapshot(), {})

    def test_project_isolation_and_fallback_are_stable(self):
        db.set_host(555)                 # 默认
        db.set_host('prjX', 777)
        self.assertEqual(db.host_hwnd('prjX'), 777)
        self.assertEqual(db.host_hwnd('prjY'), 555)   # 未登记 → 默认
        self.assertEqual(db.host_hwnd(''), 555)       # 空串 → 默认
        self.assertEqual(db.host_hwnd(None), 555)     # None → 默认
        self.assertEqual(db.hosts_snapshot(), {None: 555, 'prjX': 777})

    def test_clearing_project_via_none(self):
        db.set_host('prjC', 333)
        db.set_host('prjC', None)        # 清该项目
        self.assertIsNone(db.host_hwnd('prjC'))
        # project-first + None（首参 str，第二参 None）也要清掉
        db.set_host('prjD', 444)
        db.set_host('prjD', None)
        self.assertNotIn('prjD', db.hosts_snapshot())

    def test_snapshot_is_a_copy_not_a_reference(self):
        db.set_host('prjX', 777)
        snap = db.hosts_snapshot()
        snap['prjX'] = 1        # 改快照
        snap['new'] = 2         # 加键
        self.assertEqual(db.host_hwnd('prjX'), 777)              # 内部未被污染
        self.assertEqual(db.hosts_snapshot(), {'prjX': 777})
        self.assertNotIn('new', db.hosts_snapshot())

    def test_50_threads_concurrent_set_no_loss_no_exception(self):
        n = 50
        barrier = threading.Barrier(n)
        errors = []

        def worker(i):
            try:
                barrier.wait(timeout=10)
                db.set_host('prj-%02d' % i, 1000 + i)
                for _ in range(25):
                    db.host_hwnd('prj-%02d' % i)
                    db.hosts_snapshot()
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(errors, [])
        self.assertTrue(all(not t.is_alive() for t in threads), '有线程未结束（疑似死锁）')
        snap = db.hosts_snapshot()
        self.assertEqual(len(snap), n)
        for i in range(n):
            self.assertEqual(snap['prj-%02d' % i], 1000 + i)


# ============================================================ B. 端点路由 & 生命线
class B_HostEndpointTests(unittest.TestCase):
    def setUp(self):
        _clear_hosts()

    def tearDown(self):
        _clear_hosts()

    def test_post_get_per_project_isolation(self):
        client = TestClient(api.app)
        client.post('/api/desktop/host', json={'hwnd': FAKE_A, 'project_id': 'prjA'})
        client.post('/api/desktop/host', json={'hwnd': FAKE_B, 'project_id': 'prjB'})
        ga = client.get('/api/desktop/host', params={'project_id': 'prjA'}).json()
        gb = client.get('/api/desktop/host', params={'project_id': 'prjB'}).json()
        self.assertEqual(ga['host_hwnd'], FAKE_A)
        self.assertEqual(gb['host_hwnd'], FAKE_B)
        self.assertEqual(ga['hosts'], {'prjA': FAKE_A, 'prjB': FAKE_B})

    def test_lifeline_no_project_context_is_global(self):
        """无项目上下文（_request_project_id 为空）时，行为与改造前一致：全局键。"""
        client = TestClient(api.app)
        with patch.object(api, '_request_project_id', return_value=''):
            client.post('/api/desktop/host', json={'hwnd': 999})
            g = client.get('/api/desktop/host').json()
        self.assertEqual(g['host_hwnd'], 999)
        self.assertIsNone(g['project_id'])
        self.assertEqual(api._DESKTOP_HOSTS, {None: 999})

    def test_lifeline_host_survives_project_switch(self):
        """生命线（P5 前语义）：桌面壳不传 project_id 注册的宿主，换项目后仍应能查到。

        真机单窗口流程：壳启动时 POST {hwnd}（不带项目头）→ 用户随后在 UI 切项目 →
        前端 GET /api/desktop/host 带新项目头 → 期望仍取回同一宿主（改造前宿主是全局的）。
        """
        client = TestClient(api.app)
        # 1) 壳在「当前项目 prjP1」时注册宿主（请求不带项目头 → 后端解析为当前项目）
        with patch.object(api, '_request_project_id', return_value='prjP1'):
            r = client.post('/api/desktop/host', json={'hwnd': 999}).json()
        self.assertEqual(r['host_hwnd'], 999)
        # 2) 用户切到 prjP2，前端带 prjP2 头查询宿主
        with patch.object(api, '_request_project_id', return_value='prjP2'):
            g = client.get('/api/desktop/host').json()
        # 改造前（宿主全局）此处应为 999；若为 None 则生命线在「切项目」路径上断裂
        self.assertEqual(g['host_hwnd'], 999,
                         '换项目后宿主丢失：宿主被登记到注册时刻的当前项目桶，而非全局默认桶')

    def _with_root(self, fn):
        with tempfile.TemporaryDirectory() as d:
            prev = config.get_runtime('code_root')
            config.set_runtime('code_root', d)
            try:
                return fn()
            finally:
                config.set_runtime('code_root', prev or '')

    def test_engine_embed_picks_request_project_host(self):
        db.set_host(FAKE_A, 'prjA')
        db.set_host(FAKE_B, 'prjB')
        api._DESKTOP_HOSTS['prjA'] = FAKE_A
        api._DESKTOP_HOSTS['prjB'] = FAKE_B
        seen = {}

        def fake_embed(*args, **kwargs):
            seen['host'] = args[1] if len(args) > 1 else kwargs.get('host_hwnd')
            return {'ok': False, 'error': '引擎尚未运行。'}

        def run():
            with patch.object(api, 'engine_embed', side_effect=fake_embed), \
                 patch.object(api, '_request_project_id', return_value='prjB'):
                TestClient(api.app).post('/api/engine/embed', json={'host_hwnd': 0})
        self._with_root(run)
        self.assertEqual(seen['host'], FAKE_B)

    def test_engine_embed_falls_back_to_default_without_project(self):
        api._DESKTOP_HOSTS[None] = FAKE_DEFAULT
        seen = {}

        def fake_embed(*args, **kwargs):
            seen['host'] = args[1] if len(args) > 1 else kwargs.get('host_hwnd')
            return {'ok': False, 'error': 'x'}

        def run():
            with patch.object(api, 'engine_embed', side_effect=fake_embed), \
                 patch.object(api, '_request_project_id', return_value=''):
                TestClient(api.app).post('/api/engine/embed', json={'host_hwnd': 0})
        self._with_root(run)
        self.assertEqual(seen['host'], FAKE_DEFAULT)

    def test_engine_start_picks_request_project_host(self):
        db.set_host(FAKE_A, 'prjA')
        api._DESKTOP_HOSTS['prjA'] = FAKE_A
        seen = {}

        def fake_start(*args, **kwargs):
            seen['host'] = args[3]
            return {'ok': True, 'running': True}

        async def fake_stop(root):
            return ([], [])

        def run():
            with patch.object(api, 'engine_start', side_effect=fake_start), \
                 patch.object(api, '_stop_other_engines', side_effect=fake_stop), \
                 patch.object(api, '_request_project_id', return_value='prjA'):
                TestClient(api.app).post('/api/engine/start', json={'host_hwnd': 0})
        self._with_root(run)
        self.assertEqual(seen['host'], FAKE_A)

    def test_engine_start_notice_merge_does_not_override(self):
        """engine_start 返回的 notice 与自动停引擎 warning 必须合并，不能被覆盖。"""
        async def fake_stop(root):
            return (['other-root'], ['warn-A'])

        def fake_start(*args, **kwargs):
            return {'ok': True, 'running': True, 'notice': '显存不足：软租约不阻断'}

        def run():
            with patch.object(api, 'engine_start', side_effect=fake_start), \
                 patch.object(api, '_stop_other_engines', side_effect=fake_stop), \
                 patch.object(api, '_request_project_id', return_value=''):
                return TestClient(api.app).post('/api/engine/start', json={'host_hwnd': 0}).json()
        res = self._with_root(run)
        self.assertIn('显存不足：软租约不阻断', res.get('notice', ''))
        self.assertIn('warn-A', res.get('notice', ''))
        self.assertIn('other-root', res.get('notice', ''))


# ============================================================ C. GPU 软租约
@unittest.skipIf(gpu.GPU_MODE == 'parallel', 'parallel 模式不跑租约语义')
class C_SoftLeaseTests(unittest.TestCase):
    def setUp(self):
        gpu.force_release(None)
        gpu.set_gpu_probe(None)
        self.addCleanup(lambda: gpu.force_release(None))
        self.addCleanup(lambda: gpu.set_gpu_probe(None))

    def _soft(self):
        with gpu._cv:
            return dict(gpu._soft_leases)

    def _hard(self):
        with gpu._cv:
            return dict(gpu._leases)

    def test_soft_soft_both_succeed(self):
        a = gpu.acquire_lease('s:a', exclusive=False)
        b = gpu.acquire_lease('s:b', exclusive=False)
        self.assertTrue(a['ok'] and b['ok'])
        self.assertTrue(a.get('soft') and a.get('exclusive') is False)
        self.assertEqual(set(self._soft()), {'s:a', 's:b'})
        self.assertEqual(self._hard(), {})            # 软租约不占互斥槽
        gpu.release('s:a')
        gpu.release('s:b')
        self.assertEqual(self._soft(), {})

    def test_hard_hard_still_mutually_exclusive(self):
        h1 = gpu.acquire_lease('h:1', timeout=0)
        self.assertTrue(h1['ok'])
        h2 = gpu.acquire_lease('h:2', timeout=0)
        self.assertFalse(h2['ok'])
        self.assertEqual(h2['reason'], 'busy')
        self.assertEqual(len(self._hard()), 1)
        gpu.release('h:1')
        self.assertEqual(self._hard(), {})

    def test_hard_not_blocked_by_soft(self):
        gpu.acquire_lease('s:a', exclusive=False)
        h = gpu.acquire_lease('h:1', timeout=0)
        self.assertTrue(h['ok'], h)
        self.assertEqual(len(self._hard()), 1)
        self.assertIn('s:a', self._soft())            # 软租约仍在
        gpu.release('s:a')
        gpu.release('h:1')
        self.assertEqual((self._soft(), self._hard()), ({}, {}))

    def test_soft_not_blocked_by_hard(self):
        h = gpu.acquire_lease('h:1', timeout=0)
        self.assertTrue(h['ok'])
        s = gpu.acquire_lease('s:a', exclusive=False)
        self.assertTrue(s['ok'], s)
        self.assertEqual(len(self._hard()), 1)
        self.assertIn('s:a', self._soft())
        gpu.release('s:a')
        gpu.release('h:1')

    def test_soft_release_leaves_no_leak(self):
        gpu.acquire_lease('s:x', exclusive=False)
        gpu.release('s:x')
        self.assertNotIn('s:x', self._soft())
        self.assertEqual(gpu.status()['holders'], [])

    def test_force_release_owner_clears_soft(self):
        gpu.acquire_lease('s:x', exclusive=False)
        got = gpu.force_release('s:x')
        self.assertEqual(got, 's:x')
        self.assertNotIn('s:x', self._soft())
        self.assertIsNone(gpu.force_release('never'))     # 不存在 → None

    def test_force_release_all_clears_soft_and_hard(self):
        gpu.acquire_lease('s:a', exclusive=False)
        gpu.acquire_lease('s:b', exclusive=False)
        gpu.acquire_lease('h:1', timeout=0)
        got = gpu.force_release(None)
        self.assertIsNotNone(got)
        self.assertEqual((self._soft(), self._hard()), ({}, {}))
        for name in ('s:a', 's:b', 'h:1'):
            self.assertIn(name, got)

    def test_soft_ttl_expiry_reclaimed(self):
        gpu.acquire_lease('s:t', ttl=0.01, exclusive=False)
        self.assertIn('s:t', self._soft())
        time.sleep(0.03)
        gpu.status()                                      # 惰性回收
        self.assertNotIn('s:t', self._soft())

    def test_soft_visible_in_holders(self):
        gpu.acquire_lease('s:a', exclusive=False)
        holders = gpu.status()['holders']
        softs = [h for h in holders if h.get('soft')]
        self.assertEqual([h['owner'] for h in softs], ['s:a'])
        self.assertFalse(softs[0]['exclusive'])
        gpu.release('s:a')

    def test_gpu_status_endpoint_includes_soft(self):
        gpu.acquire_lease('s:ep', exclusive=False)
        try:
            data = TestClient(api.app).get('/api/gpu/status').json()
            owners = [h['owner'] for h in data.get('holders', [])]
            self.assertIn('s:ep', owners)
            self.assertIn('s:ep', [h['owner'] for h in data.get('soft_holders', [])])
        finally:
            gpu.release('s:ep')

    def test_hard_min_free_still_hard_rejected(self):
        """回归：独占请求显存门槛不足仍**硬拒绝**（软租约的放宽不影响独占）。"""
        gpu.set_gpu_probe(lambda: [{'index': 0, 'name': 'FakeGPU', 'used_mb': 9000,
                                    'total_mb': 10000, 'utilization': 0.0,
                                    'temperature_c': None}])
        r = gpu.acquire_lease('h:low', min_free_mb=5000, timeout=0)
        self.assertFalse(r['ok'])
        self.assertEqual(r['reason'], 'insufficient_memory')
        self.assertEqual(self._hard(), {})

    def test_hard_min_free_granted_when_enough(self):
        gpu.set_gpu_probe(lambda: [{'index': 0, 'name': 'FakeGPU', 'used_mb': 100,
                                    'total_mb': 10000, 'utilization': 0.0,
                                    'temperature_c': None}])
        r = gpu.acquire_lease('h:ok', min_free_mb=5000, timeout=0)
        self.assertTrue(r['ok'], r)
        gpu.release('h:ok')


# ============================================================ D. 降级：按项目路由（假宿主）
class D_PerProjectRoutingTests(unittest.TestCase):
    def setUp(self):
        _clear_hosts()
        db.set_host(FAKE_A, 'prjA')
        db.set_host(FAKE_B, 'prjB')

    def tearDown(self):
        _clear_hosts()
        with db._STATE_LOCK:
            db._CHILD_STATE.clear()
        gw._EMBED_STATE.clear()

    def test_fill_all_routes_by_project_host(self):
        with db._STATE_LOCK:
            db._CHILD_STATE.clear()
            db._CHILD_STATE[101] = {'host': FAKE_A, 'mode': 'fill'}
            db._CHILD_STATE[202] = {'host': FAKE_B, 'mode': 'fill'}
        seen = []

        def fake_fill(child, offset_y=None):
            seen.append(child)
            return {'ok': True, 'hwnd': child}

        with patch.object(db, 'fill_host', side_effect=fake_fill):
            db.fill_all(project_id='prjA')
            self.assertEqual(seen, [101])
            seen.clear()
            db.fill_all(project_id='prjB')
            self.assertEqual(seen, [202])
            seen.clear()
            db.fill_all()                 # project_id=None → 全部（改造前语义）
            self.assertEqual(sorted(seen), [101, 202])

    def test_engine_place_routes_to_own_child_only(self):
        with tempfile.TemporaryDirectory() as d:
            gw._EMBED_STATE.clear()
            gw._EMBED_STATE[os.path.abspath(d)] = {'child_hwnd': 101, 'host_hwnd': FAKE_A,
                                                   'offset_y': 0, 'mode': 'rect'}
            calls = []

            def fake_place(*args, **kwargs):
                calls.append(args)
                return {'ok': True, 'width': args[3], 'height': args[4]}

            with patch('desktop_bridge.place', side_effect=fake_place):
                gw.engine_place(d, 1, 2, 30, 40)
            self.assertTrue(calls)
            self.assertEqual(calls[0][0], 101)


# ============================================================ E. engine_start 落地软租约
@unittest.skipIf(gpu.GPU_MODE == 'parallel', 'parallel 模式不跑租约语义')
class E_EngineSoftLeaseTests(unittest.TestCase):
    def setUp(self):
        gpu.force_release(None)
        self.addCleanup(lambda: gpu.force_release(None))
        self.addCleanup(lambda: gpu.set_gpu_probe(None))

    def test_engine_start_requests_soft_lease(self):
        with tempfile.TemporaryDirectory() as d:
            proc = MagicMock()
            proc.pid = 4321
            proc.poll.return_value = None
            with patch.object(gw._gpu, 'acquire_lease', wraps=gpu.acquire_lease) as m, \
                 patch.object(gw, '_resolve_engine_executable', return_value='dummy'), \
                 patch.object(gw.subprocess, 'Popen', return_value=proc), \
                 patch('desktop_bridge.terminate_tree', return_value=[4321]):
                x = gw.engine_start(d)
                self.assertTrue(x.get('ok'), x)
                self.assertIs(m.call_args.kwargs.get('exclusive'), False)
                gw.engine_stop(d)
        self.assertEqual(gpu.status()['holders'], [])

    def test_engine_start_min_free_warns_but_starts(self):
        gpu.set_gpu_probe(lambda: [{'index': 0, 'name': 'FakeGPU', 'used_mb': 9000,
                                    'total_mb': 10000, 'utilization': 0.0,
                                    'temperature_c': None}])
        with tempfile.TemporaryDirectory() as d:
            proc = MagicMock()
            proc.pid = 555
            proc.poll.return_value = None
            with patch.dict(os.environ, {'DOCMIND_GPU_MIN_FREE_MB': '5000'}), \
                 patch.object(gw, '_resolve_engine_executable', return_value='dummy'), \
                 patch.object(gw.subprocess, 'Popen', return_value=proc), \
                 patch('desktop_bridge.terminate_tree', return_value=[555]):
                x = gw.engine_start(d)
                self.assertTrue(x.get('ok'), x)          # 显存不足也照常启动
                self.assertIn('notice', x)
                self.assertIn('显存不足', x['notice'])
                gw.engine_stop(d)


if __name__ == '__main__':
    unittest.main()
