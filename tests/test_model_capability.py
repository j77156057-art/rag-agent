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
    set_context_window_override,
    get_context_window_override,
    clear_context_window_override,
    load_state,
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
        budget = prompt_token_budget("custom", "zz-unit-only-model", context_window=win)
        self.assertEqual(budget, 786432)
        trigger = int(budget * 0.8)
        self.assertLess(5000, trigger)
        self.assertGreater(trigger, 600_000)

    def test_medium_window_scales_down(self):
        # 16k 窗口：75% = 12288，受输出预留约束的 usable=12800，取 12288
        budget = prompt_token_budget("custom", "zz-unit-only-model", context_window=16384)
        self.assertEqual(budget, 12288)

    def test_tiny_window_budget_never_exceeds_window(self):
        # 极小窗口：预算必须严格小于窗口本身（否则提示词会被服务端静默截断），
        # 同时保留一个不低于 256 的可用下限（由真实函数计算，不再本地复制公式）。
        for win in (1024, 4096, 8192):
            budget = prompt_token_budget("custom", "zz-unit-only-model", context_window=win)
            self.assertGreaterEqual(budget, 256, f"win={win} 预算不应低于 256")
            self.assertLess(budget, win, f"win={win} 预算不应越过窗口")


class ContextWindowOverrideTests(unittest.TestCase):
    """用户手填窗口覆盖：优先级 自定义 > 实时探测 > 画像 > 默认；可持久化恢复。"""

    P, M = "custom", "zz-unit-only-model"

    def tearDown(self):
        clear_context_window_override(self.P, self.M)

    def test_priority_custom_live_profile_default(self):
        # 无覆盖：实时探测值优先于画像/默认
        self.assertEqual(model_context_window(self.P, self.M, live_window=99000), 99000)
        # 无探测：未知模型走默认 32768
        self.assertEqual(model_context_window(self.P, self.M), 32768)
        # 手填覆盖后压过探测值
        set_context_window_override(self.P, self.M, 50000)
        self.assertEqual(model_context_window(self.P, self.M, live_window=99000), 50000)
        self.assertEqual(prompt_token_budget(self.P, self.M), int(50000 * 0.75))
        # 清除覆盖后探测值重新生效
        clear_context_window_override(self.P, self.M)
        self.assertEqual(model_context_window(self.P, self.M, live_window=99000), 99000)

    def test_zero_and_garbage_clear_override(self):
        set_context_window_override(self.P, self.M, 40960)
        set_context_window_override(self.P, self.M, 0)
        self.assertIsNone(get_context_window_override(self.P, self.M))
        set_context_window_override(self.P, self.M, "not-a-number")
        self.assertIsNone(get_context_window_override(self.P, self.M))

    def test_override_key_case_insensitive_and_scoped(self):
        set_context_window_override("  Custom ", " ZZ-Unit-Only-MODEL ", 71000)
        self.assertEqual(get_context_window_override(self.P, self.M), 71000)
        # 覆盖严格按 provider/model 隔离，不串到别的模型
        self.assertIsNone(get_context_window_override("ollama", self.M))
        clear_context_window_override(self.P, self.M)

    def test_persist_and_reapply(self):
        import tempfile
        import config
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        old_state = config.STATE_FILE
        try:
            config.STATE_FILE = tmp.name
            set_context_window_override(self.P, self.M, 88000)
            # 落盘内容可读回
            self.assertEqual(load_state("context_window_overrides", {})[f"{self.P}/{self.M}"], 88000)
            # 模拟重启：清空内存字典后重新应用
            config._CONTEXT_WINDOW_OVERRIDES.pop(f"{self.P}/{self.M}", None)
            self.assertIsNone(get_context_window_override(self.P, self.M))
            config._apply_persisted_state()
            self.assertEqual(get_context_window_override(self.P, self.M), 88000)
        finally:
            # 直接摘内存键，绝不在恢复 STATE_FILE 后调用 clear（那会把清空结果写进真实状态文件）
            config._CONTEXT_WINDOW_OVERRIDES.pop(f"{self.P}/{self.M}", None)
            config.STATE_FILE = old_state
            os.unlink(tmp.name)


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
