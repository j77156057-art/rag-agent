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
