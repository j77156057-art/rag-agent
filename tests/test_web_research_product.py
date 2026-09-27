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
from agent import _clip_tool_observation


class _FakeToolResult:
    """模拟 ToolResult：带 `.text` 字符串，但自身不可切片 / 不可下标 / 不可成员判断。

    若下游未先规整成 str 就 `re.findall` / `join` / `in`，这里会主动抛 TypeError，
    从而使测试真正覆盖「工具返回值未规整即被使用」这一 bug 类。
    """

    def __init__(self, text):
        self.text = text

    def __getitem__(self, item):
        raise TypeError("'ToolResult' object is not subscriptable")

    def __contains__(self, item):
        raise TypeError("'ToolResult' object is not iterable")


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

    def test_builtin_auto_merges_and_deduplicates_backends(self):
        rows = {
            "ddg": "· Godot official docs\n  scene tree reference\n  https://docs.godotengine.org/en/stable/scene.html",
            "baidu": "· Godot 场景树教程\n  scene tree reference\n  https://docs.godotengine.org/en/stable/scene.html\n"
                     "· Godot community guide\n  scene tree guide\n  https://example.com/godot",
            "bing": "· Godot scene tree\n  scene tree reference\n  https://example.net/godot",
        }
        with patch.dict(tools.os.environ, {"WEB_SEARCH_FANOUT": "3"}, clear=False), \
                patch.object(tools, "_ddg_search", side_effect=lambda _q: rows["ddg"]), \
                patch.object(tools, "_baidu_search", side_effect=lambda _q: rows["baidu"]), \
                patch.object(tools, "_bing_search", side_effect=lambda _q: rows["bing"]):
            out = tools._builtin_search("builtin_auto", "Godot scene tree")
        self.assertIn("聚合搜索结果", out)
        self.assertEqual(out.count("https://docs.godotengine.org/en/stable/scene.html"), 1)
        self.assertIn("https://example.com/godot", out)

    def test_web_observation_clip_keeps_research_tail(self):
        raw = "研究主题：上下文窗口\n搜索摘要：\n" + ("前段资料\n" * 1800) + "\n冲突提示（自动抽取）：\n来源尾部：128K"
        clipped = _clip_tool_observation(raw, 1800, "web_research")
        self.assertIn("中间来源正文已折叠", clipped)
        self.assertIn("来源尾部：128K", clipped)

    def test_web_research_coerces_toolresult_without_raising(self):
        # 回归 #2：web_search / web_fetch 返回不可切片的 ToolResult（非图片分支）
        # 不得抛 TypeError，且应把 .text 当正文产出可 join 的 str。
        search_tr = _FakeToolResult("· 标题\n  https://example.com/doc")
        fetch_tr = _FakeToolResult("来源：https://example.com/doc\n正文：窗口上下文说明")
        with patch("tools.web_search", return_value=search_tr), \
                patch("tools.web_fetch", return_value=fetch_tr), \
                patch("tools._web_images_enabled", return_value=False), \
                patch.dict(tools.os.environ, {"DOCMIND_WEB_RESEARCH_MAX_SOURCES": "1"}, clear=False):
            out = tools.web_research("上下文窗口")
        self.assertIsInstance(out, str)
        self.assertIn("example.com/doc", out)
        self.assertIn("正文：窗口上下文说明", out)

    def test_auto_search_adds_relevant_platform_sources(self):
        base = "· 通用结果\n  摘要\n  https://example.com/general"
        platform_rows = {
            "知乎": "· 知乎评价\n  用户口碑\n  https://www.zhihu.com/question/1",
            "小红书": "· 小红书探店\n  实拍体验\n  https://www.xiaohongshu.com/explore/1",
        }
        with patch.object(tools, "_infer_search_sources", return_value=["zhihu.com", "xiaohongshu.com"]), \
                patch.object(tools, "_search_one_source", side_effect=lambda domain, _q: (
                    tools._SEARCH_SOURCE_LABELS[domain], platform_rows[tools._SEARCH_SOURCE_LABELS[domain]])):
            out = tools._search_auxiliary_sources("什么菜好吃 推荐", base)
        self.assertIn("知乎评价", out)
        self.assertIn("小红书探店", out)
        self.assertIn("通用结果", out)

    def test_batch_search_runs_queries_in_parallel_and_excludes_seen(self):
        rows = {
            "候选菜 评价": "· 菜 A 评价\n  很好吃\n  https://example.com/a",
            "候选菜 做法": "· 菜 A 做法\n  步骤\n  https://example.com/a\n"
                         "· 菜 B 做法\n  步骤\n  https://example.com/b",
        }
        with patch.object(tools, "web_search", side_effect=lambda q, _allow_aux=True: rows[q]):
            out = tools.web_search_batch(json.dumps({"queries": list(rows), "exclude": ["example.com/a"]}, ensure_ascii=False))
        self.assertIn("2 个查询并行", out)
        self.assertNotIn("example.com/a", out)
        self.assertIn("example.com/b", out)


if __name__ == "__main__":
    unittest.main()
