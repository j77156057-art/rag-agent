# -*- coding: utf-8 -*-
"""按 provider 计价的成本核算与预算熔断（离线）。"""
import os
import tempfile
import unittest

import pricing


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_price_")
        self._old_budget = pricing.BUDGET_FILE
        self._old_pricing = pricing.PRICING_FILE
        pricing.BUDGET_FILE = os.path.join(self.tmp, "budget.json")
        pricing.PRICING_FILE = os.path.join(self.tmp, "pricing.json")

    def tearDown(self):
        pricing.BUDGET_FILE = self._old_budget
        pricing.PRICING_FILE = self._old_pricing

    def test_price_lookup_and_model_override(self):
        self.assertEqual(pricing.price_for("ollama"), (0.0, 0.0))
        self.assertEqual(pricing.price_for("qwen", "qwen-plus"), (0.8, 2.0))
        # 未知模型回落到 provider 默认价
        self.assertEqual(pricing.price_for("qwen", "no-such-model"), (0.8, 2.0))

    def test_cost_local_is_zero(self):
        self.assertEqual(pricing.cost_cny("ollama", "x", 1_000_000, 1_000_000), 0.0)
        self.assertEqual(pricing.cost_cny("mock", "m", 999, 999), 0.0)

    def test_cost_cloud_math(self):
        # qwen-plus: 0.8 元/1M in, 2.0 元/1M out
        self.assertAlmostEqual(pricing.cost_cny("qwen", "qwen-plus", 1_000_000, 1_000_000), 2.8)

    def test_pricing_file_override(self):
        with open(pricing.PRICING_FILE, "w", encoding="utf-8") as f:
            f.write('{"deepseek": {"in": 10.0, "out": 20.0}}')
        self.assertEqual(pricing.price_for("deepseek"), (10.0, 20.0))

    def test_budget_check_charge_and_reset(self):
        pricing.set_limit(1.0, "s1")
        self.assertTrue(pricing.check("s1")["ok"])
        pricing.charge(0.4, "s1")
        self.assertTrue(pricing.check("s1")["ok"])
        pricing.charge(0.7, "s1")
        st = pricing.check("s1")
        self.assertFalse(st["ok"])
        self.assertIn("会话预算", st["reason"])
        pricing.reset("s1")
        self.assertTrue(pricing.check("s1")["ok"])

    def test_global_limit_blocks_all_sessions(self):
        pricing.set_limit(0.5)
        pricing.charge(0.6, "any")
        self.assertFalse(pricing.check("other")["ok"])
        pricing.reset()

    def test_zero_limit_means_unlimited(self):
        pricing.set_limit(0)
        pricing.charge(100.0, "s")
        self.assertTrue(pricing.check("s")["ok"])
        pricing.reset()

    def test_charge_zero_is_noop(self):
        pricing.set_limit(1.0, "s")
        pricing.charge(0.0, "s")
        self.assertEqual(pricing.status()["global_spent"], 0.0)

    def test_status_shape(self):
        st = pricing.status()
        for k in ("global_limit", "global_spent", "day", "day_spent", "sessions", "pricing_file"):
            self.assertIn(k, st)


if __name__ == "__main__":
    unittest.main()
