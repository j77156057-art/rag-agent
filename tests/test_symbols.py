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


class JavaTests(unittest.TestCase):
    def extract(self, text):
        return S.extract(text, "C.java")

    def test_class_header_methods_fields_constructors(self):
        src = (
            "package cn.itcast;\n"                       # 1
            "public class Player extends Object {\n"     # 2
            "    private static final int MAX = 10;\n"   # 3
            "    private String name;\n"                 # 4
            "    public Player(String name) {\n"         # 5
            "        this.name = name;\n"                # 6
            "    }\n"                                    # 7
            "    public Map<String, Integer> scores(String... ids)\n"  # 8
            "            throws IOException {\n"         # 9
            '        String brace = "}{";\n'             # 10
            "        return null;\n"                     # 11
            "    }\n"                                    # 12
            "    public void run() {\n"                  # 13
            "        if (x) { doIt(); }\n"               # 14
            "        for (int k = 0; k < 2; k++) { x(k); }\n"  # 15
            "        Runnable r = new Runnable() {\n"    # 16
            "            public void run() { go(); }\n"  # 17
            "        };\n"                               # 18
            "    }\n"                                    # 19
            "}\n"                                        # 20
        )
        env = self.extract(src)
        self.assertEqual(env["class_name"], "Player")
        self.assertEqual(env["extends"], "Object")
        by = {(s["name"], s["start"]): s for s in env["symbols"]}
        self.assertEqual(by[("Player", 2)]["kind"], S.CLASS)
        self.assertEqual((by[("Player", 2)]["start"], by[("Player", 2)]["end"]), (2, 20))
        # static final 常量 / 实例字段
        self.assertEqual(by[("MAX", 3)]["kind"], S.CONST)
        self.assertEqual(by[("name", 4)]["kind"], S.VAR)
        # 构造器
        ctor = by[("Player", 5)]
        self.assertEqual(ctor["kind"], S.FUNCTION)
        self.assertEqual(ctor["detail"], "constructor")
        self.assertEqual(ctor["parent"], "Player")
        self.assertEqual((ctor["start"], ctor["end"]), (5, 7))
        self.assertIn("Player(String name)", ctor["signature"])
        # 跨行签名 + throws + 字符串里的花括号不影响边界
        sc = by[("scores", 8)]
        self.assertEqual((sc["start"], sc["end"]), (8, 12))
        self.assertEqual(sc["detail"], "Map<String, Integer>")
        self.assertIn("throws IOException", sc["signature"])
        # 方法闭合正确；控制语句、局部变量、匿名类方法均不入符号表
        self.assertEqual((by[("run", 13)]["start"], by[("run", 13)]["end"]), (13, 19))
        names = [s["name"] for s in env["symbols"]]
        self.assertNotIn("doIt", names)
        self.assertNotIn("go", names)
        self.assertNotIn("r", names)
        self.assertEqual(len([n for n in names if n == "run"]), 1)  # 匿名类 run 未混入

    def test_interface_abstract_and_default_const(self):
        src = (
            "public interface Repo {\n"                      # 1
            "    int LIMIT = 1;\n"                           # 2 隐式 public static final
            "    void save(String x) throws Exception;\n"   # 3 抽象方法
            "    default int one() {\n"                     # 4
            "        return 1;\n"                            # 5
            "    }\n"                                        # 6
            "}\n"                                            # 7
        )
        env = self.extract(src)
        by = {(s["name"], s["start"]): s for s in env["symbols"]}
        self.assertEqual(by[("LIMIT", 2)]["kind"], S.CONST)
        save = by[("save", 3)]
        self.assertEqual(save["kind"], S.FUNCTION)
        self.assertEqual((save["start"], save["end"]), (3, 3))
        self.assertEqual(save["detail"], "void")
        one = by[("one", 4)]
        self.assertEqual((one["start"], one["end"]), (4, 6))

    def test_enum_constants_then_members(self):
        src = (
            "public enum Color {\n"                          # 1
            "    RED,\n"                                     # 2
            "    GREEN(true),\n"                             # 3
            "    BLUE {\n"                                   # 4
            "        @Override public String h() {\n"        # 5 匿名类体方法（不收）
            '            return "b";\n'                      # 6
            "        }\n"                                    # 7
            "    };\n"                                       # 8
            "    public String h() {\n"                      # 9
            '        return "x";\n'                          # 10
            "    }\n"                                        # 11
            "}\n"                                            # 12
        )
        env = self.extract(src)
        by = {(s["name"], s["start"]): s for s in env["symbols"]}
        self.assertEqual(env["symbols"][0]["kind"], S.ENUM)
        self.assertEqual((by[("RED", 2)]["start"], by[("RED", 2)]["end"]), (2, 2))
        self.assertEqual(by[("GREEN", 3)]["kind"], S.CONST)
        blue = by[("BLUE", 4)]
        self.assertEqual((blue["start"], blue["end"]), (4, 8))
        h = by[("h", 9)]
        self.assertEqual((h["start"], h["end"]), (9, 11))
        self.assertEqual(len([s for s in env["symbols"] if s["name"] == "h"]), 1)

    def test_nested_class_parent_and_locals_excluded(self):
        src = (
            "public class Outer {\n"                 # 1
            "    int outerField;\n"                  # 2
            "    static class Inner {\n"             # 3
            "        int innerField;\n"              # 4
            "        void m() {\n"                   # 5
            "            int local = 1;\n"           # 6
            "        }\n"                            # 7
            "    }\n"                                # 8
            "    void outerM() {}\n"                 # 9
            "}\n"                                    # 10
        )
        env = self.extract(src)
        by = {(s["name"], s["start"]): s for s in env["symbols"]}
        self.assertEqual(by[("Inner", 3)]["parent"], "Outer")
        self.assertEqual(by[("innerField", 4)]["parent"], "Inner")
        self.assertEqual(by[("m", 5)]["parent"], "Inner")
        self.assertEqual(by[("outerField", 2)]["parent"], "Outer")
        self.assertEqual(by[("outerM", 9)]["parent"], "Outer")
        self.assertNotIn(("local", 6), by)

    def test_javadoc_annotation_start_line(self):
        src = (
            "public class C {\n"            # 1
            "    /**\n"                     # 2
            "     * 计算结果\n"             # 3
            "     */\n"                     # 4
            "    @Override\n"               # 5
            "    public int f() {\n"        # 6
            "        return 1;\n"           # 7
            "    }\n"                       # 8
            "    @Override public void g() {}\n"  # 9 注解与声明同行
            "}\n"                           # 10
        )
        env = self.extract(src)
        by = {(s["name"], s["start"]): s for s in env["symbols"]}
        f = by[("f", 5)]  # start 跳到注解行
        self.assertIn("计算结果", f["doc"])
        self.assertEqual((f["start"], f["end"]), (5, 8))
        self.assertIn(("g", 9), by)

    def test_brace_in_comment_and_string(self):
        src = (
            "public class C {\n"                          # 1
            "    // 伪花括号 } {\n"                        # 2
            "    /* 跨块 }\n"                             # 3
            "       仍在注释 { */\n"                       # 4
            '    String s = "a}{b"; // }\n'               # 5
            "    public void f() {\n"                     # 6
            "    }\n"                                     # 7
            "}\n"                                          # 8
        )
        env = self.extract(src)
        by = {(s["name"], s["start"]): s for s in env["symbols"]}
        self.assertEqual((by[("C", 1)]["start"], by[("C", 1)]["end"]), (1, 8))
        self.assertEqual((by[("f", 6)]["start"], by[("f", 6)]["end"]), (6, 7))


class JavaChunkNamingTests(unittest.TestCase):
    """ingest._symbol_name 对启发式切块的 Java 定义行命名。"""

    def test_constructor_static_final_method_field(self):
        from ingest import _symbol_name

        def name(line):
            return _symbol_name(line, ".java")

        self.assertEqual(name("    public Player(String name) {"), "Player")
        self.assertEqual(name("    private AIApiClient(Context context) {"), "AIApiClient")
        self.assertEqual(name('    private static final String KEY = "x";'), "KEY")
        self.assertEqual(
            name("    private static final Map<ApiProvider, String> API_URLS = new HashMap<>();"),
            "API_URLS",
        )
        self.assertEqual(name("    public void play(int id) {"), "play")
        self.assertEqual(name("    public List<String> names() {"), "names")
        # 返回大写类型的普通方法不能被误判成构造器
        self.assertEqual(name("    public Song getCurrentSong() {"), "getCurrentSong")
        # 无修饰字段仍保持无名
        self.assertEqual(name("    private String url;"), "")


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
