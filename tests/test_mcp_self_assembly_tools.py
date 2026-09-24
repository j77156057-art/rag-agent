# -*- coding: utf-8 -*-
"""Agent 自助装配 MCP 工具链：search → (审批) add → probe → discover → (审批) decide。

全部离线：stdio 探活/发现用桩替换，不真正拉起子进程；审批写临时项目的台账。
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import mcp_capabilities
import mcp_client
from tools import (
    dev_mcp_search, dev_mcp_add, dev_mcp_probe, dev_mcp_discover,
    dev_mcp_decide, dev_mcp_remove, dev_approve,
)


def _parse(text):
    return json.loads(text)


class McpSelfAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docmind_mcp_self_")
        self.root = os.path.join(self.tmp.name, "project")
        os.makedirs(self.root)
        self._old_root = config.get_runtime("code_root")
        config.set_runtime("code_root", self.root)

    def tearDown(self):
        if self._old_root:
            config.set_runtime("code_root", self._old_root)
        else:
            config._RUNTIME.pop("code_root", None)
        self.tmp.cleanup()

    def _approve(self, action, target):
        out = dev_approve(f"action: {action}\ntarget: {target}")
        self.assertIn("已审批", out)

    # ---- search ------------------------------------------------------------
    def test_search_offline_directory_returns_template(self):
        r = _parse(dev_mcp_search("EDA"))
        self.assertTrue(r["ok"])
        item = r["results"][0]
        self.assertEqual(item["id"], "eda")
        self.assertIn("template", item)
        self.assertEqual(item["template"]["transport"], "stdio")

    def test_search_rejects_short_query(self):
        self.assertTrue(dev_mcp_search("e").startswith("MCP 搜索失败"))

    # ---- add：审批门 --------------------------------------------------------
    def test_add_blocked_then_approved_saves_server(self):
        arg = ("key: kicad\nlabel: KiCad MCP\ntransport: stdio\n"
               "command: uvx\nargs: kicad-mcp-pro --transport stdio\n")
        blocked = _parse(dev_mcp_add(arg))
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["action"], "mcp_server")
        target = blocked["target"]
        self.assertTrue(target.startswith("kicad:"))

        self._approve("mcp_server", target)
        ok = _parse(dev_mcp_add(arg))
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["server"]["transport"], "stdio")
        self.assertTrue(ok["server"]["enabled"])

        cfg = mcp_client.get_server_config(self.root, "kicad")
        self.assertEqual(cfg["command"], "uvx")
        self.assertEqual(cfg["args"], ["kicad-mcp-pro", "--transport", "stdio"])

    def test_add_target_rebound_when_command_changes(self):
        a1 = "key: svc\ntransport: http\nurl: http://127.0.0.1:9000/mcp\n"
        blocked1 = _parse(dev_mcp_add(a1))
        self._approve("mcp_server", blocked1["target"])
        self.assertTrue(_parse(dev_mcp_add(a1))["ok"])
        # 换 URL 后旧 target 不匹配，必须重新审批（防止审批被换地址复用）
        a2 = "key: svc\ntransport: http\nurl: http://127.0.0.1:9999/mcp\n"
        blocked2 = _parse(dev_mcp_add(a2))
        self.assertTrue(blocked2["blocked"])
        self.assertNotEqual(blocked1["target"], blocked2["target"])

    def test_add_validates_inputs(self):
        self.assertFalse(_parse(dev_mcp_add("key: bad key!"))["ok"])
        self.assertFalse(_parse(dev_mcp_add("key: x\ntransport: stdio"))["ok"])
        self.assertFalse(_parse(dev_mcp_add("key: x\ntransport: http\nurl: ftp://x"))["ok"])
        bad_args = _parse(dev_mcp_add("key: x\ncommand: uvx\nargs_json: not-json"))
        self.assertFalse(bad_args["ok"])

    def test_args_json_takes_precedence(self):
        arg = ('key: js\ntransport: stdio\ncommand: uvx\n'
               'args: ignored words\nargs_json: ["pkg", "--flag", "a b"]\n')
        blocked = _parse(dev_mcp_add(arg))
        self._approve("mcp_server", blocked["target"])
        _parse(dev_mcp_add(arg))
        cfg = mcp_client.get_server_config(self.root, "js")
        self.assertEqual(cfg["args"], ["pkg", "--flag", "a b"])

    # ---- probe -------------------------------------------------------------
    def test_probe_unknown_server_fails_cleanly(self):
        r = _parse(dev_mcp_probe("missing"))
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_probe_returns_tool_names(self):
        # 预置一个已保存的 stdio 服务器，桩掉真正的会话
        mcp_client.save_server(self.root, "demo", {
            "transport": "stdio", "command": "uvx", "args": ["demo-mcp"], "enabled": True})
        with patch("mcp_client._session_for") as _sess:
            _sess.return_value.request.return_value = {
                "tools": [{"name": "ping"}, {"name": "read"}]}
            r = _parse(dev_mcp_probe("demo"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["tool_count"], 2)
        self.assertEqual(r["tools"], ["ping", "read"])

    # ---- discover + decide --------------------------------------------------
    def test_discover_then_approve_gates_routing(self):
        mcp_client.save_server(self.root, "eda-x", {
            "transport": "stdio", "command": "uvx", "args": ["eda"], "enabled": True})
        tools = [{"name": "run_drc", "description": "run pcb drc check",
                  "input_schema": {"type": "object", "properties": {}}}]
        with patch("mcp_client.list_tools", return_value={"ok": True, "tools": tools}):
            d = _parse(dev_mcp_discover("eda-x"))
        self.assertTrue(d["ok"])
        self.assertEqual(d["candidate"]["status"], "pending")
        self.assertIn("drc", d["candidate"]["capabilities"])
        self.assertEqual(mcp_capabilities.active_for(self.root, "eda-x"), {})

        # 未审批直接 approve 被门禁拦截
        blocked = _parse(dev_mcp_decide("decision: approve\nkey: eda-x"))
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["action"], "mcp_capability")
        self._approve("mcp_capability", "eda-x")
        decided = _parse(dev_mcp_decide("decision: approve\nkey: eda-x"))
        self.assertTrue(decided["ok"])
        self.assertTrue(decided["routing_enabled"])
        active = mcp_capabilities.active_for(self.root, "eda-x")
        self.assertEqual(active["status"], "active")

    def test_decide_reject_needs_no_approval(self):
        mcp_client.save_server(self.root, "rj", {
            "transport": "stdio", "command": "uvx", "args": ["rj"], "enabled": True})
        tools = [{"name": "x", "description": "generic helper",
                  "input_schema": {"type": "object", "properties": {}}}]
        with patch("mcp_client.list_tools", return_value={"ok": True, "tools": tools}):
            _parse(dev_mcp_discover("rj"))
        r = _parse(dev_mcp_decide("decision: reject\nkey: rj"))
        self.assertTrue(r["ok"])
        self.assertFalse(r["routing_enabled"])
        self.assertEqual(mcp_capabilities.active_for(self.root, "rj"), {})

    # ---- remove -------------------------------------------------------------
    def test_remove_blocked_then_approved(self):
        mcp_client.save_server(self.root, "gone", {
            "transport": "stdio", "command": "uvx", "args": ["gone"], "enabled": True})
        blocked = _parse(dev_mcp_remove("gone"))
        self.assertTrue(blocked["blocked"])
        self._approve("mcp_server", "gone")
        with patch("mcp_client.close_server"):
            r = _parse(dev_mcp_remove("gone"))
        self.assertTrue(r["ok"])
        self.assertNotIn("gone", mcp_client.load_user_servers(self.root))

    # ---- 无代码库 ------------------------------------------------------------
    def test_tools_require_code_root(self):
        import tools as tools_mod
        config._RUNTIME.pop("code_root", None)
        with patch.object(tools_mod, "CODE_ROOT", ""):
            outs = (dev_mcp_search("EDA"), dev_mcp_add("key: x"),
                    dev_mcp_probe("x"), dev_mcp_discover("x"),
                    dev_mcp_decide("decision: approve\nkey: x"), dev_mcp_remove("x"))
        for out in outs:
            self.assertIn("未配置代码库", out)


if __name__ == "__main__":
    unittest.main()
