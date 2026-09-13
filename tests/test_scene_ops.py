# -*- coding: utf-8 -*-
"""场景画布 2.0 单元测试（scene_runtime 的解析 / 图模型 / 受控编辑 / 运行时检索）。

覆盖：
* 行块解析的边界——``groups=[...]`` 数组属性里含 ``]``、属性行含 ``=``、
  ``Vector2`` 类型名里的数字不能被当成坐标（这两个都是真实踩过的坑）；
* 图模型——层级/脚本/实例化三类边、深度、position 来源、实例子树覆写标记；
* 受控编辑——add/rename/reparent/duplicate/set_props/move/restore 及 undo 可逆性；
* 护栏——根节点/受保护路径/非法名/移入自身子孙/结构非法自动回滚；
* 运行时事件筛选、会话切分与清空（清空引擎日志用续读游标，不截断正在写的文件）。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scene_runtime as sr  # noqa: E402
from workbench_fs import FsError  # noqa: E402

SCENE = '''[gd_scene load_steps=4 format=3 uid="uid://abc123"]

[ext_resource type="Script" path="res://behaviors/player.gd" id="1_pl"]
[ext_resource type="PackedScene" path="res://scenes/Bullet.tscn" id="2_bul"]
[ext_resource type="Texture2D" path="res://assets/hero.png" id="3_tex"]

[sub_resource type="CircleShape2D" id="CircleShape2D_1"]
radius = 12.0

[node name="Main" type="Node2D"]

[node name="Player" type="CharacterBody2D" parent="." groups=["hero", "actors"]]
position = Vector2(100, 200)
script = ExtResource("1_pl")

[node name="Sprite" type="Sprite2D" parent="Player"]
texture = ExtResource("3_tex")

[node name="Gun" type="Node2D" parent="Player"]
position = Vector2(8, 0)

[node name="Enemy" parent="." instance=ExtResource("2_bul")]
position = Vector2(400, 120)
'''


class SceneFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name.replace("\\", "/")
        self.rel = "scenes/Main.tscn"
        self._write(self.rel, SCENE)
        os.makedirs(os.path.join(self.root, "behaviors"), exist_ok=True)
        with open(os.path.join(self.root, "behaviors", "player.gd"), "w", encoding="utf-8") as f:
            f.write("extends CharacterBody2D\n")
        os.makedirs(os.path.join(self.root, "scenes"), exist_ok=True)
        with open(os.path.join(self.root, "scenes", "Bullet.tscn"), "w", encoding="utf-8") as f:
            f.write('[gd_scene format=3]\n\n[node name="Bullet" type="Node2D"]\n')

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, content):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)

    def _read(self):
        with open(os.path.join(self.root, *self.rel.split("/")), encoding="utf-8-sig") as f:
            return f.read()

    def _node(self, graph, node_id):
        for node in graph["nodes"]:
            if node["id"] == node_id:
                return node
        return None

    def _edges(self, graph, kind):
        return [(e["source"], e["target"]) for e in graph["edges"] if e["kind"] == kind]


class ParsingTests(SceneFixture):
    def test_header_with_array_attribute_is_not_swallowed(self):
        """groups=[...] 里含 ] —— 朴素正则会把这行判成属性行，整个节点消失。"""
        graph = sr.scene_graph(self.root, self.rel)
        player = self._node(graph, "Player")
        self.assertIsNotNone(player, "带 groups 的节点必须被识别为节点")
        self.assertEqual(player["groups"], ["hero", "actors"])
        self.assertEqual(player["parent"], ".")
        self.assertEqual(sorted(n["id"] for n in graph["nodes"]),
                         [".", "Enemy", "Player", "Player/Gun", "Player/Sprite"])

    def test_vector_parse_ignores_type_digit(self):
        """Vector2(100, 200) 的类型名里有个 2，解析坐标时必须先剥掉类型前缀。"""
        graph = sr.scene_graph(self.root, self.rel)
        self.assertEqual(self._node(graph, "Player")["position"], [100.0, 200.0])
        self.assertEqual(self._node(graph, "Player/Gun")["position"], [8.0, 0.0])
        self.assertEqual(self._node(graph, ".")["position"], None)

    def test_graph_edges_and_files(self):
        graph = sr.scene_graph(self.root, self.rel)
        self.assertEqual(sorted(self._edges(graph, "hierarchy")),
                         [(".", "Enemy"), (".", "Player"), ("Player", "Player/Gun"),
                          ("Player", "Player/Sprite")])
        self.assertEqual(self._edges(graph, "script"), [("Player", "ext:1_pl")])
        self.assertEqual(self._edges(graph, "instance"), [("Enemy", "ext:2_bul")])
        # 贴图这类"参考资源"也要单独成边：排查"这张图被谁用着"时用得上
        self.assertEqual(self._edges(graph, "reference"), [("Player/Sprite", "ext:3_tex")])
        files = {f["id"]: f for f in graph["files"]}
        self.assertEqual(files["ext:1_pl"]["rel"], "behaviors/player.gd")
        self.assertEqual(files["ext:1_pl"]["kind"], "script")
        self.assertEqual(files["ext:1_pl"]["used"], 1)
        self.assertEqual(files["ext:3_tex"]["kind"], "resource")
        self.assertFalse(files["ext:3_tex"]["orphan"], "被 Sprite.texture 引用的贴图不算孤儿资源")
        self.assertEqual(files["ext:3_tex"]["nodes"], ["Player/Sprite"])
        self.assertIn("ext:3_tex", self._node(graph, "Player/Sprite")["resource_ids"])
        self.assertEqual(graph["stats"]["max_depth"], 1)
        self.assertEqual(graph["stats"]["orphans"], 0)
        self.assertFalse(graph["read_only"])

    def test_unused_external_resource_is_marked_orphan(self):
        """真孤儿（没被任何节点引用）才标 orphan——用来提示可以清理的资源。"""
        self._write("scenes/Unused.tscn",
                    '[gd_scene format=3]\n\n'
                    '[ext_resource type="Texture2D" path="res://assets/hero.png" id="9_t"]\n\n'
                    '[node name="Root" type="Node2D"]\n')
        graph = sr.scene_graph(self.root, "scenes/Unused.tscn")
        files = {f["id"]: f for f in graph["files"]}
        self.assertTrue(files["ext:9_t"]["orphan"])
        self.assertEqual(files["ext:9_t"]["used"], 0)

    def test_position_from_transform_and_scale_rotation(self):
        self._write("scenes/T.tscn", '[gd_scene format=3]\n\n[node name="Root" type="Node2D"]\n'
                                    'transform = Transform2D(1, 0, 0, 1, 30, 40)\n'
                                    'rotation = 1.5\n'
                                    'scale = Vector2(2, 3)\n')
        node = sr.scene_graph(self.root, "scenes/T.tscn")["nodes"][0]
        self.assertEqual(node["position"], [30.0, 40.0])
        self.assertEqual(node["position_from"], "transform")
        self.assertEqual(node["rotation"], 1.5)
        self.assertEqual(node["scale"], [2.0, 3.0])

    def test_node_inside_instance_is_marked_overridden(self):
        self._write("scenes/I.tscn",
                    '[gd_scene format=3]\n\n[ext_resource type="PackedScene" '
                    'path="res://scenes/Bullet.tscn" id="1_b"]\n\n'
                    '[node name="Root" type="Node2D"]\n\n'
                    '[node name="Child" parent="." instance=ExtResource("1_b")]\n\n'
                    '[node name="Inner" type="Sprite2D" parent="Child"]\n')
        graph = sr.scene_graph(self.root, "scenes/I.tscn")
        self.assertFalse(self._node(graph, "Child")["overridden"])
        self.assertTrue(self._node(graph, "Child/Inner")["overridden"],
                        "实例子树内的节点应标记为引擎侧覆写")

    def test_rejects_non_tscn_and_traversal(self):
        self._write("scenes/x.gd", "extends Node\n")
        with self.assertRaises(FsError):
            sr.scene_graph(self.root, "scenes/x.gd")
        with self.assertRaises(FsError):
            sr.scene_graph(self.root, "../outside.tscn")

    def test_scene_tree_keeps_legacy_shape(self):
        tree = sr.scene_tree(self.root, self.rel)
        self.assertEqual(len(tree["nodes"]), 5)
        first = tree["nodes"][0]
        for key in ("name", "type", "parent", "node_path", "line", "properties", "script", "instance"):
            self.assertIn(key, first)
        self.assertTrue(tree["read_only"])
        player = [n for n in tree["nodes"] if n["name"] == "Player"][0]
        self.assertEqual(player["script"], "behaviors/player.gd")
        self.assertEqual(player["node_path"], "Player")


class EditTests(SceneFixture):
    def test_add_appends_as_last_child_with_blank_separators(self):
        result = sr.scene_op(self.root, self.rel, "add", parent="Player", type="Area2D",
                             name="Hitbox", properties={"position": "Vector2(0, 0)"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["node"], "Player/Hitbox")
        self.assertEqual(result["undo"], {"op": "delete", "node": "Player/Hitbox"})
        text = self._read()
        self.assertIn('[node name="Hitbox" type="Area2D" parent="Player"]', text)
        self.assertIn("\n\n[node name=\"Hitbox\"", text)
        self.assertIn("position = Vector2(0, 0)\n\n", text)
        graph = sr.scene_graph(self.root, self.rel)
        self.assertEqual(self._node(graph, "Player")["children"][-1], "Player/Hitbox")

    def test_add_dedupes_sibling_name(self):
        sr.scene_op(self.root, self.rel, "add", parent="Player", type="Area2D", name="Hitbox")
        again = sr.scene_op(self.root, self.rel, "add", parent="Player", type="Area2D", name="Hitbox")
        self.assertEqual(again["node"], "Player/Hitbox2")

    def test_add_then_delete_round_trips(self):
        before = self._read()
        added = sr.scene_op(self.root, self.rel, "add", parent="Player", type="Area2D", name="H")
        removed = sr.scene_op(self.root, self.rel, "delete", node=added["node"])
        self.assertTrue(removed["ok"])
        self.assertEqual(self._read(), before, "新增再删除应逐字节回到原状（空行分隔也要还原）")

    def test_delete_then_restore_round_trips(self):
        before = self._read()
        removed = sr.scene_op(self.root, self.rel, "delete", node="Player")
        self.assertEqual(removed["undo"]["op"], "restore")
        restored = sr.scene_op(self.root, self.rel, "restore", lines=removed["undo"]["lines"],
                               at=removed["undo"]["at"])
        self.assertTrue(restored["ok"])
        self.assertEqual(self._read(), before, "删除后按 undo 回填应逐字节还原")
        self.assertEqual(sorted(n["id"] for n in sr.scene_graph(self.root, self.rel)["nodes"]),
                         [".", "Enemy", "Player", "Player/Gun", "Player/Sprite"])

    def test_rename_rewrites_descendant_parents(self):
        result = sr.scene_op(self.root, self.rel, "rename", node="Player", name="Hero")
        self.assertEqual(result["node"], "Hero")
        self.assertEqual(result["undo"], {"op": "rename", "node": "Hero", "name": "Player"})
        text = self._read()
        self.assertIn('[node name="Hero" type="CharacterBody2D" parent="." groups=["hero", "actors"]]', text)
        self.assertIn('[node name="Sprite" type="Sprite2D" parent="Hero"]', text)
        self.assertIn('[node name="Gun" type="Node2D" parent="Hero"]', text)
        self.assertNotIn('parent="Player"', text)

    def test_reparent_moves_subtree_and_rewrites_paths(self):
        result = sr.scene_op(self.root, self.rel, "reparent", node="Player/Sprite", parent=".")
        self.assertEqual(result["node"], "Sprite")
        text = self._read()
        self.assertIn('[node name="Sprite" type="Sprite2D" parent="."]', text)
        graph = sr.scene_graph(self.root, self.rel)
        self.assertEqual(self._node(graph, "Sprite")["parent"], ".")

    def test_reparent_keeps_grandchildren_consistent(self):
        self._write("scenes/Deep.tscn",
                    '[gd_scene format=3]\n\n[node name="Root" type="Node2D"]\n\n'
                    '[node name="World" type="Node2D" parent="."]\n\n'
                    '[node name="Player" type="Node2D" parent="."]\n\n'
                    '[node name="Body" type="Sprite2D" parent="Player"]\n\n'
                    '[node name="Arm" type="Sprite2D" parent="Player/Body"]\n')
        sr.scene_op(self.root, "scenes/Deep.tscn", "reparent", node="Player", parent="World")
        graph = sr.scene_graph(self.root, "scenes/Deep.tscn")
        ids = sorted(n["id"] for n in graph["nodes"])
        self.assertEqual(ids, [".", "World", "World/Player", "World/Player/Body", "World/Player/Body/Arm"])
        self.assertEqual(self._node(graph, "World/Player/Body/Arm")["parent"], "World/Player/Body")

    def test_duplicate_copies_subtree_with_new_names(self):
        result = sr.scene_op(self.root, self.rel, "duplicate", node="Player")
        self.assertEqual(result["node"], "Player2")
        text = self._read()
        self.assertIn('[node name="Player2" type="CharacterBody2D" parent="." groups=["hero", "actors"]]', text)
        self.assertIn('[node name="Sprite" type="Sprite2D" parent="Player2"]', text)
        graph = sr.scene_graph(self.root, self.rel)
        self.assertIn("Player2/Gun", [n["id"] for n in graph["nodes"]])

    def test_set_props_and_move(self):
        result = sr.scene_op(self.root, self.rel, "set_props", node="Player",
                             properties={"position": "Vector2(11, 22)", "z_index": "5"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["undo"]["properties"], {"position": "Vector2(100, 200)"})
        self.assertEqual(result["undo"]["remove"], ["z_index"])
        text = self._read()
        self.assertIn("position = Vector2(11, 22)", text)
        self.assertIn("z_index = 5", text)

        moved = sr.scene_op(self.root, self.rel, "move", node="Player", position=[7, 9])
        self.assertTrue(moved["ok"])
        self.assertIn("position = Vector2(7, 9)", self._read())
        self.assertEqual(sr.scene_graph(self.root, self.rel)["nodes"][1]["position"], [7.0, 9.0])

    def test_set_props_undo_restores_and_removes(self):
        before = self._read()
        first = sr.scene_op(self.root, self.rel, "set_props", node="Player",
                            properties={"position": "Vector2(1, 1)", "z_index": "3"})
        sr.scene_op(self.root, self.rel, "set_props", node="Player",
                    properties=first["undo"].get("properties", {}), remove=first["undo"].get("remove", []))
        self.assertEqual(self._read(), before, "set_props 的 undo 应能逐字节还原")

    def test_move_on_transform_node_is_refused(self):
        self._write("scenes/T.tscn", '[gd_scene format=3]\n\n[node name="Root" type="Node2D"]\n'
                                    'transform = Transform2D(1, 0, 0, 1, 30, 40)\n')
        result = sr.scene_op(self.root, "scenes/T.tscn", "move", node="Root", position=[1, 2])
        self.assertFalse(result["ok"])
        self.assertIn("transform", result["error"])

    def test_stale_mtime_is_rejected(self):
        result = sr.scene_op(self.root, self.rel, "rename", node="Player", name="Hero", if_mtime=1.0)
        self.assertFalse(result["ok"])
        self.assertTrue(result["stale"])

    def test_root_rename_rejected(self):
        result = sr.scene_op(self.root, self.rel, "rename", node=".", name="Other")
        self.assertFalse(result.get("ok", False))
        self.assertIn("根节点", result["error"])

    def test_every_op_undo_is_replayable_and_restores_file(self):
        """回归用例：undo 必须与 /api/scene/op 请求体同形，且回放后文件逐字节还原。

        曾经 undo 里用 `props`、接口用 `properties`，前端原样回传被后端当成空 payload
        静默拒绝——撤销按钮看着正常，实际什么都没发生。这类"字段名漂移"只能靠回放测试守住。
        """
        cases = [
            {"op": "add", "parent": "Player", "type": "Area2D", "name": "H"},
            {"op": "rename", "node": "Player", "name": "Hero"},
            {"op": "reparent", "node": "Player/Gun", "parent": "."},
            {"op": "duplicate", "node": "Player"},
            {"op": "set_props", "node": "Player",
             "properties": {"position": "Vector2(9, 9)", "z_index": "2"}},
            {"op": "set_props", "node": "Player", "remove": ["position"]},
            {"op": "move", "node": "Player", "position": [5, 6]},
            {"op": "delete", "node": "Player/Sprite"},
            {"op": "delete", "node": "Player"},
        ]
        for payload in cases:
            with self.subTest(op=payload["op"], target=payload.get("node")):
                # 每个用例都从同一份原始场景出发：否则前一个用例改了名/删了节点，
                # 后一个用例引用的路径就失效了（这坑踩过，报错信息还很误导）
                self._write(self.rel, SCENE)
                before = self._read()
                applied = sr.scene_op(self.root, self.rel, **payload)
                self.assertTrue(applied["ok"], applied.get("error"))
                self.assertNotEqual(self._read(), before, "操作应真的改动文件")
                undone = sr.scene_op(self.root, self.rel, **applied["undo"])
                self.assertTrue(undone["ok"], undone.get("error"))
                self.assertEqual(self._read(), before, "undo 未能逐字节还原文件")


class GuardTests(SceneFixture):
    def test_delete_root_refused(self):
        result = sr.scene_op(self.root, self.rel, "delete", node=".")
        self.assertFalse(result.get("ok", False))
        self.assertIn("根节点", result["error"])

    def test_reparent_into_own_descendant_refused(self):
        result = sr.scene_op(self.root, self.rel, "reparent", node="Player", parent="Player/Gun")
        self.assertFalse(result.get("ok", False))
        self.assertIn("子孙", result["error"])

    def test_node_not_found(self):
        result = sr.scene_op(self.root, self.rel, "delete", node="Nope")
        self.assertFalse(result.get("ok", False))
        self.assertIn("不存在", result["error"])

    def test_ambiguous_name_points_to_paths(self):
        self._write("scenes/Ambig.tscn",
                    '[gd_scene format=3]\n\n[node name="Root" type="Node2D"]\n\n'
                    '[node name="A" type="Node2D" parent="."]\n\n'
                    '[node name="Target" type="Node2D" parent="A"]\n\n'
                    '[node name="B" type="Node2D" parent="."]\n\n'
                    '[node name="Target" type="Node2D" parent="B"]\n')
        result = sr.scene_op(self.root, "scenes/Ambig.tscn", "delete", node="Target")
        self.assertFalse(result.get("ok", False))
        self.assertIn("A/Target", result["error"])

    def test_invalid_names_and_types(self):
        for kwargs in ({"name": "bad/name"}, {"name": "bad.name"}, {"name": "  pad  "}):
            result = sr.scene_op(self.root, self.rel, "add", parent=".", type="Node2D", **kwargs)
            self.assertFalse(result.get("ok", False), kwargs)
        result = sr.scene_op(self.root, self.rel, "add", parent=".", type="1Bad", name="X")
        self.assertFalse(result.get("ok", False))

    def test_property_value_injection_refused(self):
        bad = {"position": "Vector2(0,0)\n[node name=\"Injected\" type=\"Node2D\"]"}
        result = sr.scene_op(self.root, self.rel, "set_props", node="Player", properties=bad)
        self.assertFalse(result.get("ok", False))
        self.assertEqual(self._read(), SCENE)

    def test_contract_file_is_read_only(self):
        """契约文件禁写：写路径不抛异常，但必须返回 403 且不改文件。"""
        self._write("DOCMIND_RULES.md", "# rules\n")
        result = sr.scene_op(self.root, "DOCMIND_RULES.md", "delete", node=".")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 403)
        with open(os.path.join(self.root, "DOCMIND_RULES.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "# rules\n")

    def test_graph_read_of_contract_file_is_refused_by_extension(self):
        self._write("DOCMIND_RULES.md", "# rules\n")
        with self.assertRaises(FsError):
            sr.scene_graph(self.root, "DOCMIND_RULES.md")

    def test_broken_scene_is_not_edited(self):
        broken = SCENE + '\n[node name="Orphan" type="Node2D" parent="Ghost"]\n'
        self._write(self.rel, broken)
        result = sr.scene_op(self.root, self.rel, "add", parent=".", type="Node2D", name="X")
        self.assertFalse(result["ok"])
        self.assertIn("结构异常", result["error"])
        self.assertEqual(self._read(), broken, "拒绝编辑时不得改动文件")

    def test_edit_producing_invalid_structure_rolls_back(self):
        """守门用例：若某次编辑真的把结构改坏，必须自动回滚而不是留半个坏场景。"""
        import unittest.mock as mock
        original = sr._op_rename

        def broken(lines, node_map, node_path, new_name):
            # 故意把子节点的 parent 指向一个不存在的路径
            return original(lines, node_map, node_path, new_name) and (
                [line.replace('parent="Hero"', 'parent="Ghost"') for line in lines],
                {'op': 'rename', 'node': 'Hero', 'name': 'Player'}, 'Hero')

        with mock.patch.object(sr, '_op_rename', side_effect=broken):
            result = sr.scene_op(self.root, self.rel, "rename", node="Player", name="Hero")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("rolled_back"))
        self.assertEqual(self._read(), SCENE, "自检失败必须原样回滚")

    def test_unknown_op(self):
        result = sr.scene_op(self.root, self.rel, "explode", node="Player")
        self.assertFalse(result.get("ok", False))


class RuntimeEventTests(SceneFixture):
    def _seed(self):
        sr.runtime_events(self.root, [
            {"type": "damage", "data": {"hp": 10}, "session": "s1",
             "timestamp": "2026-09-14T00:00:00+00:00"},
            {"type": "heal", "data": {"hp": 20}, "session": "s1",
             "timestamp": "2026-09-14T00:00:30+00:00"},
            {"type": "death", "session": "s2", "timestamp": "2026-09-14T01:00:00+00:00"},
        ])

    def test_append_filter_and_stats(self):
        self._seed()
        data = sr.runtime_events(self.root)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["types"], {"damage": 1, "death": 1, "heal": 1})
        self.assertEqual([e["type"] for e in sr.runtime_events(self.root, types=["damage"])["events"]],
                         ["damage"])
        self.assertEqual([e["type"] for e in sr.runtime_events(self.root, sources=["godot"])["events"]], [])
        self.assertEqual(len(sr.runtime_events(self.root, limit=1)["events"]), 1)
        ranged = sr.runtime_events(self.root, from_ts="2026-09-14T00:59:00+00:00")["events"]
        self.assertEqual([e["type"] for e in ranged], ["death"])
        self.assertEqual([e["type"] for e in sr.runtime_events(self.root, keyword="20")["events"]],
                         ["heal"])

    def test_keyword_does_not_match_timestamps(self):
        self._seed()
        self.assertEqual(sr.runtime_events(self.root, keyword="2026-09-14")["matched"], 0)

    def test_sessions_group_by_explicit_id(self):
        self._seed()
        sessions = sr.runtime_sessions(self.root)["sessions"]
        self.assertEqual([s["id"] for s in sessions], ["s1", "s2"])
        self.assertEqual([s["count"] for s in sessions], [2, 1])

    def test_sessions_split_by_gap_when_no_id(self):
        sr.runtime_events(self.root, [
            {"type": "a", "timestamp": "2026-09-14T00:00:00+00:00"},
            {"type": "b", "timestamp": "2026-09-14T00:00:10+00:00"},
            {"type": "c", "timestamp": "2026-09-14T05:00:00+00:00"},
        ])
        sessions = sr.runtime_sessions(self.root, 120)["sessions"]
        self.assertEqual([s["count"] for s in sessions], [2, 1])

    def test_engine_log_marker_is_captured(self):
        marker = sr.MARKER + json.dumps({"type": "spawn", "data": {"n": 3}})
        self._write(".docmind_engine.log", "noise\n" + marker + "\nmore noise\n")
        data = sr.runtime_events(self.root)
        captured = [e for e in data["events"] if e["source"] == "godot"]
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["type"], "spawn")
        self.assertEqual(data["captured"], 1)

    def test_clear_all_uses_cursor_without_truncating_log(self):
        marker = sr.MARKER + json.dumps({"type": "old"})
        self._write(".docmind_engine.log", marker + "\n")
        self.assertTrue(len(sr.runtime_events(self.root)["events"]) >= 1)
        sr.runtime_clear(self.root, "all")
        self.assertEqual(sr.runtime_events(self.root)["total"], 0)
        with open(os.path.join(self.root, ".docmind_engine.log"), encoding="utf-8") as f:
            self.assertIn(marker, f.read(), "清空不得截断正在写的引擎日志")
        with open(os.path.join(self.root, ".docmind_engine.log"), "a", encoding="utf-8") as f:
            f.write(sr.MARKER + json.dumps({"type": "new"}) + "\n")
        events = sr.runtime_events(self.root)["events"]
        self.assertEqual([e["type"] for e in events], ["new"], "清空后只应看到新产生的事件")

    def test_event_validation(self):
        with self.assertRaises(FsError):
            sr.runtime_events(self.root, [{"no_type": 1}])
        with self.assertRaises(FsError):
            sr.runtime_events(self.root, [{"type": "x", "data": "y" * 9000}])
        with self.assertRaises(FsError):
            sr.runtime_events(self.root, [{"type": "x"}] * 501)
        with self.assertRaises(FsError):
            sr.runtime_clear(self.root, "nope")


if __name__ == "__main__":
    unittest.main()
