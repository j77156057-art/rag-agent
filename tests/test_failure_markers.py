# -*- coding: utf-8 -*-
"""工具失败文案白名单（agent._FAILURE_MARKERS）回归。

守住的这一类问题：工具的失败观察若不命中 _FAILURE_MARKERS，会被 _is_failure 误判为
成功 —— 不触发自我反思、trace 的 ok 也误标为 True。这里用表驱动把所有已知失败文案
一次性钉死，新增工具失败文案时同步补此表。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent


# (说明, 观察文案, 期望 is_failure)
FAILURE_CASES = [
    ("web_search 双后端硬异常", "搜索失败: bing: OSError: boom（DuckDuckGo/Bing 均不可达…）", True),
    ("web_search 无结果", "搜索未返回结果，可能是网络受限或该关键词无结果。", True),
    ("web_fetch 网络失败", "网页读取失败：TimeoutError: late", True),
    ("web_fetch 协议非法", "网页读取失败：只允许 http/https。", True),
    ("search_code 无命中", "未找到相关内容。", True),
    ("calculate 运行异常", "计算失败: division by zero", True),
    ("calculate 非法字符", "表达式包含非法字符，已拒绝执行。", True),
    ("read_file 读取异常", "读取失败: PermissionError: [Errno 13] access denied", True),
    ("read_file 文件缺失", "文件不存在：src/missing.py", True),
    ("read_file 路径越界", "拒绝访问：../../etc/passwd 不在代码根目录内。", True),
    ("空参缺输入", "未提供搜索关键词。", True),
    ("工具入参缺失", "参数缺失：请提供分区清单。可传 JSON 数组…", True),
    ("工具入参格式错", "参数缺失：请提供 regions: <JSON 数组或 {\"regions\":[...]} 分区清单>。", True),
    ("安全策略拦截", "安全限制：该操作被拒绝。", True),
    ("写操作护栏", "拒绝写入：未授权写操作。", True),
]

# 正常文案必须判为非失败，否则会误触发反思、打断正常收尾
NORMAL_CASES = [
    ("检索命中", "已找到 2 处匹配：a.py:12、b.py:34", False),
    ("网页正文", "网页内容如下：Hello world", False),
    ("计算结果回填", "计算结果为：2", False),
]


class FailureMarkerTableTests(unittest.TestCase):
    def test_all_failure_texts_detected(self):
        for name, text, expected in FAILURE_CASES:
            with self.subTest(case=name):
                self.assertIs(
                    agent._is_failure(text), expected,
                    f"{name} 应判为失败：{text!r}",
                )

    def test_normal_texts_not_failure(self):
        for name, text, expected in NORMAL_CASES:
            with self.subTest(case=name):
                self.assertIs(
                    agent._is_failure(text), expected,
                    f"{name} 不应判为失败：{text!r}",
                )

    def test_empty_observation_is_failure(self):
        # 既有语义：空/空白观察一律视为失败
        self.assertTrue(agent._is_failure(""))
        self.assertTrue(agent._is_failure("   "))
        self.assertTrue(agent._is_failure(None))

    def test_markers_cover_every_table_entry(self):
        # 反向校验：表中每条失败文案都必须真的命中 _FAILURE_MARKERS 中的某个子串，
        # 防止 _is_failure 逻辑变动后表与实现脱节。
        for name, text, expected in FAILURE_CASES:
            if not expected:
                continue
            with self.subTest(case=name):
                self.assertTrue(
                    any(m in text for m in agent._FAILURE_MARKERS),
                    f"{name} 未命中任何 _FAILURE_MARKERS：{text!r}",
                )


if __name__ == "__main__":
    unittest.main()
