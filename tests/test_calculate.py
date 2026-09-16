# -*- coding: utf-8 -*-
"""calculate 加固回归：幂运算指数限幅（可静态求值）+ 结果位数兜底。

守住两类问题：9**9**9 这类嵌套幂会让 eval 长时间占满 CPU（实测 >6s 不返回）；
多重乘法可能堆出超长整数。两者都应被前置/兜底拒绝，且必须「快速返回」。
同时保证合法的「可静态求值」指数（2**3**2、2**(3+4) 等）不被误杀。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools

_REJECT = "不支持的 ** 指数"


class CalculateGuardTests(unittest.TestCase):
    def test_normal_pow(self):
        self.assertEqual(tools.calculate("2**10"), "1024")

    def test_static_exponent_nested_pow(self):
        # 指数 3**2 可静态求出（=9），2**9=512 → 合法放行（旧判据会误杀）
        self.assertEqual(tools.calculate("2**3**2"), "512")

    def test_static_exponent_arithmetic(self):
        # 指数 (3+4)=7 → 2**7=128
        self.assertEqual(tools.calculate("2**(3+4)"), "128")

    def test_zero_exponent(self):
        self.assertEqual(tools.calculate("2**0"), "1")

    def test_negative_exponent(self):
        self.assertEqual(tools.calculate("2**(-1)"), "0.5")

    def test_max_allowed_exponent(self):
        self.assertEqual(tools.calculate("2**512"), str(2 ** 512))

    def test_over_max_exponent_rejected(self):
        self.assertIn(_REJECT, tools.calculate("2**513"))

    def test_compare_after_pow(self):
        self.assertEqual(tools.calculate("2**10 > 1000"), "成立（True）")

    def test_large_but_allowed_pow(self):
        # 指数 100 ≤ 512、结果 101 位 < 4000，应正常返回
        self.assertEqual(tools.calculate("10**100"), str(10 ** 100))

    def test_nested_pow_rejected_fast(self):
        # 外层指数 9**9 可静态求出=387420489 > 512 → 拒绝，绝不进入 eval
        tools.calculate("9**9**9")  # 预热
        t = time.monotonic()
        out = tools.calculate("9**9**9")
        dt = time.monotonic() - t
        self.assertIn(_REJECT, out)
        self.assertLess(dt, 0.05, "9**9**9 必须快速拒绝（<50ms），不得卡死")

    def test_big_exponent_literal_rejected_fast(self):
        tools.calculate("2**1000")  # 预热
        t = time.monotonic()
        out = tools.calculate("2**1000")  # 字面量但 > 512
        dt = time.monotonic() - t
        self.assertIn(_REJECT, out)
        self.assertLess(dt, 0.05)

    def test_result_too_many_digits_rejected(self):
        # 每个 9**512 约 489 位，9 个相乘 ≈ 4400 位 > 4000 → 兜底拒绝
        expr = "*".join(["9**512"] * 9)
        self.assertIn("计算结果过大", tools.calculate(expr))

    def test_non_static_exponent_rejected(self):
        # 指数是名称（非字面量、不可静态求出）→ 命中白名单/指数校验而拒绝
        out = tools.calculate("2**n")
        self.assertTrue(_REJECT in out or "非法字符" in out)


if __name__ == "__main__":
    unittest.main()
