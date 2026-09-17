"""P5：每项目宿主窗口（宿主分桶）+ 引擎 GPU 软租约（离线）。

不依赖 pywebview / 真引擎 / 真窗口：Win32 嵌入层全部打桩，只验证**路由与协调逻辑**。
降级验证（关键，替代真机多窗口）：用两个"假宿主 hwnd"（任意整数）+ 两个假嵌入子窗口，
断言 `fill_all(project_id=)` 与引擎端点按项目取到各自宿主。
"""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import config  # noqa: E402
import desktop_bridge as db  # noqa: E402
import game_workbench as gw  # noqa: E402
import gpu_coordinator as gpu  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

FAKE_A, FAKE_B = 0x000A11, 0x000B22  # 任意整数充当"普通 Windows 窗口"的 hwnd


def _clear_hosts():
    with db._HOST_LOCK:
        db._HOST_STATE.clear()
    api._DESKTOP_HOSTS.clear()


# --------------------------------------------------------------- 1. 宿主分桶
class HostBucketingTests(unittest.TestCase):
    def setUp(self):
        _clear_hosts()

    def tearDown(self):
        _clear_hosts()

    def test_per_project_roundtrip(self):
        db.set_host(FAKE_A, 'prjA')
        db.set_host(FAKE_B, 'prjB')
        self.assertEqual(db.host_hwnd('prjA'), FAKE_A)
        self.assertEqual(db.host_hwnd('prjB'), FAKE_B)
        self.assertEqual(db.hosts_snapshot(), {'prjA': FAKE_A, 'prjB': FAKE_B})

    def test_team_signature_project_first(self):
        # 团队约定顺序 set_host(project_id, hwnd) 也支持（首参为 str）
        db.set_host('prjC', 333)
        self.assertEqual(db.host_hwnd('prjC'), 333)
        db.set_host('prjC', None)   # 清除
        self.assertIsNone(db.host_hwnd('prjC'))

    def test_backward_compatible_single_arg(self):
        # 旧形式：单参 = 默认（无项目）宿主，行为与改造前一致
        self.assertIsNone(db.get_host())
        db.set_host(999)
        self.assertEqual(db.get_host(), 999)
        self.assertEqual(db.host_hwnd(), 999)
        db.set_host(None)
        self.assertIsNone(db.get_host())

    def test_unregistered_falls_back_to_default(self):
        db.set_host(555)                      # 默认宿主
        db.set_host('prjX', 777)              # 项目宿主
        self.assertEqual(db.host_hwnd('prjX'), 777)
        self.assertEqual(db.host_hwnd('nope'), 555)   # 未登记项目 → 回落默认
        self.assertEqual(db.host_hwnd(''), 555)       # 空 key → 默认


# --------------------------------------------------------------- 2. 端点路由
class HostEndpointTests(unittest.TestCase):
    def setUp(self):
        _clear_hosts()

    def tearDown(self):
        _clear_hosts()

    def test_post_get_per_project(self):
        client = TestClient(api.app)
        r1 = client.post('/api/desktop/host', json={'hwnd': FAKE_A, 'project_id': 'prjA'}).json()
        client.post('/api/desktop/host', json={'hwnd': FAKE_B, 'project_id': 'prjB'})
        self.assertTrue(r1['ok'])
        self.assertEqual(r1['host_hwnd'], FAKE_A)
        self.assertEqual(r1['project_id'], 'prjA')
        g1 = client.get('/api/desktop/host', params={'project_id': 'prjA'}).json()
        g2 = client.get('/api/desktop/host', params={'project_id': 'prjB'}).json()
        self.assertEqual(g1['host_hwnd'], FAKE_A)
        self.assertEqual(g2['host_hwnd'], FAKE_B)
        self.assertEqual(g1['hosts'], {'prjA': FAKE_A, 'prjB': FAKE_B})

    def test_backward_compatible_no_project_id(self):
        """不传 project_id 时行为与改造前一致（归全局默认键 + GET 取回同一宿主）。"""
        client = TestClient(api.app)
        with patch.object(api, '_request_project_id', return_value=''):
            client.post('/api/desktop/host', json={'hwnd': 999})
            g = client.get('/api/desktop/host').json()
        self.assertEqual(g['host_hwnd'], 999)
        self.assertIsNone(g['project_id'])


# --------------------------------------------- 2b. 回归：登记桶只认显式 project_id
class HostRegistrationBucketTests(unittest.TestCase):
    """P5 回归修复：`POST /api/desktop/host` 的桶选择**只认显式 project_id**，
    绝不用 `_request_project_id()` 兜底。否则旧桌面壳（无 body project_id）会把宿主
    绑到「登记当刻的当前项目」，用户切项目后读取路径落空 → 生命线被破坏。
    读取路径则**项目桶查不到时回落全局桶**，保证单窗口 + 切项目仍能取到宿主。"""

    def setUp(self):
        _clear_hosts()

    def tearDown(self):
        _clear_hosts()

    def test_old_shell_registers_global_not_request_project(self):
        """(a) 旧壳 POST {hwnd}（无 project_id）即便当前请求有项目，也必须落到全局桶；
        之后用**任意**项目上下文 GET 都能取回（回落到全局桶）。"""
        client = TestClient(api.app)
        with patch.object(api, '_request_project_id', return_value='prjP1'):
            # 桌面壳登记时恰逢「当前项目 = prjP1」，但 body 未带 project_id
            r = client.post('/api/desktop/host', json={'hwnd': 999}).json()
        self.assertEqual(r['host_hwnd'], 999)
        self.assertIsNone(r['project_id'])                    # 未绑到 prjP1
        self.assertEqual(api._DESKTOP_HOSTS, {None: 999})     # 只在全局桶
        self.assertNotIn('prjP1', api._DESKTOP_HOSTS)

        # 切到 prjP2 后：显式 ?project_id=prjP2 → 回落全局，仍取回 999
        g = client.get('/api/desktop/host', params={'project_id': 'prjP2'}).json()
        self.assertEqual(g['host_hwnd'], 999)
        self.assertEqual(g['source'], 'global')

        # 切到 prjP2 后：请求带 header（无 query）→ pid=None → 全局桶，同样取回 999
        with patch.object(api, '_request_project_id', return_value='prjP2'):
            g2 = client.get('/api/desktop/host').json()
        self.assertEqual(g2['host_hwnd'], 999)

    def test_explicit_project_bucket_prioritized_over_global(self):
        """(b) 显式 {hwnd, project_id:'prjA'} → 登记到 prjA 桶；prjA 查询优先取项目桶，
        未登记的 prjB 查询回落全局桶。"""
        client = TestClient(api.app)
        client.post('/api/desktop/host', json={'hwnd': 1000})              # 全局宿主
        r = client.post('/api/desktop/host', json={'hwnd': FAKE_A,
                                                   'project_id': 'prjA'}).json()
        self.assertEqual(r['project_id'], 'prjA')
        self.assertEqual(api._DESKTOP_HOSTS, {'prjA': FAKE_A, None: 1000})

        ga = client.get('/api/desktop/host', params={'project_id': 'prjA'}).json()
        self.assertEqual(ga['host_hwnd'], FAKE_A)             # 项目桶优先
        self.assertEqual(ga['source'], 'project')

        gb = client.get('/api/desktop/host', params={'project_id': 'prjB'}).json()
        self.assertEqual(gb['host_hwnd'], 1000)               # 未登记 → 回落全局
        self.assertEqual(gb['source'], 'global')

        gn = client.get('/api/desktop/host').json()
        self.assertEqual(gn['host_hwnd'], 1000)               # 无项目 → 全局
        self.assertEqual(gn['project_id'], None)
        self.assertEqual(gn['source'], 'global')

    def test_source_none_when_nothing_registered(self):
        client = TestClient(api.app)
        g = client.get('/api/desktop/host', params={'project_id': 'prjX'}).json()
        self.assertIsNone(g['host_hwnd'])
        self.assertEqual(g['source'], 'none')

    def test_bridge_host_hwnd_no_arg_matches_old_behavior(self):
        """(c) `desktop_bridge.host_hwnd()`（无参）与改造前一致：返回全局宿主；
        未知项目也回落全局。"""
        self.assertIsNone(db.host_hwnd())
        db.set_host(888)                 # 旧壳单参登记 == 全局宿主
        self.assertEqual(db.host_hwnd(), 888)
        self.assertEqual(db.host_hwnd('whatever'), 888)   # 未知项目 → 回落全局
        db.set_host(None)
        self.assertIsNone(db.host_hwnd())


# --------------------------------------------------------------- 3. 引擎端点按项目取宿主
class EngineHostRoutingTests(unittest.TestCase):
    def setUp(self):
        _clear_hosts()
        db.set_host(FAKE_A, 'prjA')
        db.set_host(FAKE_B, 'prjB')
        api._DESKTOP_HOSTS['prjA'] = FAKE_A
        api._DESKTOP_HOSTS['prjB'] = FAKE_B

    def tearDown(self):
        _clear_hosts()

    def test_engine_embed_uses_request_project_host(self):
        with tempfile.TemporaryDirectory() as d:
            prev = config.get_runtime('code_root')
            config.set_runtime('code_root', d)
            captured = {}
            try:
                def fake_embed(*args, **kwargs):
                    captured['host'] = args[1] if len(args) > 1 else kwargs.get('host_hwnd')
                    return {'ok': False, 'error': '引擎尚未运行。'}
                with patch.object(api, 'engine_embed', side_effect=fake_embed), \
                     patch.object(api, '_request_project_id', return_value='prjA'):
                    client = TestClient(api.app)
                    resp = client.post('/api/engine/embed', json={'host_hwnd': 0})
                self.assertNotEqual(resp.status_code, 500, resp.text)
                self.assertEqual(captured['host'], FAKE_A)
            finally:
                config.set_runtime('code_root', prev or '')

    def test_engine_start_uses_request_project_host(self):
        with tempfile.TemporaryDirectory() as d:
            prev = config.get_runtime('code_root')
            config.set_runtime('code_root', d)
            captured = {}
            try:
                def fake_start(*args, **kwargs):
                    captured['host'] = args[3]
                    return {'ok': True, 'running': True}

                async def fake_stop(root):
                    return ([], [])
                with patch.object(api, 'engine_start', side_effect=fake_start), \
                     patch.object(api, '_stop_other_engines', side_effect=fake_stop), \
                     patch.object(api, '_request_project_id', return_value='prjB'):
                    client = TestClient(api.app)
                    resp = client.post('/api/engine/start', json={'host_hwnd': 0})
                self.assertNotEqual(resp.status_code, 500, resp.text)
                self.assertEqual(captured['host'], FAKE_B)
            finally:
                config.set_runtime('code_root', prev or '')


# --------------------------------------------------------------- 4. 降级验证：按项目路由（假宿主）
class PerProjectRoutingDegradeTests(unittest.TestCase):
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
            db.fill_all()   # project_id=None → 全部（与改造前一致）
            self.assertEqual(sorted(seen), [101, 202])

    def test_engine_place_routes_to_its_own_child(self):
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
            self.assertEqual(calls[0][0], 101)   # 只动自己那棵（属于 prjA 宿主）的引擎窗口


# --------------------------------------------------------------- 5. GPU 软租约
@unittest.skipIf(gpu.GPU_MODE == 'parallel', 'parallel 模式不跑租约语义')
class SoftLeaseTests(unittest.TestCase):
    def setUp(self):
        gpu.force_release(None)
        gpu.set_gpu_probe(None)
        self.addCleanup(lambda: gpu.force_release(None))
        self.addCleanup(lambda: gpu.set_gpu_probe(None))

    def _owners(self):
        return [h['owner'] for h in gpu.status()['holders']]

    def test_two_soft_leases_both_succeed_and_visible(self):
        a = gpu.acquire_lease('soft:a', exclusive=False)
        b = gpu.acquire_lease('soft:b', exclusive=False)
        self.assertTrue(a['ok'] and b['ok'])
        self.assertTrue(a.get('soft') and a.get('exclusive') is False)
        # 软租约在 gpu_status 里可见（登记了）
        owners = self._owners()
        self.assertIn('soft:a', owners)
        self.assertIn('soft:b', owners)
        soft = [h for h in gpu.status()['holders'] if h.get('soft')]
        self.assertEqual(len(soft), 2)
        gpu.release('soft:a')
        gpu.release('soft:b')
        self.assertEqual(self._owners(), [])

    def test_soft_does_not_block_hard(self):
        gpu.acquire_lease('soft:a', exclusive=False)
        h = gpu.acquire_lease('hard:h1', timeout=0)     # 硬租约不被软租约阻挡
        self.assertTrue(h['ok'], h)
        hards = [x for x in gpu.status()['holders'] if not x.get('soft')]
        self.assertTrue(hards and hards[0]['exclusive'])
        gpu.release('soft:a')
        gpu.release('hard:h1')
        self.assertEqual(self._owners(), [])

    def test_hard_blocks_hard(self):
        h1 = gpu.acquire_lease('hard:h1', timeout=0)
        self.assertTrue(h1['ok'])
        h2 = gpu.acquire_lease('hard:h2', timeout=0)
        self.assertFalse(h2['ok'])
        self.assertEqual(h2['reason'], 'busy')
        gpu.release('hard:h1')
        self.assertEqual(self._owners(), [])

    def test_soft_lease_ttl_reclaimed(self):
        gpu.acquire_lease('soft:t', ttl=0.001, exclusive=False)
        time.sleep(0.02)
        gpu.status()   # status() 会惰性回收到期租约
        self.assertEqual(self._owners(), [])

    def test_soft_min_free_is_warning_not_reject(self):
        # 假单卡：总 10000MB、已用 9000MB → 余 1000MB，门槛 5000MB
        gpu.set_gpu_probe(lambda: [{'index': 0, 'name': 'FakeGPU', 'used_mb': 9000,
                                    'total_mb': 10000, 'utilization': 0.0,
                                    'temperature_c': None}])
        r = gpu.acquire_lease('soft:low', min_free_mb=5000, exclusive=False)
        self.assertTrue(r['ok'], r)              # 软租约不因显存不足被拒
        self.assertIn('warning', r)
        self.assertIn('显存不足', r['warning'])
        gpu.release('soft:low')


# --------------------------------------------------------------- 6. 引擎软租约落地
@unittest.skipIf(gpu.GPU_MODE == 'parallel', 'parallel 模式不跑租约语义')
class EngineSoftLeaseTests(unittest.TestCase):
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
                self.assertIs(m.call_args.kwargs.get('exclusive'), False,
                              'engine_start 必须以软租约启动：%s' % (m.call_args,))
                gw.engine_stop(d)
        self.assertEqual(gpu.status()['holders'], [])

    def test_engine_start_min_free_warns_not_rejects(self):
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
                self.assertTrue(x.get('ok'), x)             # 不足也照常启动
                self.assertIn('notice', x)
                self.assertIn('显存不足', x['notice'])
                gw.engine_stop(d)


if __name__ == '__main__':
    unittest.main()
