"""连接器启停 UI 的后端契约测试：连接状态查询 + 断开 + 配置管理端点。

注意：本沙箱无真实 Godot/Unity/Unreal 引擎，无法实机建立 stdio 长驻会话，
因此用注入假会话的方式验证 active_servers / close_server 的核心逻辑与 HTTP 端点形状。
真机「连接后 active 出现、断开后消失」需在装好 godot-ai 插件后手测。
"""
import asyncio
import os
import tempfile
import unittest

import mcp_client
import api


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class TestMcpConnectorLogic(unittest.TestCase):
    def test_active_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mcp_client.active_servers(d), [])

    def test_active_and_close(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.abspath(d)
            mcp_client._SESSIONS[(root, "demo")] = _FakeSession()
            self.assertEqual(mcp_client.active_servers(root), ["demo"])
            res = mcp_client.close_server(root, "demo")
            self.assertTrue(res["ok"])
            self.assertTrue(res["closed"])
            self.assertNotIn((root, "demo"), mcp_client._SESSIONS)
            self.assertEqual(mcp_client.active_servers(root), [])

    def test_close_unknown_key_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.abspath(d)
            res = mcp_client.close_server(root, "does-not-exist")
            self.assertTrue(res["ok"])
            self.assertFalse(res["closed"])


class TestMcpConnectorEndpoints(unittest.TestCase):
    def setUp(self):
        # 用临时目录作为项目根，避免污染仓库根的 .docmind_mcp.json；
        # 直接 monkeypatch _project_root_or_error，避免受其它测试对全局
        # get_runtime("code_root")/CODE_ROOT 的污染影响（隔离运行通过、整跑失败的根因）。
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_root = api._project_root_or_error
        api._project_root_or_error = lambda: self._tmp.name

    def tearDown(self):
        api._project_root_or_error = self._orig_root
        self._tmp.cleanup()

    def test_status_shape(self):
        out = asyncio.run(api.mcp_status_ep())
        self.assertTrue(out["ok"])
        self.assertIsInstance(out["active"], list)

    def test_close_endpoint_shape(self):
        out = asyncio.run(api.mcp_close_ep(api.McpServerKeyReq(key="godot")))
        # 未连接时返回 ok:True, closed:False（不抛错）
        self.assertTrue(out["ok"])
        self.assertFalse(out["closed"])

    def test_save_and_remove_roundtrip(self):
        out = asyncio.run(api.mcp_server_save_ep(
            api.McpServerReq(key="demo_custom", config={"transport": "http", "url": "http://127.0.0.1:9999/mcp"})))
        self.assertTrue(out["ok"])
        keys = [s["key"] for s in out["servers"]]
        self.assertIn("demo_custom", keys)
        out2 = asyncio.run(api.mcp_server_remove_ep(api.McpServerKeyReq(key="demo_custom")))
        self.assertTrue(out2["ok"])
        self.assertNotIn("demo_custom", [s["key"] for s in out2["servers"]])


if __name__ == "__main__":
    unittest.main()
