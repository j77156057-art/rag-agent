# -*- coding: utf-8 -*-
"""unity_graph：合成 Unity 工程夹具下的 GUID 引用图测试（不依赖 Unity 编辑器）。"""
import os
import tempfile
import unittest

from unity_graph import build_unity_graph

P1 = 'a' * 31 + '1'          # Assets/Scripts/Player.cs
E1 = 'b' * 31 + '2'          # Assets/Scripts/Enemy.cs（孤立）
SCN = 'c' * 30 + '01'        # Assets/Scenes/Main.unity
PFB = 'd' * 30 + '02'        # Assets/Prefabs/Player.prefab
TEX = 'e' * 30 + '03'        # Assets/Art/tex.png
FOLDER = 'f' * 30 + '04'     # Assets/Art 文件夹 meta
DEAD = '1' * 31 + '5'        # Assets/Unused/Dead.cs（孤立）
GHOST = '2' * 31 + '6'       # 孤儿 meta（资产文件不存在）
DUPLICATE = '5' * 31 + '9'   # 两个 meta 共用的冲突 guid
MISSING = '9' * 31 + 'f'     # 被引用但工程内无 meta
LIBGUID = '3' * 30 + '07'    # Library/ 下，必须被跳过


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)


def meta(guid):
    return (
        "fileFormatVersion: 2\n"
        f"guid: {guid}\n"
        "MonoImporter:\n"
        "  externalObjects: {}\n"
    )


MAIN_UNITY = f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &845201912
GameObject:
  m_ObjectHideFlags: 0
--- !u!114 &9223372036
MonoBehaviour:
  m_Script: {{fileID: 11500000, guid: {P1.upper()}, type: 3}}
  m_Texture: {{fileID: 21300000, guid: {TEX}, type: 3}}
  m_Broken: {{fileID: 21300000, guid: {MISSING}, type: 3}}
  m_ScriptAgain: {{fileID: 11500000, guid: {P1}, type: 3}}
"""  # 大小写混合：验证 GUID 归一

PREFAB = f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1001 &12345
PrefabInstance:
  m_Script: {{fileID: 11500000, guid: {P1}, type: 3}}
  m_Materials:
  - {{fileID: 2100000, guid: {TEX}, type: 3}}
"""


def build_project(base):
    write(os.path.join(base, 'ProjectSettings', 'ProjectVersion.txt'),
          'm_EditorVersion: 2022.3.40f1\n')
    write(os.path.join(base, 'Assets', 'Scripts', 'Player.cs.meta'), meta(P1))
    write(os.path.join(base, 'Assets', 'Scripts', 'Player.cs'),
          'using UnityEngine;\npublic class Player : MonoBehaviour {}\n')
    write(os.path.join(base, 'Assets', 'Scripts', 'Enemy.cs.meta'), meta(E1))
    write(os.path.join(base, 'Assets', 'Scripts', 'Enemy.cs'), 'public class Enemy {}\n')
    write(os.path.join(base, 'Assets', 'Scenes', 'Main.unity.meta'), meta(SCN))
    write(os.path.join(base, 'Assets', 'Scenes', 'Main.unity'), MAIN_UNITY)
    write(os.path.join(base, 'Assets', 'Prefabs', 'Player.prefab.meta'), meta(PFB))
    write(os.path.join(base, 'Assets', 'Prefabs', 'Player.prefab'), PREFAB)
    write(os.path.join(base, 'Assets', 'Art', 'tex.png.meta'), meta(TEX))
    write(os.path.join(base, 'Assets', 'Art', 'tex.png'), b'\x89PNG\r\n\x1a\n'.decode('latin1'))
    write(os.path.join(base, 'Assets', 'Art.meta'), meta(FOLDER))
    write(os.path.join(base, 'Assets', 'Unused', 'Dead.cs.meta'), meta(DEAD))
    write(os.path.join(base, 'Assets', 'Unused', 'Dead.cs'), 'class Dead {}\n')
    # 孤儿 meta：只有 .meta，资产本体缺失
    write(os.path.join(base, 'Assets', 'Orphan', 'ghost.prefab.meta'), meta(GHOST))
    # GUID 冲突：Dup/A 与 Dup/B 的 meta 使用同一 guid（与活跃节点无关）
    write(os.path.join(base, 'Assets', 'Dup', 'A.cs.meta'), meta(DUPLICATE))
    write(os.path.join(base, 'Assets', 'Dup', 'A.cs'), 'class A {}\n')
    write(os.path.join(base, 'Assets', 'Dup', 'B.cs.meta'), meta(DUPLICATE))
    write(os.path.join(base, 'Assets', 'Dup', 'B.cs'), 'class B {}\n')
    # Library 必须整体跳过
    write(os.path.join(base, 'Library', 'skipped.cs.meta'), meta(LIBGUID))


class UnityGraphTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        build_project(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def test_graph_structure(self):
        res = build_unity_graph(self.base)
        self.assertTrue(res['ok'])
        st = res['stats']
        self.assertTrue(st['unity_project'])
        self.assertEqual(st['scanned_root'], 'Assets')

        # 10 个 meta（Library 下 1 个被跳过）；9 个唯一 guid（1 个重复冲突）
        self.assertEqual(st['metas'], 10)
        self.assertEqual(st['assets_total'], 9)
        self.assertEqual(st['duplicate_guids'], 1)
        self.assertEqual(st['orphan_meta'], 1)
        # 只有 .unity/.prefab 做正文扫描
        self.assertEqual(st['serialized_files'], 2)

        # 活跃资产节点：Main.unity / Player.prefab / Player.cs / tex.png + missing
        self.assertEqual(st['asset_nodes'], 4)
        self.assertEqual(st['missing_nodes'], 1)
        self.assertEqual(st['nodes'], 5)

        edges = {(e['source'].split(':', 1)[1],
                  e['target'].split(':', 1)[1],
                  e['target'].startswith('m:')): e
                 for e in res['edges']}
        self.assertEqual(st['edges'], 5)
        self.assertEqual(st['resolved_edges'], 4)
        self.assertEqual(st['missing_edges'], 1)

        # 大小写归一：Main 对 Player.cs 的两处引用聚合为一条边 ×2
        key = (SCN, P1, False)
        self.assertIn(key, edges)
        self.assertEqual(edges[key]['count'], 2)
        # 其余四条边
        self.assertIn((SCN, TEX, False), edges)
        self.assertIn((PFB, P1, False), edges)
        self.assertIn((PFB, TEX, False), edges)
        self.assertIn((SCN, MISSING, True), edges)

        # missing 节点形态
        missing_nodes = [n for n in res['nodes'] if n['kind'] == 'missing']
        self.assertEqual(len(missing_nodes), 1)
        self.assertTrue(missing_nodes[0]['external'])
        self.assertIn('99999999', missing_nodes[0]['sub'] + missing_nodes[0]['label'])

        # 节点类型着色
        kinds = {n['rel']: n['kind'] for n in res['nodes'] if not n['external']}
        self.assertEqual(kinds['Assets/Scenes/Main.unity'], 'scene')
        self.assertEqual(kinds['Assets/Prefabs/Player.prefab'], 'prefab')
        self.assertEqual(kinds['Assets/Scripts/Player.cs'], 'script')
        self.assertEqual(kinds['Assets/Art/tex.png'], 'texture')

    def test_library_skipped(self):
        res = build_unity_graph(self.base)
        guids = {n.get('guid') for n in res['nodes']}
        self.assertNotIn(LIBGUID, guids)
        # Library guid 绝不进入索引：任意边都不应指向它
        self.assertFalse(any(LIBGUID in e['target'] for e in res['edges']))

    def test_assets_dir_without_project_settings(self):
        """code_root 直接指向一个 Assets 式目录（无 ProjectSettings）也能建图。"""
        with tempfile.TemporaryDirectory() as raw:
            other = os.path.join(raw, 'AssetsOnly')
            g = 'c' * 30 + '11'
            write(os.path.join(other, 'Scenes', 'Demo.unity.meta'), meta(g))
            write(os.path.join(other, 'Scenes', 'Demo.unity'),
                  f"m_Script: {{fileID: 11500000, guid: {MISSING}, type: 3}}\n")
            res = build_unity_graph(other)
            self.assertTrue(res['ok'])
            self.assertFalse(res['stats']['unity_project'])
            self.assertEqual(res['stats']['scanned_root'], '.')
            self.assertEqual(res['stats']['missing_edges'], 1)
            self.assertEqual(res['stats']['asset_nodes'], 1)

    def test_invalid_root(self):
        self.assertFalse(build_unity_graph('')['ok'])
        self.assertFalse(build_unity_graph(os.path.join(self.base, 'nope'))['ok'])


if __name__ == '__main__':
    unittest.main()
