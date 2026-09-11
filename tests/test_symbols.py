"""symbols.py 符号提取单测（python -B -m unittest tests.test_symbols）。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import symbols as S


class GDScriptTests(unittest.TestCase):
    def extract(self, text):
        return S.extract("\ufeff" + text, "x.gd")  # 顺带验证 BOM

    def names(self, env, kind=None):
        return [s["name"] for s in env["symbols"] if kind is None or s["kind"] == kind]

    def test_header_class_name_extends_doc(self):
        env = self.extract("## 暴击数值表\nclass_name CritConfig\nextends RefCounted\n")
        self.assertEqual(env["class_name"], "CritConfig")
        self.assertEqual(env["extends"], "RefCounted")
        self.assertIn("暴击数值表", env["doc"])

    def test_extends_then_header_doc(self):
        # Godot 样例形态：extends 在上，## 类说明在下，空行与成员隔开 → 类文档
        env = self.extract("extends CharacterBody3D\n## 敌人 AI\n\n@export var speed: float = 3.0\n")
        self.assertEqual(env["extends"], "CharacterBody3D")
        self.assertIn("敌人 AI", env["doc"])
        var = [s for s in env["symbols"] if s["name"] == "speed"][0]
        self.assertEqual(var["kind"], S.VAR)
        self.assertEqual(var["detail"], "float")
        self.assertEqual(var["start"], 4)  # 跳转到 @export 行
        self.assertEqual(var["doc"], "")  # 空行隔开，类文档不挂到成员

    def test_doc_directly_above_first_member_belongs_to_member(self):
        # 无空行紧贴首条声明：Godot 约定归该声明
        env = self.extract("extends Node\n## 速度配置\n@export var speed: float = 3.0\n")
        self.assertEqual(env["doc"], "")
        var = [s for s in env["symbols"] if s["name"] == "speed"][0]
        self.assertIn("速度配置", var["doc"])

    def test_const_func_end_lines(self):
        src = "class_name C\n\nconst A: int = 1\nconst B: int = 2\n\nstatic func f(x: int) -> int:\n    return x + A\n"
        env = self.extract(src)
        f = [s for s in env["symbols"] if s["name"] == "f"][0]
        self.assertEqual((f["start"], f["end"]), (6, 7))
        self.assertEqual(f["detail"], "-> int")
        self.assertEqual(self.names(env, S.CONST), ["A", "B"])

    def test_signal_and_multiline_enum(self):
        src = (
            "extends Node\n"
            "signal hp_changed(new_hp: int)\n"
            "enum State {\n    IDLE,\n    RUN,\n    DEAD,\n}\n"
            "signal simple\n"
        )
        env = self.extract(src)
        sig = [s for s in env["symbols"] if s["kind"] == S.SIGNAL]
        self.assertEqual([s["name"] for s in sig], ["hp_changed", "simple"])
        en = [s for s in env["symbols"] if s["kind"] == S.ENUM][0]
        self.assertEqual((en["start"], en["end"]), (3, 7))
        self.assertEqual(en["detail"], "IDLE, RUN, DEAD")

    def test_inner_class_parent_and_locals_excluded(self):
        src = (
            "class_name Outer\n"
            "\n"
            "class Inner:\n"
            "    extends RefCounted\n"
            "    var weight: float = 1.0\n"
            "    func bump() -> void:\n"
            "        var local_var := 9\n"
            "        pass\n"
            "\n"
            "func outer_fn() -> void:\n"
            "    pass\n"
        )
        env = self.extract(src)
        by = {s["name"]: s for s in env["symbols"]}
        self.assertEqual(by["Inner"]["kind"], S.CLASS)
        self.assertEqual(by["weight"]["parent"], "Inner")
        self.assertEqual(by["bump"]["parent"], "Inner")
        self.assertEqual((by["bump"]["start"], by["bump"]["end"]), (6, 8))
        self.assertEqual(by["outer_fn"]["parent"], "")
        self.assertNotIn("local_var", by)  # 函数体内局部变量不是符号

    def test_export_group_and_multiline_var(self):
        src = (
            '@export_group("战斗")\n'
            "@export var hp: int = 10\n"
            "var opts := {\n"
            '    "a": 1,\n'
            '    "b": 2,\n'
            "}\n"
        )
        env = self.extract(src)
        by = {s["name"]: s for s in env["symbols"]}
        self.assertIn("战斗", by)
        self.assertEqual(by["战斗"]["kind"], S.GROUP)
        self.assertEqual(by["opts"]["end"], 6)  # 跨行字典续行

    def test_hash_in_string_not_comment(self):
        src = 'extends Node\nvar url: String = "https://x.com/a#b"\nfunc f() -> void:\n    pass\n'
        env = self.extract(src)
        self.assertEqual(self.names(env, S.VAR), ["url"])
        self.assertEqual(self.names(env, S.FUNCTION), ["f"])

    def test_doc_above_symbol(self):
        src = "extends Node\n## 二倍暴击\n## 注意封顶\nstatic func crit() -> float:\n    return 2.0\n"
        env = self.extract(src)
        f = [s for s in env["symbols"] if s["name"] == "crit"][0]
        self.assertIn("二倍暴击", f["doc"])
        self.assertIn("封顶", f["doc"])


class PythonTests(unittest.TestCase):
    def test_class_methods_decorator_assignment(self):
        src = (
            "MOD = 3\n"
            "X_CAP = 9\n"
            "\n"
            "class A:\n"
            '    """类文档"""\n'
            "    @classmethod\n"
            "    def m(cls, x):\n"
            "        return x\n"
            "\n"
            "def top():\n"
            "    pass\n"
        )
        env = S.extract(src, "a.py")
        by = {s["name"]: s for s in env["symbols"]}
        self.assertEqual(by["MOD"]["kind"], S.CONST)
        self.assertEqual(by["X_CAP"]["kind"], S.CONST)
        self.assertEqual(by["A"]["kind"], S.CLASS)
        self.assertIn("类文档", by["A"]["doc"])
        self.assertEqual(by["m"]["parent"], "A")
        self.assertEqual(by["m"]["start"], 6)  # 含装饰器行
        self.assertIn("m(cls, x)", by["m"]["signature"])
        self.assertEqual(by["top"]["parent"], "")

    def test_syntax_error_returns_empty_envelope(self):
        env = S.extract("def broken(:\n", "b.py")
        self.assertEqual(env["symbols"], [])
        self.assertEqual(env["lang"], "python")


class SceneTests(unittest.TestCase):
    def test_tscn_nodes_and_resources(self):
        src = (
            '[gd_scene load_steps=2]\n'
            '[ext_resource type="Script" path="res://ui/hud.gd" id="1"]\n'
            '[node name="Root" type="Control"]\n'
            'offset_right = 40.0\n'
            '[node name="Label" type="Label" parent="."]\n'
        )
        env = S.extract(src, "x.tscn")
        nodes = [(s["name"], s["kind"], s["detail"]) for s in env["symbols"]]
        self.assertIn(("Root", S.NODE, "Control"), nodes)
        self.assertIn(("Label", S.NODE, "Label"), nodes)
        self.assertIn(("hud.gd", S.RESOURCE, "Script"), nodes)
        root = [s for s in env["symbols"] if s["name"] == "Root"][0]
        self.assertEqual(root["end"], 4)  # L3 Root 段覆盖 L4 属性行


class GenericTests(unittest.TestCase):
    def test_js_function_class(self):
        src = "export function add(a, b) {\n  return a + b\n}\nclass Foo {}\n"
        env = S.extract(src, "x.js")
        by = {s["name"]: s for s in env["symbols"]}
        self.assertEqual(by["add"]["kind"], S.FUNCTION)
        self.assertEqual((by["add"]["start"], by["add"]["end"]), (1, 3))
        self.assertEqual(by["Foo"]["kind"], S.CLASS)

    def test_lua_function_end(self):
        src = "function M.hello()\n  print('hi')\nend\n"
        env = S.extract(src, "x.lua")
        s = [x for x in env["symbols"] if x["name"] == "hello"][0]
        self.assertEqual((s["start"], s["end"]), (1, 3))


class CacheTests(unittest.TestCase):
    def test_file_cache_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.gd")
            with open(p, "w", encoding="utf-8") as f:
                f.write("extends Node\nfunc a() -> void:\n    pass\n")
            env1 = S.file_symbols(p)
            self.assertEqual([x["name"] for x in env1["symbols"]], ["a"])
            import time
            time.sleep(0.02)
            with open(p, "w", encoding="utf-8") as f:
                f.write("extends Node\nfunc b() -> void:\n    pass\n")
            env2 = S.file_symbols(p)
            self.assertEqual([x["name"] for x in env2["symbols"]], ["b"])


if __name__ == "__main__":
    unittest.main()
