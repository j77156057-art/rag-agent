"""tree-sitter 精确引用回归：TS/TSX/JS 与 Vue SFC。

这里守的是「相对正则的全部价值」：注释、字符串字面量、模板注释里的同名文本
不能被当成引用；Vue 的行号必须映射回文件真实行。
语法缺失时整模块回退 None（调用方落回正则），也有一项用例钉住。
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent_runtime import code_intel, ts_index  # noqa: E402

TS_READY = ts_index.available()


def lines_of(rows):
    return sorted({row["line"] for row in rows})


TS_SRC = '''\
// foo only lives in this comment
const label = "foo inside a string literal";
export function foo(a: number): number { return baz(a); }
function baz(x: number) { return foo(x) + foo(1); }
class Holder { run() { return foo(2); } }
const obj = { key: foo(3) };
const alias = foo;
interface Shape { foo: string }
const live = { state: { foo: 1 } };
const deep = live.state.foo;
'''

VUE_SRC = '''\
<template>
  <div>{{ greeting }}</div>
  <!-- greeting must not count as a reference -->
  <button @click="bump">go</button>
</template>
<script setup lang="ts">
const greeting = "hi";
function bump() { return greeting; }
</script>
'''


@unittest.skipUnless(TS_READY, "tree-sitter / TS 语法不可用")
class TsMatchesCases(unittest.TestCase):
    def run_ts(self, rel, source, symbol="foo"):
        out = ts_index.ts_matches(rel, source, symbol)
        self.assertIsNotNone(out, f"不应回退：{ts_index.import_error()}")
        defs, refs, notes = out
        return defs, refs, notes

    def test_typescript_definitions_and_references(self):
        defs, refs, _ = self.run_ts("src/a.ts", TS_SRC)
        self.assertEqual(lines_of(defs), [3, 8])
        self.assertEqual(lines_of([row for row in refs if row["kind"] != "str"]),
                         [4, 5, 6, 7, 10])

    def test_object_literal_key_is_low_confidence_not_a_reference(self):
        _, refs, _ = self.run_ts("src/a.ts", TS_SRC)
        kinds = {row["line"]: row["kind"] for row in refs}
        self.assertEqual(kinds.get(9), "str")   # const live = { state: { foo: 1 } }
        self.assertEqual(kinds.get(10), "attr")  # const deep = live.state.foo

    def test_comment_and_string_never_count_as_references(self):
        _, refs, _ = self.run_ts("src/a.ts", TS_SRC)
        joined = " ".join(row["code"] for row in refs)
        self.assertNotIn("only lives in this comment", joined)
        self.assertNotIn("inside a string literal", joined)

    def test_property_access_is_marked_as_attribute(self):
        _, refs, _ = self.run_ts("src/a.ts", TS_SRC)
        kinds = {row["line"]: row["kind"] for row in refs}
        self.assertEqual(kinds.get(10), "attr")

    def test_interface_member_is_a_definition_not_a_reference(self):
        defs, refs, _ = self.run_ts("src/a.ts", TS_SRC)
        self.assertIn(8, lines_of(defs))
        self.assertNotIn(8, lines_of(refs))

    def test_jsx_element_counts_as_reference(self):
        src = ('function Cockpit() { return <Cockpit/>; }\n'
               'export default function App() { return <Cockpit/>; }\n')
        defs, refs, _ = self.run_ts("src/App.tsx", src, "Cockpit")
        self.assertEqual(lines_of(defs), [1])
        self.assertIn(2, lines_of(refs))

    def test_plain_javascript(self):
        src = 'function tick() {}\ntick();\n// tick in comment\nvar tick2 = tick;\n'
        defs, refs, _ = self.run_ts("src/x.js", src, "tick")
        self.assertEqual(lines_of(defs), [1])
        self.assertEqual(lines_of(refs), [2, 4])

    def test_vue_maps_lines_back_to_the_file(self):
        defs, refs, notes = self.run_ts("src/View.vue", VUE_SRC, "greeting")
        self.assertEqual(lines_of(defs), [7])
        self.assertIn(2, lines_of(refs))

    def test_vue_template_html_comment_is_not_a_reference(self):
        _, refs, _ = self.run_ts("src/View.vue", VUE_SRC, "greeting")
        self.assertNotIn(3, lines_of(refs))

    def test_vue_template_binding_is_found(self):
        defs, refs, _ = self.run_ts("src/View.vue", VUE_SRC, "bump")
        self.assertEqual(lines_of(defs), [8])
        self.assertIn(4, lines_of(refs))

    def test_vue_without_script_block(self):
        _, refs, notes = self.run_ts("src/Plain.vue", "<template><p>greeting</p></template>\n",
                                     "greeting")
        self.assertEqual(lines_of(refs), [1])
        self.assertTrue(any("没有 <script>" in n for n in notes))

    def test_python_and_unknown_extensions_decline(self):
        self.assertIsNone(ts_index.ts_matches("a.py", "def foo(): pass", "foo"))
        self.assertIsNone(ts_index.ts_matches("README.md", "foo", "foo"))

    def test_falls_back_when_grammar_missing(self):
        real = ts_index._load
        ts_index._load = lambda flavor: (_ for _ in ()).throw(ImportError("no grammar"))
        try:
            self.assertIsNone(ts_index.ts_matches("src/a.ts", TS_SRC, "foo"))
            self.assertIn("no grammar", ts_index.import_error())
        finally:
            ts_index._load = real


class FindReferencesIntegration(unittest.TestCase):
    """code_intel.find_references 的语言分发：Python 与 TS/Vue 混排。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="docmind_ts_int_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content):
        full = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_mixed_tree_uses_the_precise_path(self):
        if not TS_READY:
            self.skipTest("tree-sitter 不可用")
        self.write("pkg/helper.ts", TS_SRC)
        self.write("pkg/view.vue", VUE_SRC)
        res = code_intel.find_references(self.tmp, "foo")
        self.assertTrue(res["ok"], res["error"])
        self.assertEqual(res["ts_indexed_files"], 2)
        self.assertTrue(any("tree-sitter" in n for n in res["notes"]))
        self.assertEqual(res["scanned"], 2)

    def test_render_mentions_precision(self):
        if not TS_READY:
            self.skipTest("tree-sitter 不可用")
        self.write("pkg/helper.ts", TS_SRC)
        text = code_intel.render_references(code_intel.find_references(self.tmp, "foo"))
        self.assertIn("helper.ts", text)


class AvailabilityContract(unittest.TestCase):
    def test_ext_set_covers_the_active_surfaces(self):
        for ext in (".ts", ".tsx", ".js", ".jsx", ".vue"):
            self.assertIn(ext, ts_index.TS_EXTS)

    def test_script_range_parsing(self):
        blocks, covered = ts_index._script_ranges(VUE_SRC)
        # (代码起始行 = <script> 标签行 + 1, 代码结束行 = </script> 前一行, flavor)
        self.assertEqual(blocks, [(7, 8, "typescript")])
        self.assertEqual(covered, [(7, 8)])


if __name__ == "__main__":
    unittest.main()
