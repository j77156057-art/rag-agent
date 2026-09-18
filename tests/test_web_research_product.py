import io
import json
import os
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
