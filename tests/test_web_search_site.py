# -*- coding: utf-8 -*-
"""web_search 站点限定 + 百度后端回归：

- _parse_web_search_arg：纯关键词 / site: / platform:(别名映射) / 行内 site / 空；
- web_search：site/platform 必须拼成 `site:` 透传给后端；
- 百度后端：auto 下作为 ddg 失败后的兜底层、可强制指定、HTML 解析正确。
"""
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools


class ParseWebSearchArgTests(unittest.TestCase):
    def test_pure_query(self):
        q, site = tools._parse_web_search_arg("某关键词")
        self.assertEqual(q, "某关键词")
        self.assertIsNone(site)

    def test_multiline_site(self):
        q, site = tools._parse_web_search_arg("query: 某关键词\nsite: github.com")
        self.assertEqual(q, "某关键词")
        self.assertEqual(site, "github.com")

    def test_platform_alias_maps_to_domain(self):
        q, site = tools._parse_web_search_arg("platform: github\n某关键词")
        self.assertEqual(site, "github.com")
        self.assertEqual(q, "某关键词")

    def test_platform_chinese_alias(self):
        q, site = tools._parse_web_search_arg("platform: b站\n如何做特效")
        self.assertEqual(site, "bilibili.com")

    def test_inline_site_within_query(self):
        q, site = tools._parse_web_search_arg("foo site: github.com")
        self.assertEqual(q, "foo")
        self.assertEqual(site, "github.com")

    def test_empty(self):
        q, site = tools._parse_web_search_arg("")
        self.assertEqual(q, "")
        self.assertIsNone(site)


class WebSearchSiteAppendTests(unittest.TestCase):
    """site/platform 必须拼成 site: 透传给后端。"""

    def _auto(self):
        return {**os.environ, "WEB_SEARCH_BACKEND": "auto"}

    def test_site_passed_to_backend(self):
        with patch.dict(tools.os.environ, self._auto(), clear=True), \
                patch("tools._ddg_search", return_value="· 结果") as m:
            tools.web_search("query: 某关键词\nsite: github.com")
        sent = m.call_args[0][0]
        self.assertIn("site:github.com", sent)
        self.assertIn("某关键词", sent)

    def test_platform_passed_as_site(self):
        with patch.dict(tools.os.environ, self._auto(), clear=True), \
                patch("tools._ddg_search", return_value="· 结果") as m:
            tools.web_search("platform: github\n某关键词")
        sent = m.call_args[0][0]
        self.assertIn("site:github.com", sent)


class BaiduBackendTests(unittest.TestCase):
    """auto 下百度作为 ddg 失败后的兜底层之一。"""

    def _auto(self):
        return {**os.environ, "WEB_SEARCH_BACKEND": "auto"}

    def test_baidu_used_when_ddg_fails(self):
        with patch.dict(tools.os.environ, self._auto(), clear=True), \
                patch("tools._ddg_search", side_effect=OSError("blocked")), \
                patch("tools._baidu_search", return_value="· 百度结果") as m:
            out = tools.web_search("某关键词")
        self.assertEqual(out, "· 百度结果")
        m.assert_called_once()

    def test_forced_baidu_backend(self):
        env = {**os.environ, "WEB_SEARCH_BACKEND": "baidu"}
        with patch.dict(tools.os.environ, env, clear=True), \
                patch("tools._baidu_search", return_value="· 百度结果") as m, \
                patch("tools._ddg_search", return_value="x") as ddg:
            out = tools.web_search("某关键词")
        self.assertEqual(out, "· 百度结果")
        ddg.assert_not_called()

    def test_baidu_parse_html(self):
        html = (
            '<div class="result c-container">'
            '<h3 class="t"><a href="https://example.com/a">示例标题</a></h3>'
            '<div class="c-abstract">示例摘要内容</div>'
            '</div>'
        )
        fake = io.BytesIO(html.encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake), \
                patch("urllib.request.Request", side_effect=lambda url, **kw: url):
            out = tools._baidu_search("某关键词")
        self.assertIn("示例标题", out)
        self.assertIn("示例摘要内容", out)
        self.assertIn("https://example.com/a", out)


if __name__ == "__main__":
    unittest.main()
