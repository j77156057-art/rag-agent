"""Godot 诊断解析 / 单文件校验 / project.godot 插件启用 单元测试。

有真实 Godot 可执行文件时附带跑一次端到端 --check-only（临时项目），无则跳过。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import game_workbench as gw  # noqa: E402

SAMPLE_OUTPUT = """Godot Engine v4.7.2.stable.official.ed1daf0bf
WARNING: Blend file import is enabled
res://behaviors/enemy.gd:22 - Parse Error: Identifier "speed" not declared in the current scope.
  res://values/player_stats.gd:10: ERROR: Cannot get member "hp" on base "Nil".
SCRIPT ERROR: Parse Error: Expected closing ")" after call arguments. (at res://behaviors/player.gd:88)
SCRIPT ERROR: Parse Error: Identifier "totally_undefined_symbol_zzz" not declared in the current scope.
   at: GDScript::reload (res://bad.gd:4)
ERROR: Failed to load script "res://bad.gd" with error "Parse error".
"""


class DiagnosticsParseTest(unittest.TestCase):
    def test_parse_formats(self):
        diags = gw.parse_godot_diagnostics(SAMPLE_OUTPUT)
        by_path = {(d["path"], d["line"]): d for d in diags}
        self.assertIn(("behaviors/enemy.gd", 22), by_path)
        self.assertIn(("values/player_stats.gd", 10), by_path)
        self.assertIn(("behaviors/player.gd", 88), by_path)
        self.assertIn(("bad.gd", 4), by_path)
        self.assertEqual(by_path[("behaviors/enemy.gd", 22)]["severity"], "error")
        # 无行号的 "Failed to load script" 汇总行不应产生诊断
        self.assertNotIn(("bad.gd", 0), by_path)
        # res:// 前缀被剥离、消息不空
        self.assertTrue(by_path[("values/player_stats.gd", 10)]["message"])

    def test_empty_input(self):
        self.assertEqual(gw.parse_godot_diagnostics(""), [])
        self.assertEqual(gw.parse_godot_diagnostics(None), [])


class EnablePluginTextTest(unittest.TestCase):
    def test_no_section_appends(self):
        text = 'config_version=5\n\n[application]\nconfig/name="x"\n'
        out = gw._enable_plugin_in_project_text(text)
        self.assertIn("[editor_plugins]", out)
        self.assertIn('PackedStringArray("godot_ai")', out)

    def test_existing_section_appends_item(self):
        text = '[editor_plugins]\nenabled=PackedStringArray("other_plugin")\n'
        out = gw._enable_plugin_in_project_text(text)
        self.assertIn("other_plugin", out)
        self.assertIn("godot_ai", out)

    def test_idempotent(self):
        text = '[editor_plugins]\nenabled=PackedStringArray("godot_ai")\n'
        out = gw._enable_plugin_in_project_text(text)
        self.assertEqual(out.count("godot_ai"), 1)

    def test_detect_enabled(self):
        self.assertTrue(gw._plugin_enabled('[editor_plugins]\nenabled=PackedStringArray("godot_ai")\n'))
        self.assertFalse(gw._plugin_enabled('[editor_plugins]\nenabled=PackedStringArray("other")\n'))
        self.assertFalse(gw._plugin_enabled(""))


class CheckScriptGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="docmind-gd-")

    def test_traversal_rejected(self):
        r = gw.godot_check_script(self.tmp, "../../evil.gd")
        self.assertFalse(r["ok"])
        self.assertIn("超出代码库", r["error"])

    def test_non_gd_rejected(self):
        r = gw.godot_check_script(self.tmp, "readme.txt")
        self.assertFalse(r["ok"])

    def test_missing_file(self):
        r = gw.godot_check_script(self.tmp, "nope.gd")
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["error"])


class GodotIntegrationTest(unittest.TestCase):
    """仅在本机存在 Godot 4 可执行文件时运行。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="docmind-godot-")
        self.exe = gw._resolve_engine_executable("godot", "godot")
        if not (self.exe and os.path.isfile(self.exe)):
            self.skipTest("本机未找到 Godot 可执行文件")

    def test_check_only_detects_parse_error(self):
        with open(os.path.join(self.tmp, "project.godot"), "w", encoding="utf-8", newline="\n") as f:
            f.write('config_version=5\n\n[application]\nconfig/name="docmind-tmp"\n')
        with open(os.path.join(self.tmp, "bad.gd"), "w", encoding="utf-8", newline="\n") as f:
            f.write("extends Node\n\nfunc _ready() -> void:\n\tvar x = totally_undefined_symbol_zzz\n")
        r = gw.godot_check_script(self.tmp, "bad.gd", timeout=150)
        self.assertFalse(r.get("ok"))
        self.assertTrue(
            any(d["path"] == "bad.gd" for d in r.get("all_diagnostics", [])),
            f"应解析出 bad.gd 诊断，实际：{r.get('all_diagnostics')} / {r.get('error', '')}",
        )


if __name__ == "__main__":
    unittest.main()
