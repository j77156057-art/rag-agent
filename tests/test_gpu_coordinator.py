"""P2-1 GPU 协调器测试：多卡租约/FIFO/取消/驱逐钩子/空闲卸载/采样环/ComfyUI 生命周期。

协调器是进程级单例（模块全局状态），每个用例都彻底复位，避免与 test_gpu_queue
等用例串状态；GPU_MODE 是 import 期常量，用 monkeypatch 模块全局的方式切模式。
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gpu_coordinator as g  # noqa: E402
import game_workbench as gw  # noqa: E402
import api  # noqa: E402


def _fake_gpus(used0=10000, used1=1000, total=12000):
    """构造双卡探测行（可被测试动态改 used_*）。"""
    return [
        {"index": 0, "name": "GPU-A", "used_mb": used0, "total_mb": total,
         "utilization": 5.0, "temperature_c": 40.0},
        {"index": 1, "name": "GPU-B", "used_mb": used1, "total_mb": total,
         "utilization": 10.0, "temperature_c": 42.0},
    ]


class GpuCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self._orig_mode = g.GPU_MODE
        g.GPU_MODE = "serial"
        g._leases.clear()
        g._waiters.clear()
        g._activities.clear()
        g._samples.clear()
        g._hooks.clear()
        g._last_idle_unload = 0.0
        g.set_gpu_probe(None)
        g.configure(idle_unload_seconds=g.IDLE_UNLOAD_DEFAULT,
                    poll_interval=g.POLL_INTERVAL)

    def tearDown(self):
        # 回收租约、唤醒并清掉残留排队，再恢复全局
        try:
            g.force_release()
            for tok in list(g._waiters):
                tok["canceled"] = True
                tok["event"].set()
        finally:
            g.stop_background()
            g.set_gpu_probe(None)
            g.GPU_MODE = self._orig_mode
            g.configure(idle_unload_seconds=g.IDLE_UNLOAD_DEFAULT,
                        poll_interval=g.POLL_INTERVAL)
            g._hooks.clear()

    # ---- 探测/聚合 -------------------------------------------------------

    def test_probe_and_memory_aggregation(self):
        rows = _fake_gpus()
        g.set_gpu_probe(lambda: rows)
        self.assertEqual(len(g.query_gpus()), 2)
        mi = g.memory_info()
        self.assertEqual(mi["total_mb"], 24000)
        self.assertEqual(mi["used_mb"], 11000)
        self.assertEqual(mi["free_mb"], 13000)
        self.assertEqual([x["index"] for x in mi["gpus"]], [0, 1])

    def test_probe_failure_degrades_gracefully(self):
        g.set_gpu_probe(lambda: None)
        self.assertIsNone(g.memory_info())
        # 无探测时 serial 仍可工作（退化为单卡视图）
        self.assertTrue(g.acquire_lease("a", 0.1)["ok"])

    def test_real_probe_ttl_cache(self):
        """真实探测路径（非注入）在 TTL 内只调一次 nvidia-smi 替代函数。"""
        g.set_gpu_probe(None)  # 回到"真实探测"路径
        orig_default = g._default_probe
        orig_ttl = g.PROBE_CACHE_TTL
        calls = {"n": 0}
        try:
            g.PROBE_CACHE_TTL = 0.3

            def fake_default():
                calls["n"] += 1
                return _fake_gpus(used0=1000)

            g._default_probe = fake_default
            g._probe_cache.update(t=0.0, rows=None)
            g.query_gpus(); g.query_gpus(); g.memory_info()
            self.assertEqual(calls["n"], 1)  # TTL 内全部复用
            time.sleep(0.35)
            g.query_gpus()
            self.assertEqual(calls["n"], 2)  # 过期后重探
        finally:
            g._default_probe = orig_default
            g.PROBE_CACHE_TTL = orig_ttl
            g._probe_cache.update(t=0.0, rows=None)

    def test_injected_probe_never_cached(self):
        state = {"used": 1000}
        g.set_gpu_probe(lambda: _fake_gpus(used0=state["used"]))
        self.assertEqual(g.memory_info()["used_mb"], 2000)   # 卡0 1000 + 卡1 1000
        state["used"] = 9000  # 立即生效，无 TTL 延迟
        self.assertEqual(g.memory_info()["used_mb"], 10000)  # 卡0 9000 + 卡1 1000

    # ---- multi 模式 ------------------------------------------------------

    def test_multi_auto_picks_freest_gpu(self):
        g.GPU_MODE = "multi"
        state = {"rows": _fake_gpus(used0=10000, used1=1000)}
        g.set_gpu_probe(lambda: state["rows"])
        r1 = g.acquire_lease("a", 0.1)
        r2 = g.acquire_lease("b", 0.1)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(r1["gpu"], 1)  # 更空的卡 1
        self.assertEqual(r2["gpu"], 0)
        r3 = g.acquire_lease("c", timeout=0)
        self.assertFalse(r3["ok"])
        self.assertEqual(r3["reason"], "busy")

    def test_multi_requested_gpu(self):
        g.GPU_MODE = "multi"
        g.set_gpu_probe(lambda: _fake_gpus())
        self.assertEqual(g.acquire_lease("a", 0.1, gpu=1)["gpu"], 1)
        clash = g.acquire_lease("b", 0.0, gpu=1)
        self.assertFalse(clash["ok"])
        self.assertEqual(clash["gpu"], 1)
        self.assertEqual(g.acquire_lease("c", 0.1, gpu=0)["gpu"], 0)

    def test_fifo_head_blocks_later_waiter(self):
        """严格 FIFO：队首 w1 指定的卡 0 忙时，后面的 w2(任意卡) 不得拿卡 1。"""
        g.GPU_MODE = "multi"
        g.set_gpu_probe(lambda: _fake_gpus(used0=2000, used1=2000))
        self.assertEqual(g.acquire_lease("holder", 0.1, gpu=0)["gpu"], 0)
        got = {}

        def wait(name, **kw):
            got[name] = g.acquire_lease(name, 3.0, **kw)

        t1 = threading.Thread(target=wait, args=("w1",), kwargs={"gpu": 0})
        t1.start()
        deadline = time.time() + 2
        while g.status()["queue_length"] < 1 and time.time() < deadline:
            time.sleep(0.005)
        t2 = threading.Thread(target=wait, args=("w2",))
        t2.start()
        time.sleep(0.2)
        self.assertEqual([q["owner"] for q in g.status()["queue"]], ["w1", "w2"])
        g.release("holder")
        t1.join(2); t2.join(2)
        self.assertEqual(got["w1"]["gpu"], 0)
        self.assertEqual(got["w2"]["gpu"], 1)

    def test_min_free_denied_without_queue(self):
        g.GPU_MODE = "multi"
        # 卡 1 只剩 500MB
        g.set_gpu_probe(lambda: _fake_gpus(used0=11500, used1=11500))
        r = g.acquire_lease("b", 0.1, min_free_mb=5000)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "insufficient_memory")
        self.assertEqual(g.status()["queue_length"], 0)
        self.assertEqual(len(g.status()["holders"]), 0)

    # ---- 驱逐钩子 --------------------------------------------------------

    def test_evict_hook_fires_once_then_grants(self):
        """显存门槛拒绝时：钩子（卸载 Ollama）跑一次，显存腾出来后重试成功。"""
        g.GPU_MODE = "multi"
        state = {"rows": _fake_gpus(used0=1000, used1=11500)}  # 卡0 空，卡1 仅剩 500
        g.set_gpu_probe(lambda: state["rows"])
        self.assertEqual(g.acquire_lease("other-job", 0.1, gpu=0)["gpu"], 0)
        calls = []

        def hook():
            calls.append(1)
            state["rows"] = _fake_gpus(used0=1000, used1=1000)  # 卡1 腾出 11GB
            return True

        g.register_hook("ollama", hook)
        r = g.acquire_lease("b", 0.5, min_free_mb=5000, evict=("ollama",))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["gpu"], 1)
        self.assertTrue(r["evicted"])
        self.assertEqual(len(calls), 1)  # 只重试一次，钩子不重复调

    def test_evict_hook_not_called_when_free(self):
        g.set_gpu_probe(lambda: _fake_gpus(used0=1000, used1=1000))
        calls = []
        g.register_hook("ollama", lambda: calls.append(1) or True)
        r = g.acquire_lease("a", 0.1, evict=("ollama",))
        self.assertTrue(r["ok"])
        self.assertFalse(r.get("evicted"))
        self.assertEqual(calls, [])

    def test_evict_hook_not_called_when_active_holder(self):
        """有任务正持租约（卡忙）时排队者不得触发卸载：卸载不释放租约，
        只会让用户白付一次模型冷加载。"""
        g.set_gpu_probe(lambda: _fake_gpus(used0=1000))  # 显存本身充足
        self.assertTrue(g.acquire_lease("export-job", 0.1)["ok"])
        calls = []
        g.register_hook("ollama", lambda: calls.append(1) or True)
        r = g.acquire_lease("b", 0.3, min_free_mb=5000, evict=("ollama",))
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "timeout")
        self.assertEqual(calls, [])
        g.force_release("export-job")

    def test_reentrant_flag_matches_real_state(self):
        g.set_gpu_probe(lambda: _fake_gpus(used0=1000))
        r1 = g.acquire_lease("a", 0.1)
        self.assertTrue(r1["ok"])
        self.assertFalse(r1["reentrant"])
        r2 = g.acquire_lease("a", 0.1)  # 同 owner 再来一次=重入
        self.assertTrue(r2["ok"])
        self.assertTrue(r2["reentrant"])
        r3 = g.acquire_lease("a", 0.1, gpu=None)
        self.assertTrue(r3["reentrant"])

    # ---- 取消/改名/定向回收 ----------------------------------------------

    def test_cancel_wait(self):
        self.assertTrue(g.acquire("holder", 0.1))
        result = {}

        def waiter():
            result["r"] = g.acquire_lease("b", 5.0)

        t = threading.Thread(target=waiter)
        t.start()
        deadline = time.time() + 2
        while g.status()["queue_length"] < 1 and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(g.cancel_wait("b"), 1)
        t.join(2)
        self.assertFalse(result["r"]["ok"])
        self.assertEqual(result["r"]["reason"], "canceled")
        self.assertEqual(g.status()["queue_length"], 0)
        self.assertIsNotNone(g.status()["active"])  # 持有者不受影响

    def test_cancel_wait_unknown_owner(self):
        self.assertEqual(g.cancel_wait("nobody"), 0)

    def test_reown_and_release_by_job_owner(self):
        r = g.acquire_lease("comfyui:submit:abc", 0.1, ttl=600)
        self.assertTrue(r["ok"])
        self.assertTrue(g.reown("comfyui:submit:abc", "comfyui:pid-1",
                                purpose="comfyui", ttl=600))
        st = g.status()
        self.assertEqual(st["active"], "comfyui:pid-1")
        g.release("comfyui:submit:abc")  # 旧名释放是 no-op
        self.assertEqual(st["active"], g.status()["active"])
        self.assertEqual(g.force_release("comfyui:pid-1"), "comfyui:pid-1")
        self.assertIsNone(g.status()["active"])

    def test_reown_missing_lease(self):
        self.assertFalse(g.reown("ghost", "new"))

    def test_force_release_named_owner_only(self):
        g.GPU_MODE = "multi"
        g.set_gpu_probe(lambda: _fake_gpus(used0=2000, used1=2000))
        g.acquire_lease("a", 0.1, gpu=0)
        g.acquire_lease("b", 0.1, gpu=1)
        self.assertEqual(g.force_release("a"), "a")
        holders = {h["owner"] for h in g.status()["holders"]}
        self.assertEqual(holders, {"b"})
        self.assertIsNone(g.force_release("ghost"))

    def test_queue_position(self):
        self.assertTrue(g.acquire("holder", 0.1))
        t = threading.Thread(target=lambda: g.acquire_lease("w", 5.0))
        t.start()
        deadline = time.time() + 2
        while g.queue_position("w") != 1 and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(g.queue_position("w"), 1)
        g.cancel_wait("w")
        t.join(2)

    # ---- 空闲卸载 --------------------------------------------------------

    def test_idle_unload_rules(self):
        calls = []
        g.register_hook("ollama", lambda: calls.append(1) or True)
        g.configure(idle_unload_seconds=10)
        now = 1000.0
        # 没活动过：不卸
        self.assertFalse(g._maybe_idle_unload(now))
        g.note_activity("ollama")  # 真实时间打点，下面用相对偏移
        base = time.time()
        self.assertFalse(g._maybe_idle_unload(base + 5))   # 还没空闲够
        # 有别的租约持有时不卸载
        self.assertTrue(g.acquire_lease("comfyui:pid", 0.1)["ok"])
        self.assertFalse(g._maybe_idle_unload(base + 11))
        g.force_release("comfyui:pid")
        self.assertTrue(g._maybe_idle_unload(base + 11))   # 空闲够且无其他租约
        self.assertEqual(len(calls), 1)
        self.assertFalse(g._maybe_idle_unload(base + 12))  # 60s 冷却内
        self.assertEqual(len(calls), 1)

    def test_idle_unload_disabled(self):
        calls = []
        g.register_hook("ollama", lambda: calls.append(1) or True)
        g.configure(idle_unload_seconds=0)
        g.note_activity("ollama")
        self.assertFalse(g._maybe_idle_unload(time.time() + 3600))
        self.assertEqual(calls, [])

    # ---- 采样环 + 后台线程 -----------------------------------------------

    def test_sample_ring_and_background(self):
        g.set_gpu_probe(lambda: _fake_gpus())
        g._sample_once()
        g._sample_once()
        samples = g.recent_samples()
        self.assertEqual(len(samples), 2)
        self.assertEqual(len(samples[-1]["gpus"]), 2)
        g.configure(poll_interval=1)
        g.start_background()
        time.sleep(1.3)
        g.stop_background()
        self.assertGreaterEqual(len(g.recent_samples()), 3)

    def test_samples_and_leases_persist_to_separate_files(self):
        """回归 B3：后台采样不得冲掉租约快照。

        采样写 SAMPLES_FILE、租约写 STATE_FILE；采样仍可从 STATE_FILE 恢复租约。
        """
        with tempfile.TemporaryDirectory() as d:
            old_state, old_samples = g.STATE_FILE, g.SAMPLES_FILE
            g.STATE_FILE = Path(d) / "gpu_state.json"
            g.SAMPLES_FILE = Path(d) / "gpu_samples.json"
            try:
                g.set_gpu_probe(lambda: _fake_gpus())
                g.acquire_lease("worker", timeout=1.0, purpose="train")  # 触发 _persist_runtime_locked
                g._sample_once()  # 采样，绝不能覆盖租约快照
                state = json.loads(g.STATE_FILE.read_text(encoding="utf-8"))
                samples = json.loads(g.SAMPLES_FILE.read_text(encoding="utf-8"))
                # STATE_FILE 仍是租约/队列 schema
                self.assertIn("leases", state)
                self.assertIn("queue", state)
                self.assertTrue(state["leases"], "租约快照必须保留")
                self.assertNotIn("samples", state)
                # SAMPLES_FILE 是采样 schema
                self.assertIn("samples", samples)
                self.assertNotIn("leases", samples)
                # 采样后仍能从 STATE_FILE 产出 recovered 事件
                g._recovery_events.clear()
                recovered = g.restore_runtime_state()
                self.assertGreaterEqual(recovered, 1, "STATE_FILE 未被采样冲掉，应能恢复租约")
                self.assertTrue(any(ev.get("status") == "recovered" for ev in g._recovery_events))
            finally:
                g.STATE_FILE, g.SAMPLES_FILE = old_state, old_samples

    # ---- parallel 模式 ---------------------------------------------------

    def test_parallel_mode_bypasses_coordination(self):
        g.GPU_MODE = "parallel"
        r1 = g.acquire_lease("a", 0.1)
        r2 = g.acquire_lease("b", 0.1)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertIsNone(r1["gpu"])
        self.assertFalse(g.status()["coordinating"])
        self.assertIsNone(g.force_release())
        g.release("a")  # no-op，不报错

    # ---- status 结构 -----------------------------------------------------

    def test_status_structure(self):
        g.set_gpu_probe(lambda: _fake_gpus(used0=9000, used1=3000))
        self.assertTrue(g.acquire_lease("ollama", 0.1, purpose="chat")["ok"])
        st = g.status()
        for key in ("mode", "coordinating", "holders", "queue", "queue_length",
                    "memory", "gpus", "samples", "ollama_idle", "hooks",
                    "active", "purpose"):
            self.assertIn(key, st)
        self.assertEqual(len(st["gpus"]), 2)
        g0 = st["gpus"][0]
        self.assertEqual(g0["free_mb"], 3000)
        self.assertEqual(g0["holder"]["owner"], "ollama")
        self.assertIsNone(st["gpus"][1]["holder"])
        # 旧键仍在
        for old_key in ("active", "purpose", "held_for_seconds",
                        "queue_length", "memory", "ollama_keep_alive"):
            self.assertIn(old_key, st)


# ---------------------------------------------------------------------------
# ComfyUI 作业租约生命周期：用本地假 ComfyUI 服务跑 queue → history → 释放/取消
# ---------------------------------------------------------------------------

class _ComfyState:
    def __init__(self):
        self.phase = "running"   # running|finished
        self.interrupts = 0
        self.deletes = 0
        self.counter = 0


def _make_comfy_server(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if self.path == "/prompt":
                state.counter += 1
                self._json(200, {"prompt_id": f"pid-{state.counter}", "number": state.counter})
            elif self.path == "/interrupt":
                state.interrupts += 1
                self._json(200, {})
            elif self.path == "/queue":
                state.deletes += 1
                self._json(200, {"deleted": ["pid-1"]})
            else:
                self._json(404, {"error": "not found"})

        def do_GET(self):
            if self.path.startswith("/history/"):
                if state.phase == "finished":
                    self._json(200, {"pid-1": {"outputs": {"9": {"images": [
                        {"filename": "a.png", "subfolder": "", "type": "output"}]}},
                        "status": {"completed": True, "status_str": "success"}}})
                else:
                    self._json(200, {})
            else:
                self._json(404, {"error": "not found"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ComfyLeaseLifecycleTest(unittest.TestCase):
    def setUp(self):
        g.GPU_MODE = "serial"
        g._leases.clear()
        g._waiters.clear()
        g._activities.clear()
        g._hooks.clear()
        g._last_idle_unload = 0.0
        # 注入"显存充足"的假探测：本机真实余量常 < COMFY_MIN_FREE_MB(1024)，
        # 生命周期测试只关心租约流转，不应被真实显存门槛干扰
        g.set_gpu_probe(lambda: _fake_gpus(used0=1000, used1=1000))
        self.state = _ComfyState()
        self.srv = _make_comfy_server(self.state)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        g.force_release()
        g.set_gpu_probe(None)

    def test_queue_hold_history_release(self):
        r = gw.comfy_queue({"3": {"class_type": "KSampler"}}, self.url)
        self.assertTrue(r["ok"], r)
        owner = r["lease"]["owner"]
        self.assertEqual(owner, "comfyui:pid-1")
        self.assertEqual(g.status()["active"], owner)
        # 生成中：不释放
        h = gw.comfy_history("pid-1", self.url)
        self.assertTrue(h["ok"])
        self.assertFalse(h["finished"])
        self.assertEqual(g.status()["active"], owner)
        # 完成：history 幂等释放作业租约
        self.state.phase = "finished"
        h = gw.comfy_history("pid-1", self.url)
        self.assertTrue(h["finished"])
        self.assertTrue(h["lease_released"])
        self.assertIsNone(g.status()["active"])
        # 再轮询一次：释放幂等，不报错
        h2 = gw.comfy_history("pid-1", self.url)
        self.assertFalse(h2.get("lease_released"))

    def test_cancel_requests_queue_delete_then_releases_on_terminal_history(self):
        r = gw.comfy_queue({}, self.url)
        self.assertTrue(r["ok"])
        owner = r["lease"]["owner"]
        c = gw.comfy_cancel("pid-1", self.url)
        self.assertTrue(c["ok"])
        self.assertTrue(c["deleted"])
        self.assertFalse(c["lease_released"])
        self.assertEqual(c["cancel_state"], "requested")
        self.assertEqual(self.state.deletes, 1)
        self.assertEqual(g.status()["active"], owner)
        self.state.phase = "finished"
        h = gw.comfy_history("pid-1", self.url)
        self.assertTrue(h["finished"])
        self.assertTrue(h["lease_released"])
        self.assertIsNone(g.status()["active"])
        self.assertEqual(owner, "comfyui:pid-1")

    def test_queue_rejected_when_gpu_busy(self):
        self.assertTrue(g.acquire("ollama", 0.1))
        r = gw.comfy_queue({}, self.url)
        self.assertFalse(r["ok"])
        self.assertIn("GPU", r["error"])
        self.assertEqual(g.status()["active"], "ollama")

    def test_queue_failure_releases_submit_lease(self):
        # 指向一个立即拒绝连接的端口，提交失败必须释放提交租约
        r = gw.comfy_queue({}, "http://127.0.0.1:1")
        self.assertFalse(r["ok"])
        self.assertIsNone(g.status()["active"])

    def test_queue_rejected_low_vram_without_evict_hook(self):
        """显存余量低于 COMFY_MIN_FREE_MB 且没有驱逐钩子：直接拒绝，不排队。"""
        g.set_gpu_probe(lambda: _fake_gpus(used0=11500))  # 仅剩 500MB
        r = gw.comfy_queue({}, self.url)
        self.assertFalse(r["ok"])
        self.assertIn("显存不足", r["error"])
        self.assertEqual(g.status()["queue_length"], 0)
        self.assertIsNone(g.status()["active"])

    def test_queue_evicts_ollama_then_grants(self):
        """余量不足但驱逐钩子能腾出显存：卸载一次后提交成功。"""
        state = {"rows": _fake_gpus(used0=11500)}  # 起手只剩 500MB
        g.set_gpu_probe(lambda: state["rows"])
        g.register_hook("ollama", lambda: state.__setitem__(
            "rows", _fake_gpus(used0=1000)) or True)
        r = gw.comfy_queue({}, self.url)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["lease"]["owner"], "comfyui:pid-1")
        self.assertTrue(r["lease"]["evicted"])


class OllamaEvictHookRetryTest(unittest.TestCase):
    """api._gpu_ollama_evict_hook 两轮卸载：模拟多模型同驻时嵌入模型首次被吞。"""

    def setUp(self):
        self.resident = ["qwen3.6:35b-a3b", "bge-m3:latest"]
        self.calls = []
        self._orig_ps = api._ollama_ps
        self._orig_ka = api._ollama_keep_alive
        self._orig_sleep = api.time.sleep
        api._ollama_ps = lambda timeout=8.0: [{"name": n} for n in self.resident]
        api.time.sleep = lambda _s: None

        def fake_keep_alive(model, keep_alive, timeout=600.0):
            nth = sum(1 for c in self.calls if c[0] == model) + 1
            self.calls.append((model, keep_alive))
            # 复刻真机现象：bge 第一次 keep_alive=0 返回成功但仍驻留（大模型卸载竞争）
            if model == "bge-m3:latest" and nth == 1:
                return True, ""
            if model in self.resident:
                self.resident.remove(model)
            return True, ""

        api._ollama_keep_alive = fake_keep_alive

    def tearDown(self):
        api._ollama_ps = self._orig_ps
        api._ollama_keep_alive = self._orig_ka
        api.time.sleep = self._orig_sleep

    def test_second_round_unloads_survivor(self):
        self.assertTrue(api._gpu_ollama_evict_hook())
        self.assertEqual(self.resident, [])
        bge = [c for c in self.calls if c[0] == "bge-m3:latest"]
        self.assertEqual(len(bge), 2)  # 首次被吞，复查后补一次
        qwen = [c for c in self.calls if c[0] == "qwen3.6:35b-a3b"]
        self.assertEqual(len(qwen), 1)  # 一次成功不重复

    def test_no_models_means_no_calls(self):
        self.resident.clear()
        self.assertFalse(api._gpu_ollama_evict_hook())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
