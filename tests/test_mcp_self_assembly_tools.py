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
import agent as agent_mod
import mcp_capabilities
import mcp_client
from tools import (
    dev_mcp_search, dev_mcp_add, dev_mcp_probe, dev_mcp_discover,
    dev_mcp_decide, dev_mcp_remove, dev_approve,
    set_session_web_enabled, _session_web_enabled,
)
from game_workbench import approval


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
        # 模拟工作台上的用户确认；Agent 的 dev_approve 不能写入 MCP 审批。
        row = approval(self.root, action, "user", approved=True, target=target)
        self.assertEqual(row["user"], "user")

    def test_agent_cannot_self_approve_mcp(self):
        result = dev_approve("action: mcp_server\ntarget: arbitrary")
        self.assertIn("不能自行审批", result)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".docmind_approvals.jsonl")))

    # ---- search ------------------------------------------------------------
    def test_search_offline_directory_returns_template(self):
        token = set_session_web_enabled(False)
        try:
            r = _parse(dev_mcp_search("EDA"))
        finally:
            _session_web_enabled.reset(token)
        self.assertTrue(r["ok"])
        item = r["results"][0]
        self.assertEqual(item["id"], "eda")
        self.assertIn("template", item)
        self.assertEqual(item["template"]["transport"], "stdio")

    def test_agent_search_uses_registry_only_when_session_allows_web(self):
        calls = []

        def fake_registry(query):
            calls.append(query)
            if query != "easyeda":
                return {"ok": False, "candidates": []}
            return {"ok": True, "candidates": [{
                "transport": "stdio", "command": "npx",
                "args": ["-y", "@vlabsoft/easyeda-pro-mcp"], "url": "",
                "env": {}, "headers": {}, "command_unresolved": False,
                "provenance": {"server_name": "io.github.VLab-Software/easyeda-pro-mcp",
                               "description": "EasyEDA Pro bridge",
                               "url": "https://github.com/VLab-Software/easyeda_mcp",
                               "domain": "github.com"},
            }]}

        with patch("mcp_registry_bridge.registry_fn_for", return_value=fake_registry):
            token = set_session_web_enabled(True)
            try:
                result = _parse(dev_mcp_search("EDA"))
            finally:
                _session_web_enabled.reset(token)
        self.assertIn("easyeda", calls)
        options = result["results"][0]["connection_options"]
        self.assertTrue(any(o.get("id") == "io.github.VLab-Software/easyeda-pro-mcp"
                            for o in options))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".docmind_mcp.json")))

        token = set_session_web_enabled(False)
        try:
            with patch("mcp_registry_bridge.registry_fn_for", side_effect=AssertionError("network")):
                offline = _parse(dev_mcp_search("EDA"))
        finally:
            _session_web_enabled.reset(token)
        self.assertEqual(offline["results"][0]["source_status"], "offline_guide")

    def test_natural_language_agent_can_discover_mcp_without_preconfigured_server(self):
        class ScriptedLLM:
            def __init__(self):
                self.calls = 0
                self.first_messages = None
                self.first_tool_names = None

            def chat(self, messages, stream=True, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    self.first_messages = messages
                    self.first_tool_names = agent._effective_tool_names()
                response = ("Thought: 先查真实连接候选\nAction: dev_mcp_search\nAction Input: EDA"
                            if self.calls == 1 else "Final Answer: 已找到候选，等待用户选择。")
                return [response] if stream else response

            def count_tokens(self, _text):
                return 0

        def fake_registry(query):
            if query != "easyeda":
                return {"ok": False, "candidates": []}
            return {"ok": True, "candidates": [{
                "transport": "stdio", "command": "npx",
                "args": ["-y", "@vlabsoft/easyeda-pro-mcp"], "url": "",
                "env": {}, "headers": {}, "command_unresolved": False,
                "provenance": {"server_name": "io.github.VLab-Software/easyeda-pro-mcp",
                               "description": "EasyEDA Pro bridge",
                               "url": "https://github.com/VLab-Software/easyeda_mcp",
                               "domain": "github.com"},
            }]}

        old_state_root = config.STATE_ROOT
        config.STATE_ROOT = os.path.join(self.tmp.name, "state")
        try:
            llm = ScriptedLLM()
            agent = agent_mod.Agent(llm=llm, session_id="mcp-natural-language", tool_mode="react")
            with patch("mcp_registry_bridge.registry_fn_for", return_value=fake_registry):
                events = list(agent.run("帮我连接 EDA 的 MCP", web_enabled=True, stream=True))
            self.assertIn("dev_mcp_search", str(llm.first_messages))
            self.assertIn("dev_mcp_search", llm.first_tool_names)
            self.assertTrue(any(e.get("type") == "action" and "dev_mcp_search" in e.get("text", "")
                                for e in events))
            self.assertTrue(any(e.get("type") == "observation" and "easyeda-pro-mcp" in e.get("text", "")
                                for e in events))
            self.assertFalse(os.path.exists(os.path.join(self.root, ".docmind_mcp.json")))
        finally:
            config.STATE_ROOT = old_state_root

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
