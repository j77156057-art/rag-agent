# -*- coding: utf-8 -*-
"""LLM 弹性层：重试/退避/deadline/可重试判定（离线，不触网）。"""
import time
import unittest

from llm import retry_call, is_retryable


class _Err(Exception):
    def __init__(self, code=None, msg=""):
        super().__init__(msg or f"http {code}")
        if code is not None:
            self.status_code = code


class RetryableClassificationTests(unittest.TestCase):
    def test_rate_limit_and_5xx_are_retryable(self):
        self.assertTrue(is_retryable(_Err(429, "too many requests")))
        self.assertTrue(is_retryable(_Err(500, "internal error")))
        self.assertTrue(is_retryable(_Err(503, "server error")))
        self.assertTrue(is_retryable(TimeoutError("timed out")))
        self.assertTrue(is_retryable(_Err(None, "connection reset by peer")))

    def test_client_errors_are_not_retryable(self):
        self.assertFalse(is_retryable(_Err(401, "unauthorized")))
        self.assertFalse(is_retryable(_Err(403, "forbidden")))
        self.assertFalse(is_retryable(_Err(400, "bad request")))


class RetryCallTests(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _Err(503, "server error")
            return "ok"

        self.assertEqual(retry_call(fn, attempts=5, base=0.0), "ok")
        self.assertEqual(calls["n"], 3)

    def test_non_retryable_raises_immediately(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _Err(401, "unauthorized")

        with self.assertRaises(_Err):
            retry_call(fn, attempts=5, base=0.0)
        self.assertEqual(calls["n"], 1, "客户端错误不应触发重试")

    def test_exhausted_retries_raise_last_error(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _Err(500, "internal error")

        with self.assertRaises(_Err):
            retry_call(fn, attempts=3, base=0.0)
        self.assertEqual(calls["n"], 3)

    def test_deadline_stops_retrying(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _Err(503, "server error")

        with self.assertRaises(Exception):
            retry_call(fn, deadline=time.monotonic() + 0.05, attempts=1000, base=0.05)
        self.assertLess(calls["n"], 1000, "到达 deadline 后必须停止重试")

    def test_past_deadline_fails_fast(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return "never"

        with self.assertRaises(TimeoutError):
            retry_call(fn, deadline=time.monotonic() - 1, attempts=3, base=0.0)
        self.assertEqual(calls["n"], 0, "已过期的 deadline 不应发起任何调用")


class ThinkingResolutionTests(unittest.TestCase):
    """_resolve_thinking：native 恒开 / toggle 跟随开关 / none 恒关。"""

    def _client(self, provider, model):
        from llm import LLMClient
        return LLMClient(provider=provider, model=model)

    def test_native_model_forced_on_even_when_switched_off(self):
        c = self._client("deepseek", "deepseek-reasoner")
        self.assertTrue(c._resolve_thinking(False))
        self.assertTrue(c._resolve_thinking(None))

    def test_toggle_family_follows_flag(self):
        c = self._client("ollama", "qwen3:8b")
        self.assertTrue(c._resolve_thinking(True))
        self.assertFalse(c._resolve_thinking(False))

    def test_plain_model_never_thinks(self):
        c = self._client("ollama", "qwen2.5:7b")
        self.assertFalse(c._resolve_thinking(True))
        self.assertFalse(c._resolve_thinking(None))

    def test_runtime_toggle_used_when_flag_omitted(self):
        from config import set_runtime
        c = self._client("qwen", "qwen3-235b-a22b")
        try:
            set_runtime("llm_enable_thinking", "1")
            self.assertTrue(c._resolve_thinking(None))
            set_runtime("llm_enable_thinking", "0")
            self.assertFalse(c._resolve_thinking(None))
            # 显式参数优先于运行时开关
            self.assertTrue(c._resolve_thinking(True))
        finally:
            set_runtime("llm_enable_thinking", "")


class OllamaPayloadTests(unittest.TestCase):
    """ollama 原生 /api/chat：think 字段恒携带、num_ctx 按画像封顶、输出预算给满。"""

    def _capture(self, model, thinking_on):
        import json as _json
        from unittest.mock import patch
        from llm import LLMClient

        # 离线：窗口实时探测打桩为 None，回退内置画像，保证 num_ctx 断言确定
        with patch("llm.probe_ollama_context", return_value=None):
            client = LLMClient(provider="ollama", model=model)
        captured = {}

        class _Resp:
            def read(self):
                return b'{"message":{"content":"ok"},"prompt_eval_count":7,"eval_count":3}'

        class _Opener:
            def open(self, req, timeout=None):
                captured["payload"] = _json.loads(req.data.decode("utf-8"))
                return _Resp()

        with patch("llm._gpu_acquire", return_value=True), \
                patch("llm._gpu_release"), patch("llm._gpu_note_activity"), \
                patch("llm.urllib.request.build_opener", return_value=_Opener()):
            out = client._ollama_chat(
                [{"role": "user", "content": "hi"}],
                stream=False, thinking_on=thinking_on)
        return captured["payload"], out

    def test_payload_always_carries_think_flag(self):
        from config import (prompt_token_budget, output_token_budget,
                             model_context_window)
        payload_on, out = self._capture("qwen3:8b", True)
        self.assertEqual(out, "ok")
        self.assertIs(payload_on["think"], True)
        # 16k 窗口：num_ctx 按「prompt预算 + 输出预算(窗口派生,不再写死3072) + 512」
        # 封顶到真实窗口；num_predict 给足派生输出预算（放不下时按窗口余量裁剪,保底512）。
        win = model_context_window("ollama", "qwen3:8b")   # 16384（probe 已打桩 None）
        budget = prompt_token_budget("ollama", "qwen3:8b")
        out_b = output_token_budget("ollama", "qwen3:8b")
        want = budget + out_b + 512
        self.assertEqual(payload_on["options"]["num_ctx"], min(want, win))
        self.assertEqual(payload_on["options"]["num_predict"],
                         min(out_b, max(512, win - budget - 512)))

        payload_off, _ = self._capture("qwen3:8b", False)
        self.assertIs(payload_off["think"], False)

    def test_plain_model_payload_think_false_ctx_by_window(self):
        from config import (prompt_token_budget, output_token_budget,
                             model_context_window)
        # qwen2.5:7b 画像窗口 32768：大窗口不再被 14.5k 限死，输出预算给足
        payload, _ = self._capture("qwen2.5:7b", False)
        self.assertIs(payload["think"], False)
        win = model_context_window("ollama", "qwen2.5:7b")   # 32768（override 画像）
        budget = prompt_token_budget("ollama", "qwen2.5:7b")
        out_b = output_token_budget("ollama", "qwen2.5:7b")
        want = budget + out_b + 512
        self.assertEqual(payload["options"]["num_ctx"], min(want, win))
        self.assertEqual(payload["options"]["num_predict"],
                         min(out_b, max(512, win - budget - 512)))

    def test_small_window_model_caps_ctx_and_keeps_min_output(self):
        # 未知 ollama 模型 → 窗口 16384（非小窗口）；构造一个 8k 画像的客户端
        from llm import LLMClient
        from unittest.mock import patch
        with patch("llm.probe_ollama_context", return_value=None):
            client = LLMClient(provider="ollama", model="tiny:7b")
        client.capability = {"context_window": 8192, "thinking": "none", "cloud": False}
        import json as _json
        captured = {}

        class _Resp:
            def read(self):
                return b'{"message":{"content":"x"}}'

        class _Opener:
            def open(self, req, timeout=None):
                captured["payload"] = _json.loads(req.data.decode("utf-8"))
                return _Resp()

        with patch("llm._gpu_acquire", return_value=True), \
                patch("llm._gpu_release"), patch("llm._gpu_note_activity"), \
                patch("llm.urllib.request.build_opener", return_value=_Opener()):
            client._ollama_chat([{"role": "user", "content": "hi"}],
                                stream=False, thinking_on=False)
        self.assertEqual(captured["payload"]["options"]["num_ctx"], 8192)
        self.assertGreaterEqual(captured["payload"]["options"]["num_predict"], 512)


class OllamaContextProbeTests(unittest.TestCase):
    """/api/show 窗口探测：字段解析、进程内缓存、失败静默回退、LLMClient 接入。"""

    def tearDown(self):
        import llm
        llm._OLLAMA_CTX_CACHE.clear()
        from config import clear_context_window_override
        clear_context_window_override("ollama", "zz-unit-custom:1b")

    def test_parse_show_payload_variants(self):
        from llm import parse_ollama_show_context as p
        # 新版字段 model_info.<arch>.context_length
        self.assertEqual(p({"model_info": {"llama.context_length": 32768}}), 32768)
        # 多个架构字段取最大值，无关数值（total_params）不干扰
        self.assertEqual(p({"model_info": {
            "llama.context_length": 8192,
            "qwen2.context_length": 32768,
            "total_params": 8_000_000_000,
        }}), 32768)
        # 顶层字段兜底
        self.assertEqual(p({"context_length": 16384}), 16384)
        # 无法识别的载荷一律 None
        self.assertIsNone(p(None))
        self.assertIsNone(p({}))
        self.assertIsNone(p({"model_info": {"llama.context_length": 256}}))
        self.assertIsNone(p({"context_length": "4096"}))

    def test_native_base_strips_v1_suffix(self):
        from llm import _ollama_native_base
        self.assertEqual(_ollama_native_base("http://127.0.0.1:11434/v1"),
                         "http://127.0.0.1:11434")
        self.assertEqual(_ollama_native_base("http://127.0.0.1:11434"),
                         "http://127.0.0.1:11434")

    def test_probe_success_uses_native_show_endpoint_and_caches(self):
        import json as _json
        from unittest.mock import patch
        import llm
        seen = {"urls": [], "n": 0}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return _json.dumps({"model_info": {"llama.context_length": 65536}}).encode()

        class _Opener:
            def open(self, req, timeout=None):
                seen["n"] += 1
                seen["urls"].append(req.full_url)
                return _Resp()

        with patch("llm.urllib.request.build_opener", return_value=_Opener()):
            win = llm.probe_ollama_context("unitmodel:1b",
                                           "http://127.0.0.1:11434/v1", force=True)
        self.assertEqual(win, 65536)
        self.assertTrue(seen["urls"][0].endswith("/api/show"))
        self.assertNotIn("/v1/api", seen["urls"][0])
        # 第二次不强制：命中缓存，不再发请求
        with patch("llm.urllib.request.build_opener",
                   side_effect=AssertionError("缓存命中不应再建 opener")):
            win2 = llm.probe_ollama_context("unitmodel:1b",
                                            "http://127.0.0.1:11434/v1")
        self.assertEqual(win2, 65536)
        self.assertEqual(seen["n"], 1)

    def test_probe_failure_returns_none_silently(self):
        from unittest.mock import patch
        import llm

        class _BadOpener:
            def open(self, req, timeout=None):
                raise OSError("connection refused")

        with patch("llm.urllib.request.build_opener", return_value=_BadOpener()):
            self.assertIsNone(llm.probe_ollama_context("zz-missing:1b", force=True))
        # 失败结果同样缓存：切模型等路径重复构造客户端时不会反复等待超时
        with patch("llm.urllib.request.build_opener",
                   side_effect=AssertionError("失败结果应缓存")):
            self.assertIsNone(llm.probe_ollama_context("zz-missing:1b"))

    def test_client_adopts_probed_window_and_custom_override_wins(self):
        import os as _os
        from unittest.mock import patch
        from llm import LLMClient
        from config import set_context_window_override

        env = {"DOCMIND_OLLAMA_PROBE": "1"}
        with patch.dict(_os.environ, env, clear=False), \
                patch("llm.probe_ollama_context", return_value=40960) as mk:
            c = LLMClient(provider="ollama", model="zz-unit-probe:1b")
        self.assertEqual(mk.call_count, 1)
        self.assertEqual(c.context_source, "probe")
        self.assertEqual(c.capability["context_window"], 40960)
        self.assertEqual(c.prompt_budget, int(40960 * 0.75))

        # 手填覆盖存在时跳过探测，窗口以覆盖为准
        set_context_window_override("ollama", "zz-unit-custom:1b", 20000)
        with patch.dict(_os.environ, env, clear=False), \
                patch("llm.probe_ollama_context",
                      side_effect=AssertionError("有手填覆盖时不应再探测")):
            c2 = LLMClient(provider="ollama", model="zz-unit-custom:1b")
        self.assertEqual(c2.context_source, "custom")
        self.assertEqual(c2.capability["context_window"], 20000)

    def test_probe_disabled_by_env_falls_back_to_profile(self):
        import os as _os
        from unittest.mock import patch
        from llm import LLMClient
        with patch.dict(_os.environ, {"DOCMIND_OLLAMA_PROBE": "0"}, clear=False), \
                patch("llm.probe_ollama_context",
                      side_effect=AssertionError("env 关闭后不应探测")):
            c = LLMClient(provider="ollama", model="zz-unit-envoff:1b")
        self.assertEqual(c.context_source, "profile")
        self.assertEqual(c.capability["context_window"], 16384)


class ReasoningStreamTests(unittest.TestCase):
    def test_reasoning_content_goes_to_sink_not_body(self):
        from llm import StreamChat

        class _U:
            def model_dump(self):
                return {"prompt_tokens": 3, "completion_tokens": 2}

        class _Chunk:
            def __init__(self, choices, usage=None):
                self.choices = choices
                self.usage = usage

        class _ThinkChoice:
            finish_reason = None

            class delta:
                content = None
                reasoning_content = "先推理一下"

        class _SayChoice:
            finish_reason = "stop"

            class delta:
                content = "答案"
                reasoning_content = None

        sink = []
        sc = StreamChat(
            [_Chunk([_ThinkChoice()]), _Chunk([_SayChoice()]), _Chunk([], usage=_U())],
            reasoning_sink=sink)
        body = "".join(sc)
        self.assertEqual(body, "答案")
        self.assertEqual("".join(sink), "先推理一下")
        self.assertEqual(sc.reasoning_chars, len("先推理一下"))


class UsageCaptureTests(unittest.TestCase):
    def test_stream_chat_captures_openai_usage(self):
        from llm import StreamChat

        class _U:
            def model_dump(self):
                return {"prompt_tokens": 11, "completion_tokens": 5}

        class _Chunk:
            def __init__(self, choices, usage=None):
                self.choices = choices
                self.usage = usage

        class _Choice:
            finish_reason = "stop"

            class delta:
                content = "hi"
                reasoning_content = None

        sink = {}
        sc = StreamChat([_Chunk([_Choice()]), _Chunk([], usage=_U())], usage_sink=sink)
        out = "".join(sc)
        self.assertEqual(out, "hi")
        self.assertEqual(sink.get("prompt_tokens"), 11)
        self.assertEqual(sink.get("completion_tokens"), 5)


if __name__ == "__main__":
    unittest.main()
