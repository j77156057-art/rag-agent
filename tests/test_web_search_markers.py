# -*- coding: utf-8 -*-
"""联网工具失败标记回归：

- web_search 区分「后端硬异常」与「后端返回空结果」——硬异常要保留「均不可达」提示，
  空结果不追加外网缺失提示（A2b）；
- web_fetch 的「网页读取失败」必须纳入 agent._FAILURE_MARKERS，才会触发反思（A5）。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
import tools


class WebSearchFailureSuffixTests(unittest.TestCase):
    """auto 模式下两种「整体失败」的收尾文案差异。"""

    def _auto_env(self):
        return {**os.environ, "WEB_SEARCH_BACKEND": "auto"}

    def test_both_backends_raise_keeps_unreachable_hint(self):
        # ddg、百度、bing 均抛异常（硬故障）→ 文案同时含「搜索失败」与「均不可达」
        with patch.dict(tools.os.environ, self._auto_env(), clear=True), \
                patch("tools._ddg_search", side_effect=OSError("boom")), \
                patch("tools._baidu_search", side_effect=OSError("boom")), \
                patch("tools._bing_search", side_effect=OSError("boom")):
            out = tools.web_search("q")
        self.assertIn("搜索失败", out)
        self.assertIn("均不可达", out)
        self.assertTrue(agent._is_failure(out), "硬异常必须被判定为失败")

    def test_both_backends_empty_no_unreachable_hint(self):
        # ddg、百度、bing 均返回空结果标记（不是硬故障）→ 不追加外网缺失提示
        empty = "搜索未返回结果，可能是网络受限或该关键词无结果。"
        with patch.dict(tools.os.environ, self._auto_env(), clear=True), \
                patch("tools._ddg_search", return_value=empty), \
                patch("tools._baidu_search", return_value=empty), \
                patch("tools._bing_search", return_value=empty):
            out = tools.web_search("q")
        self.assertIn("搜索未返回结果", out)
        self.assertNotIn("均不可达", out)


class WebFetchFailureMarkerTests(unittest.TestCase):
    """web_fetch 失败文案必须被 _is_failure 判定为失败（同 A2 同源问题）。"""

    def test_read_failure_detected(self):
        self.assertTrue(agent._is_failure("网页读取失败：OSError: boom"))
        self.assertTrue(agent._is_failure("网页读取失败：只允许 http/https。"))

    def test_normal_content_not_failure(self):
        self.assertFalse(agent._is_failure("网页内容如下：Hello world"))


if __name__ == "__main__":
    unittest.main()
