"""回归 #3：llm._doc_context_resolver 不得因工具返回值非 str（ToolResult）而抛错。

纯单测：把伪造的 tools 模块挂进 sys.modules，注入返回不可切片对象（ToolResult 形状）
的 web_search / web_fetch，断言 _doc_context_resolver 不抛、并把 .text 当正文返回。
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm


class _FakeToolResult:
    """带 `.text` 字符串，但自身不可切片 / 不可下标 / 不可成员判断。"""

    def __init__(self, text):
        self.text = text

    def __getitem__(self, item):
        raise TypeError("'ToolResult' object is not subscriptable")

    def __contains__(self, item):
        raise TypeError("'ToolResult' object is not iterable")


def _fake_tools(search_return, fetch_return):
    mod = types.ModuleType("tools")
    mod.web_search = lambda query: search_return
    mod.web_fetch = lambda url: fetch_return
    return mod


class DocContextResolverTests(unittest.TestCase):
    def test_coerces_toolresult_without_raising(self):
        # search 返回 ToolResult（含链接）→ 解析出 URL → fetch 也返回 ToolResult → 取 .text
        with patch.dict(sys.modules, {"tools": _fake_tools(
                _FakeToolResult("· 标题\n  https://example.com/d"),
                _FakeToolResult("正文：窗口上下文说明"))}):
            out = llm._doc_context_resolver("上下文窗口")
        self.assertIsInstance(out, str)
        self.assertEqual(out, "正文：窗口上下文说明")

    def test_search_without_links_returns_text(self):
        # search 返回 ToolResult 但无链接 → 直接返回规整后的文本（而不是对象/抛错）
        with patch.dict(sys.modules, {"tools": _fake_tools(
                _FakeToolResult("无链接摘要，仅说明文字"), _FakeToolResult("unused"))}):
            out = llm._doc_context_resolver("q")
        self.assertIsInstance(out, str)
        self.assertEqual(out, "无链接摘要，仅说明文字")

    def test_search_raises_returns_empty_string(self):
        def _boom(_query):
            raise RuntimeError("network down")

        mod = types.ModuleType("tools")
        mod.web_search = _boom
        mod.web_fetch = lambda url: "unused"
        with patch.dict(sys.modules, {"tools": mod}):
            self.assertEqual(llm._doc_context_resolver("q"), "")


if __name__ == "__main__":
    unittest.main()
