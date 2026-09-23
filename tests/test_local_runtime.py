import os
import unittest
from unittest.mock import patch

from agent_runtime.local_runtime import (effective_parallelism,
                                          effective_subagent_limit,
                                          resource_profile)


class LocalRuntimeTests(unittest.TestCase):
    def test_profiles_are_conservative_for_known_ollama_models(self):
        tiny = resource_profile("ollama", "qwen3:4b")
        medium = resource_profile("ollama", "qwen3:14b")
        large = resource_profile("ollama", "qwen3.6:35b-a3b")
        self.assertEqual((tiny.tier, tiny.max_parallel, tiny.max_subagents), ("tiny", 1, 3))
        self.assertEqual((medium.tier, medium.max_parallel), ("medium", 1))
        self.assertEqual((large.tier, large.max_parallel), ("large", 1))

    def test_requested_parallelism_is_clamped_for_local_only(self):
        self.assertEqual(effective_parallelism("ollama", "qwen3:8b", 4), 1)
        self.assertEqual(effective_subagent_limit("ollama", "qwen3:8b", 4), 4)
        # 4B 模型仍然只允许一个并发生成，但可以串行完成最小三阶段 DAG。
        self.assertEqual(effective_parallelism("ollama", "qwen3:4b", 4), 1)
        self.assertEqual(effective_subagent_limit("ollama", "qwen3:4b", 4), 3)
        self.assertEqual(effective_parallelism("deepseek", "deepseek-chat", 4), 4)

    def test_operator_can_raise_local_inference_slots(self):
        with patch.dict(os.environ, {"DOCMIND_LOCAL_LLM_MAX_CONCURRENCY": "2",
                                     "DOCMIND_LOCAL_SUBAGENT_MAX": "3"}, clear=False):
            profile = resource_profile("ollama", "qwen3:8b")
            self.assertEqual(profile.max_parallel, 2)
            self.assertEqual(profile.max_subagents, 3)


if __name__ == "__main__":
    unittest.main()
