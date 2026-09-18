import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools


class WebResearchProductTests(unittest.TestCase):
    def test_source_score_is_explainable(self):
        score, reason = tools._source_score("https://github.com/a/b", "official docs", "")
        self.assertGreater(score, .6)
        self.assertIn("平台原站", reason)

    def test_conflict_marker(self):
        out = tools._detect_source_conflicts(["窗口 16 GB", "窗口 24 GB"])
        self.assertIn("多个数值", out)

    def test_github_api_formatter(self):
        payload = {"items": [{"full_name": "a/b", "description": "official", "html_url": "https://github.com/a/b", "stargazers_count": 3, "updated_at": "2026-01-01T00:00:00Z"}]}
        with patch("tools._json_request", return_value=payload):
            out = tools._github_search("godot")
        self.assertIn("GitHub 专用搜索", out)
        self.assertIn("a/b", out)
        self.assertIn("可信度参考", out)

    def test_bilibili_api_formatter(self):
        payload = {"data": {"result": [{"title": "教程", "bvid": "BV1xx", "description": "说明"}]}}
        with patch("tools._json_request", return_value=payload):
            out = tools._bilibili_search("godot")
        self.assertIn("B 站专用视频搜索", out)
        self.assertIn("BV1xx", out)

    def test_subtitles_requires_video_id(self):
        self.assertIn("BV 号或 av 号", tools.web_subtitles("https://www.bilibili.com/"))

    def test_specialized_failure_keeps_diagnostic_on_fallback(self):
        with patch("tools._bilibili_search", return_value="B 站专用搜索暂不可用（验证码）"), \
                patch("tools._ddg_search", return_value="· fallback\n  s\n  https://example.com"), \
                patch("tools.get_web_search_provider", return_value="ddg"), \
                patch.object(sys.modules["__main__"], "__spec__", types.SimpleNamespace(name="test")), \
                patch.dict(tools.os.environ, {"WEB_SEARCH_PREFER_RECENT": "0", "DOCMIND_WEB_CACHE": "0"}, clear=False):
            out = tools.web_search("platform: b站\n教程")
        self.assertIn("B 站专用搜索暂不可用", out)
        self.assertIn("通用搜索回退结果", out)


if __name__ == "__main__":
    unittest.main()
