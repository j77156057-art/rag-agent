"""Wave9 回归：把 harness 自己的工具经 MCP 暴露出去时的边界。

要紧的是三件事：默认只读、任意情况下都不暴露命令执行与越界文件操作、以及
【路径沙箱不会因为换了入口就失效】。最后一条用真实调用打穿，而不是只测常量表。
"""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from agent_runtime import mcp_server  # noqa: E402


def _mcp_available():
    try:
        import mcp.server  # noqa: F401
        from mcp.types import ToolAnnotations  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


MCP_OK = _mcp_available()


class SelectionCases(unittest.TestCase):
    def test_default_is_read_only(self):
        names = mcp_server.selected_tools()
        self.assertIn("dev_find_references", names)
        self.assertIn("dev_lanes", names)
        for name in mcp_server.WRITE_TOOLS:
            self.assertNotIn(name, names)

    def test_writes_need_the_explicit_flag(self):
        names = mcp_server.selected_tools(allow_writes=True)
        self.assertIn("dev_patch", names)
        self.assertIn("dev_move", names)

    def test_dangerous_tools_never_exposed_even_with_flag(self):
        names = set(mcp_server.selected_tools(allow_writes=True))
        for name in mcp_server.NEVER_EXPOSED:
            self.assertNotIn(name, names)
        # 这几条是整个安全模型的地基，清单里必须一直在
        for name in ("run_command", "python_exec", "dev_mcp_call",
                     "read_external_file", "delete_external_file", "install_tool"):
            self.assertIn(name, mcp_server.NEVER_EXPOSED)

    def test_only_can_narrow_but_not_widen(self):
        names = mcp_server.selected_tools(only="grep,dev_git_log")
        self.assertEqual(set(names), {"grep", "dev_git_log"})
        self.assertEqual(mcp_server.selected_tools(only="run_command"), ())
        self.assertEqual(mcp_server.selected_tools(True, only="run_command,dev_patch"),
                         ("dev_patch",))

    def test_every_exposed_name_exists_in_the_registry(self):
        import tools
        for name in mcp_server.selected_tools(allow_writes=True):
            self.assertIn(name, tools.TOOLS, name)
            self.assertTrue(callable(tools.TOOLS[name].get("func")), name)
            self.assertTrue(str(tools.TOOLS[name].get("description") or ""), name)


@unittest.skipUnless(MCP_OK, "mcp SDK 不可用")
class ServerCases(unittest.TestCase):
    def setUp(self):
        self._prev = config.get_runtime("code_root")
        self.tmp = tempfile.mkdtemp(prefix="docmind_mcp_")
        for rel, body in (("a.vue", "<template><p>hi</p></template>\n"),
                          ("src/mod.py", "VALUE = 1\n"),
                          ("src/util.ts", "export const VALUE = 1;\n")):
            full = os.path.join(self.tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(body)

    def tearDown(self):
        config.set_runtime("code_root", self._prev)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **kw):
        server, root = mcp_server.build_server(self.tmp, **kw)
        return server, root

    def test_registered_set_matches_selection(self):
        server, _ = self.build()
        listed = asyncio.run(server.list_tools())
        self.assertEqual({row.name for row in listed}, set(mcp_server.selected_tools()))

    def test_writes_appear_only_when_enabled(self):
        names = {row.name for row in asyncio.run(self.build()[0].list_tools())}
        self.assertNotIn("dev_patch", names)
        names = {row.name for row in
                 asyncio.run(self.build(allow_writes=True)[0].list_tools())}
        self.assertIn("dev_patch", names)

    def test_annotations_declare_read_only(self):
        listed = {row.name: row for row in asyncio.run(self.build()[0].list_tools())}
        # pydantic 模型是 snake_case（camelCase 只是序列化别名）
        self.assertTrue(listed["grep"].annotations.read_only_hint)
        self.assertFalse(listed["grep"].annotations.destructive_hint)
        self.assertTrue(listed["dev_glob"].description)
        write_listing = {row.name: row for row in
                         asyncio.run(self.build(allow_writes=True)[0].list_tools())}
        self.assertFalse(write_listing["dev_patch"].annotations.read_only_hint)
        self.assertTrue(write_listing["dev_patch"].annotations.destructive_hint)

    def test_project_binding_sets_code_root(self):
        server, root = self.build()
        self.assertEqual(os.path.normpath(root), os.path.normpath(self.tmp))
        self.assertEqual(os.path.normpath(config.get_runtime("code_root")),
                         os.path.normpath(self.tmp))

    def _text(self, result):
        blocks = getattr(result, "content", None) or []
        return "\n".join(getattr(row, "text", "") for row in blocks)

    def test_real_call_through_mcp_finds_files(self):
        server, _ = self.build()
        out = self._text(asyncio.run(server.call_tool("dev_glob", {"input": "*.vue"})))
        self.assertIn("a.vue", out)

    def test_path_sandbox_survives_the_new_entry_point(self):
        # 换个入口不等于绕过护栏：项目外的文件必须被拒，且不回显内容
        server, _ = self.build()
        sibling = tempfile.mkdtemp(prefix="docmind_mcp_out_")
        try:
            outside = os.path.join(sibling, "outside.txt")
            with open(outside, "w", encoding="utf-8") as fh:
                fh.write("SECRET-TOKEN-DO-NOT-READ\n")
            for payload in ({"input": outside}, {"input": "../outside.txt"},
                            {"input": "./../../outside.txt"}):
                out = self._text(asyncio.run(server.call_tool("read_file", payload)))
                self.assertNotIn("SECRET-TOKEN-DO-NOT-READ", out, str(payload))
                self.assertTrue(any(word in out for word in ("拒绝", "越界", "不存在", "无法")),
                                "%s 应被拒：%s" % (payload, out[:160]))
        finally:
            shutil.rmtree(sibling, ignore_errors=True)

    def test_tool_exception_becomes_text_not_a_crash(self):
        def boom(arg=""):
            raise RuntimeError("内部炸了")
        wrapped = mcp_server._wrap("boom", boom, "test")
        self.assertIn("内部炸了", wrapped("x"))

    def test_non_string_results_are_stringified(self):
        wrapped = mcp_server._wrap("num", lambda arg="": 42, "test")
        self.assertEqual(wrapped(""), "42")


class CommandLineCases(unittest.TestCase):
    def test_list_flag_does_not_start_a_transport(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = mcp_server.main(["--list"])
        self.assertEqual(code, 0)
        printed = buffer.getvalue().split()
        self.assertIn("dev_git_log", printed)
        self.assertNotIn("dev_patch", printed)
        self.assertNotIn("run_command", printed)

    def test_list_with_writes_shows_patch(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            mcp_server.main(["--list", "--allow-writes"])
        self.assertIn("dev_patch", buffer.getvalue().split())


if __name__ == "__main__":
    unittest.main()
