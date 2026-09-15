"""连接器自主切换策略层回归。

策略核心在 mcp_client：capabilities_of（能力推导）/ connector_directory（Agent 目录）/
select_connector（按任务语义打分排序）。tools 暴露 dev_list_connectors / dev_route_connector /
dev_list_connector_tools，并在 dev_mcp_call 失败处提示回退。api 暴露 /api/agent/connector-route。

全部纯配置读取（不打开引擎会话），可用临时目录离线单测；端点用 monkeypatch 隔离项目根。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import mcp_client
import tools


def _tmp_root(servers=None):
    d = tempfile.TemporaryDirectory()
    if servers is not None:
        with open(os.path.join(d.name, ".docmind_mcp.json"), "w", encoding="utf-8") as f:
            json.dump({"servers": servers}, f)
    return d


class TestCapabilities(unittest.TestCase):
    def test_capabilities_of_godot(self):
        caps = mcp_client.capabilities_of({"engine": "godot"})
        self.assertIn("game_engine", caps)
        self.assertIn("engine:godot", caps)
        self.assertIn("scene", caps)
        self.assertIn("run", caps)

    def test_capabilities_of_user_extra(self):
        caps = mcp_client.capabilities_of({"engine": "unity", "capabilities": ["audio", "Analytics"]})
        self.assertIn("engine:unity", caps)
        self.assertIn("audio", caps)
        self.assertIn("analytics", caps)  # 统一小写

    def test_best_for_fallback(self):
        self.assertEqual(mcp_client.best_for_of({"engine": "unreal"}), mcp_client._BEST_FOR["unreal"])
        self.assertEqual(mcp_client.best_for_of({"engine": "x", "best_for": "自定义说明"}), "自定义说明")


class TestDirectory(unittest.TestCase):
    def test_directory_marks_enabled(self):
        with _tmp_root() as root:
            rows = {r["key"]: r for r in mcp_client.connector_directory(root)}
            self.assertTrue(rows["godot"]["enabled"])
            self.assertFalse(rows["unity"]["enabled"])
            self.assertFalse(rows["unreal"]["enabled"])
            # 能力 + 适用说明都带出
            self.assertIn("engine:godot", rows["godot"]["capabilities"])
            self.assertTrue(rows["godot"]["best_for"])


class TestSelectConnector(unittest.TestCase):
    def test_godot_top_for_godot_scene(self):
        with _tmp_root() as root:
            ranked = mcp_client.select_connector(root, "Godot 里打开 Main 场景并运行")
            self.assertTrue(ranked)
            self.assertEqual(ranked[0]["key"], "godot")
            self.assertGreater(ranked[0]["score"], 0)
            self.assertIn("engine:godot", ranked[0]["reason"])

    def test_unity_disabled_excluded(self):
        with _tmp_root() as root:
            # 默认 unity 未启用 → 即使 hint 提 Unity 也不该路由到它
            ranked = mcp_client.select_connector(root, "Unity 构建 Player")
            self.assertEqual(ranked, [])

    def test_unity_enabled_routes(self):
        with _tmp_root({"unity": {"enabled": True}}) as root:
            ranked = mcp_client.select_connector(root, "Unity 构建 Player")
            self.assertTrue(ranked)
            self.assertEqual(ranked[0]["key"], "unity")

    def test_generic_scene_picks_godot(self):
        with _tmp_root() as root:
            ranked = mcp_client.select_connector(root, "生成游戏场景")
            self.assertTrue(ranked)
            self.assertEqual(ranked[0]["key"], "godot")


class TestAgentTools(unittest.TestCase):
    def setUp(self):
        self._tmp = _tmp_root()
        self._pat = mock.patch.object(tools, "get_runtime", return_value=self._tmp.name)
        self._pat.start()

    def tearDown(self):
        self._pat.stop()
        self._tmp.cleanup()

    def test_dev_list_connectors(self):
        out = json.loads(tools.dev_list_connectors(""))
        self.assertTrue(out["ok"])
        keys = {c["key"] for c in out["connectors"]}
        self.assertEqual(keys, {"godot", "unity", "unreal"})

    def test_dev_route_connector_top(self):
        out = json.loads(tools.dev_route_connector("Godot 里打开 Main 场景并运行"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["top"], "godot")
        self.assertTrue(out["matches"])

    def test_dev_route_connector_no_match_message(self):
        # unity 未启用 → 无匹配，返回可降级提示而非报错
        with _tmp_root() as root:
            with mock.patch.object(tools, "get_runtime", return_value=root):
                out = json.loads(tools.dev_route_connector("Unity 构建"))
                self.assertTrue(out["ok"])
                self.assertEqual(out["matches"], [])
                self.assertIn("内置工具", out["message"])

    def test_dev_mcp_call_disabled_suggests_route(self):
        # unity 默认未启用 → 失败提示里带 dev_route_connector 回退
        res = tools.dev_mcp_call("key: unity\nname: some_tool")
        self.assertIn("dev_route_connector", res)


class TestEndpoint(unittest.TestCase):
    def test_connector_route_endpoint(self):
        from fastapi.testclient import TestClient
        import api
        with _tmp_root() as root:
            with mock.patch.object(api, "_project_root_or_error", return_value=root):
                client = TestClient(api.app)
                resp = client.get("/api/agent/connector-route", params={"hint": "Godot 场景"})
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                self.assertTrue(body["ok"])
                self.assertEqual(body["matches"][0]["key"], "godot")


if __name__ == "__main__":
    unittest.main()
