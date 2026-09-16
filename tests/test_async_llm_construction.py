# -*- coding: utf-8 -*-
"""D2 回归：async 处理函数不再在事件循环线程里同步构造 LLMClient。

LLMClient.__init__ 在 provider=ollama 时会同步探活（≤3s）；api.py 的 async 处理
函数里构造它必须经 run_in_threadpool。本用例用假 LLMClient 记录构造线程来证明。
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api
import config


class _RecordingLLM:
    """记录构造线程与 set_config 读取的属性。"""
    created = []

    def __init__(self, *args, **kwargs):
        self.thread = threading.current_thread()
        self.capability = {"context_window": 32768, "thinking": "none", "cloud": False}
        self.context_source = "profile"
        _RecordingLLM.created.append(self)


class AsyncLlmConstructionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _RecordingLLM.created = []
        self._orig_llm = api.LLMClient
        self._orig_runtime = dict(config._RUNTIME)
        # set_config 会改写模块级默认 agent，备份后还原
        self._orig_agent_llm = api.agent.llm
        self._orig_agent_history = list(api.agent.history)
        self._orig_agent_ctx = api.agent.last_context

    def tearDown(self):
        api.LLMClient = self._orig_llm
        config._RUNTIME.clear()
        config._RUNTIME.update(self._orig_runtime)
        api.agent.llm = self._orig_agent_llm
        api.agent.history = self._orig_agent_history
        api.agent.last_context = self._orig_agent_ctx

    async def test_set_config_constructs_llm_off_event_loop(self):
        api.LLMClient = _RecordingLLM
        resp = await api.set_config(api.ConfigReq(provider="mock"))

        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(len(_RecordingLLM.created), 1, "应恰好构造 1 个客户端")
        worker = _RecordingLLM.created[0].thread
        self.assertIsNot(
            worker, threading.main_thread(),
            "LLMClient 构造应发生在工作线程（run_in_threadpool），而非事件循环主线程",
        )


if __name__ == "__main__":
    unittest.main()
