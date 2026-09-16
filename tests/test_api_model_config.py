# -*- coding: utf-8 -*-
"""/api/config 模型切换路由测试：自定义 OpenAI 兼容端点校验 + 能力画像回传。

直接调用 async 处理器（不经 HTTP），失败路径在任何全局状态被修改之前返回，
成功路径用例在 finally 中恢复运行时覆盖与全局 agent.llm。
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import config  # noqa: E402


_RUNTIME_KEYS = ("llm_provider", "llm_model", "llm_base_url", "llm_api_key")


def _call(**kwargs):
    return asyncio.run(api.set_config(api.ConfigReq(**kwargs)))


class ConfigValidationTests(unittest.TestCase):
    def test_unknown_provider_rejected(self):
        resp = _call(provider="not-a-vendor", model="x")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("未知 provider", resp.body.decode("utf-8"))

    def test_custom_requires_http_base_url(self):
        resp = _call(provider="custom", model="gpt-4o-mini", base_url="file:///etc/passwd")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("http(s)", resp.body.decode("utf-8"))

    def test_custom_requires_base_url_when_none_saved(self):
        old = config.get_runtime("llm_base_url")
        config._RUNTIME.pop("llm_base_url", None)
        try:
            resp = _call(provider="custom", model="gpt-4o-mini", base_url="")
            self.assertEqual(resp.status_code, 400)
            self.assertIn("接口地址", resp.body.decode("utf-8"))
        finally:
            if old is not None:
                config.set_runtime("llm_base_url", old)

    def test_custom_requires_model_name(self):
        resp = _call(provider="custom", model="  ", base_url="https://host.test/v1")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("模型名称", resp.body.decode("utf-8"))


class ConfigSwitchTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: config.get_runtime(k) for k in _RUNTIME_KEYS}
        self._old_llm = api.agent.llm

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                config._RUNTIME.pop(k, None)
            else:
                config.set_runtime(k, v)
        api.agent.llm = self._old_llm

    def test_custom_switch_returns_capability_and_echoes_base_url(self):
        resp = _call(provider="custom", model="qwen3-8b-local",
                     base_url="https://llm.internal.example.com/v1/")
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["llm_provider"], "custom")
        # 尾部斜杠被规范化
        self.assertEqual(resp["base_url"], "https://llm.internal.example.com/v1")
        self.assertEqual(resp["capability"]["thinking"], "toggle")
        # 全局 agent 的 LLM 客户端已即时重建
        self.assertEqual(api.agent.llm.provider, "custom")
        self.assertEqual(api.agent.llm.model, "qwen3-8b-local")

    def test_mock_switch_needs_no_key(self):
        resp = _call(provider="mock", model="mock")
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["warnings"], [])
        self.assertFalse(resp["capability"]["cloud"])

    def test_ollama_switch_hidden_behind_probe(self):
        """切到 ollama 必须先过模型探活：探活失败时不得改动当前模型。"""
        before = config.get_runtime("llm_provider")
        with patch("api._check_ollama_model", return_value=(False, "模型不存在")):
            resp = _call(provider="ollama", model="not-exist:latest")
        self.assertFalse(resp["ok"])
        self.assertIn("模型不存在", resp.get("model_error", ""))
        self.assertEqual(config.get_runtime("llm_provider"), before,
                         "探活失败时不能切换 provider")


class GetConfigShapeTests(unittest.TestCase):
    def test_payload_carries_provider_meta_and_capability(self):
        async def go():
            with patch("api.check_ollama", return_value={"reachable": False}):
                return await api.get_config()
        resp = asyncio.run(go())
        for name in ("qwen", "deepseek", "kimi", "zhipu", "siliconflow",
                     "openai", "ollama", "llamacpp", "custom", "mock"):
            self.assertIn(name, resp["providers"])
            meta = resp["provider_meta"][name]
            self.assertTrue(meta["label"])
            self.assertIn("cloud", meta)
        self.assertIn("context_window", resp["capability"])
        self.assertIn("thinking", resp["capability"])
        self.assertIn("custom_base_url", resp)
        # 密钥字段只回布尔，不回原文
        self.assertNotIn("api_key", resp)


if __name__ == "__main__":
    unittest.main()
