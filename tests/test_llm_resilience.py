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
        payload_on, out = self._capture("qwen3:8b", True)
        self.assertEqual(out, "ok")
        self.assertIs(payload_on["think"], True)
        # 16k 窗口：完整预算 14584 放得下，num_predict 必须给满 3072
        self.assertEqual(payload_on["options"]["num_ctx"], 14584)
        self.assertEqual(payload_on["options"]["num_predict"], 3072)

        payload_off, _ = self._capture("qwen3:8b", False)
        self.assertIs(payload_off["think"], False)

    def test_plain_model_payload_think_false_ctx_by_window(self):
        # qwen2.5:7b 画像窗口 32768，同样放得下完整预算
        payload, _ = self._capture("qwen2.5:7b", False)
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"]["num_ctx"], 14584)
        self.assertEqual(payload["options"]["num_predict"], 3072)

    def test_small_window_model_caps_ctx_and_keeps_min_output(self):
        # 未知 ollama 模型 → 窗口 16384（非小窗口）；构造一个 8k 画像的客户端
        from llm import LLMClient
        client = LLMClient(provider="ollama", model="tiny:7b")
        client.capability = {"context_window": 8192, "thinking": "none", "cloud": False}
        import json as _json
        from unittest.mock import patch
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
