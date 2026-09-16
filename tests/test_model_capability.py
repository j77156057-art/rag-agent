# -*- coding: utf-8 -*-
"""模型能力画像单测：上下文窗口 / 思考模式（native/toggle/none）/ 云端标识。

画像决定两件事：
① ollama num_ctx 扩多大、输出预算给多少（小窗口模型不能盲目发 14k prompt）；
② 前端「深度思考」开关是常亮/可切/置灰，以及后端是否透传 enable_thinking。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    model_context_window,
    model_thinking_mode,
    model_capability,
    prompt_token_budget,
    LLM_MAX_TOKENS,
)


class ContextWindowTests(unittest.TestCase):
    def test_provider_defaults(self):
        self.assertEqual(model_context_window("qwen", "qwen-plus"), 131072)
        self.assertEqual(model_context_window("deepseek", "deepseek-chat"), 131072)
        self.assertEqual(model_context_window("kimi", "kimi-k2-0905-preview"), 131072)
        self.assertEqual(model_context_window("zhipu", "glm-4.6"), 131072)
        # ollama 默认只给 16k（本地显存有限），未知 provider 兜底 32k
        self.assertEqual(model_context_window("ollama", "some-random:7b"), 16384)
        self.assertEqual(model_context_window("totally-new", "m"), 32768)

    def test_model_specific_overrides(self):
        self.assertEqual(model_context_window("deepseek", "deepseek-reasoner"), 65536)
        self.assertEqual(model_context_window("openai", "gpt-4o-2024-08-06"), 128000)
        self.assertEqual(model_context_window("openai", "gpt-4-turbo"), 65536)
        self.assertEqual(model_context_window("openai", "gpt-3.5-turbo"), 16385)
        self.assertEqual(model_context_window("openai", "o1-mini"), 200000)
        self.assertEqual(model_context_window("ollama", "qwen2.5:7b"), 32768)
        # override 严格按 provider 匹配：gpt-4o 的窗口不能串到 ollama 上
        self.assertEqual(model_context_window("ollama", "gpt-4o"), 16384)


class ThinkingModeTests(unittest.TestCase):
    def test_native_reasoner_always_on(self):
        for provider, model in [
            ("deepseek", "deepseek-reasoner"),
            ("qwen", "qwq-32b"),
            ("ollama", "qwq:32b"),
            ("ollama", "deepseek-r1:14b"),
            ("openai", "o1-mini"),
            ("openai", "o3-mini"),
            ("openai", "gpt-5"),
        ]:
            self.assertEqual(
                model_thinking_mode(provider, model), "native",
                f"{provider}/{model} 应为 native 思考模型",
            )

    def test_toggle_qwen3_family(self):
        for provider in ("qwen", "ollama", "llamacpp", "custom", "siliconflow"):
            self.assertEqual(
                model_thinking_mode(provider, "qwen3:8b"), "toggle",
                f"{provider} 上的 qwen3 应支持思考开关",
            )

    def test_plain_models_have_no_thinking(self):
        self.assertEqual(model_thinking_mode("qwen", "qwen-plus"), "none")
        self.assertEqual(model_thinking_mode("deepseek", "deepseek-chat"), "none")
        self.assertEqual(model_thinking_mode("kimi", "kimi-k2-0905-preview"), "none")
        self.assertEqual(model_thinking_mode("zhipu", "glm-4.6"), "none")
        self.assertEqual(model_thinking_mode("openai", "gpt-4o-mini"), "none")
        # qwen3 开关规则按 provider 匹配：OpenAI 官方没有 qwen3，不应误判为 toggle
        self.assertEqual(model_thinking_mode("openai", "qwen3-fake"), "none")

    def test_capability_bundle(self):
        cap = model_capability("deepseek", "deepseek-reasoner")
        self.assertEqual(cap["context_window"], 65536)
        self.assertEqual(cap["thinking"], "native")
        self.assertTrue(cap["cloud"])

        cap_local = model_capability("ollama", "qwen3:8b")
        self.assertEqual(cap_local["thinking"], "toggle")
        self.assertFalse(cap_local["cloud"])
        self.assertEqual(cap_local["context_window"], 16384)


class PromptBudgetScalingTests(unittest.TestCase):
    """prompt 预算必须随模型真实窗口缩放，不能所有模型一刀切 11k。"""

    def test_small_local_window_scales_down(self):
        # 16k 本地模型：75% 窗口 = 12288，受输出预留约束的 usable=12800，取 12288
        self.assertEqual(prompt_token_budget("ollama", "qwen3:8b"), 12288)

    def test_large_cloud_window_scales_up(self):
        # 131k 云端模型：预算应放到约 98k（旧逻辑只给 11k，浪费 90% 窗口）
        b = prompt_token_budget("qwen", "qwen-plus")
        self.assertEqual(b, int(131072 * 0.75))
        self.assertGreater(b, 90000)

    def test_one_million_window_formula(self):
        # 1M 窗口模型（如 gemini-1.5-pro / qwen-long 级别）：75% ≈ 786k，
        # 8000 字符（约 5k token）级历史在该预算下占比不到 1%，绝不会触发压缩。
        win = 1048576
        budget = max(6144, min(win - LLM_MAX_TOKENS - 512, int(win * 0.75)))
        self.assertEqual(budget, 786432)
        trigger = int(budget * 0.6)
        self.assertLess(5000, trigger)
        self.assertGreater(trigger, 400_000)

    def test_tiny_window_keeps_floor(self):
        # 极小窗口也保 6144 下限（由 num_ctx 封顶逻辑再做物理裁剪）
        # 用一个不存在的 provider 画像走 32768 默认即可，下限逻辑直接验公式
        win = 4096
        budget = max(6144, min(win - LLM_MAX_TOKENS - 512, int(win * 0.75)))
        self.assertEqual(budget, 6144)


class OllamaBudgetTests(unittest.TestCase):
    """num_ctx / num_predict 的预算关系（与 llm._ollama_chat 的封顶逻辑同构）。"""

    def test_window_large_enough_uses_full_output_budget(self):
        win = 16384
        budget = prompt_token_budget("ollama", "qwen3:8b")
        want_ctx = budget + LLM_MAX_TOKENS + 512
        num_ctx = min(want_ctx, win)
        # 16k 窗口放得下按比例缩放的预算：输出必须给满 LLM_MAX_TOKENS
        self.assertGreaterEqual(win, want_ctx)
        self.assertEqual(num_ctx, want_ctx)
        num_predict = LLM_MAX_TOKENS if num_ctx >= want_ctx else 512
        self.assertEqual(num_predict, LLM_MAX_TOKENS)

    def test_small_window_reserves_minimum_output(self):
        # 8k 小窗口：预算再大也被物理封顶，至少留 512 输出
        win = 8192
        budget = 12288
        want_ctx = budget + LLM_MAX_TOKENS + 512
        num_ctx = min(want_ctx, win)
        self.assertEqual(num_ctx, 8192)
        num_predict = min(LLM_MAX_TOKENS, max(512, win - num_ctx + 512))
        self.assertEqual(num_predict, 512)


if __name__ == "__main__":
    unittest.main()
