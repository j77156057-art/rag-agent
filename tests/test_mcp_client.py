"""mcp_client 单元测试：纯函数 + stdio/HTTP 双传输（用假 JSON-RPC 服务器，不依赖 uvx/Godot）。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp_client  # noqa: E402

# 假 stdio MCP 服务器：newline-delimited JSON-RPC，支持 initialize/tools/list/tools/call
FAKE_STDIO_SERVER = r'''
import sys, json
for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    m = msg.get("method")
    if m == "initialize":
        r = {"jsonrpc":"2.0","id":msg["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"0"}}}
    elif m == "notifications/initialized":
        continue
    elif m == "tools/list":
        r = {"jsonrpc":"2.0","id":msg["id"],"result":{"tools":[{"name":"echo","description":"echo","inputSchema":{"type":"object"}}]}}
    elif m == "tools/call":
        a = msg.get("params",{}).get("arguments",{}) or {}
        r = {"jsonrpc":"2.0","id":msg["id"],"result":{"content":[{"type":"text","text":str(a.get("text",""))}],"isError":False}}
    else:
        r = {"jsonrpc":"2.0","id":msg.get("id"),"error":{"code":-32601,"message":"method not found"}}
    sys.stdout.write(json.dumps(r) + "\n")
    sys.stdout.flush()
'''


class PureHelperTest(unittest.TestCase):
    def test_parse_sse_frames(self):
        data = (
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
            'data: [DONE]\n\n'
            'garbage line\n'
        )
        msgs = mcp_client.parse_sse_frames(data)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["id"], 1)

    def test_extract_text(self):
        text, structured, blocks, images = mcp_client.extract_text({
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
                {"type": "image", "data": "x", "mimeType": "image/png"},
            ],
            "structuredContent": {"a": 1},
        })
        self.assertEqual(text, "hello\nworld")
        self.assertEqual(structured, {"a": 1})
        self.assertEqual(blocks, ["image"])
        self.assertEqual(images, [{"data": "x", "mime_type": "image/png"}])

    def test_normalize_config(self):
        good = mcp_client.normalize_server_config(
            {"transport": "http", "url": "http://127.0.0.1:8080/mcp", "enabled": False})
        self.assertFalse(good["enabled"])
        with self.assertRaises(mcp_client.MCPError):
            mcp_client.normalize_server_config({"transport": "ws"})
        with self.assertRaises(mcp_client.MCPError):
            mcp_client.normalize_server_config({"transport": "stdio"})
        with self.assertRaises(mcp_client.MCPError):
            mcp_client.normalize_server_config({"transport": "http", "url": "ftp://x"})

    def test_resolve_command_absolute(self):
        # 绝对路径且存在时原样返回
        self.assertEqual(mcp_client.resolve_command(sys.executable), sys.executable)


class _FakeHttpHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, result, msg_id=2):
        body = json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        msg = json.loads(self.rfile.read(length).decode() or "{}")
        method = msg.get("method")
        if method == "initialize":
            self._reply({"protocolVersion": "2024-11-05", "capabilities": {}}, msg.get("id"))
        elif method == "tools/list":
            self._reply({"tools": [{"name": "ping", "description": "ping"}]}, msg.get("id"))
        elif method == "tools/call":
            args = msg.get("params", {}).get("arguments", {}) or {}
            self._reply({"content": [{"type": "text", "text": "pong:" + str(args.get("q", ""))}]},
                        msg.get("id"))
        else:
            self._reply({}, msg.get("id"))


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="docmind-mcp-")
        mcp_client.save_server(self.tmp, "fake_stdio", {
            "transport": "stdio", "command": sys.executable,
            "args": ["-c", FAKE_STDIO_SERVER], "enabled": True,
        })
        self.httpd = HTTPServer(("127.0.0.1", 0), _FakeHttpHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        mcp_client.save_server(self.tmp, "fake_http", {
            "transport": "http",
            "url": f"http://127.0.0.1:{self.port}/mcp",
            "enabled": True,
        })

    def tearDown(self):
        mcp_client.close_all()
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_stdio_probe_list_call(self):
        probe = mcp_client.probe_server(self.tmp, "fake_stdio")
        self.assertTrue(probe["ok"])
        self.assertEqual(probe["tool_count"], 1)
        tools = mcp_client.list_tools(self.tmp, "fake_stdio")
        self.assertEqual(tools["tools"][0]["name"], "echo")
        r = mcp_client.call_tool(self.tmp, "fake_stdio", "echo", {"text": "hi-mcp"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "hi-mcp")

    def test_http_probe_list_call(self):
        probe = mcp_client.probe_server(self.tmp, "fake_http")
        self.assertTrue(probe["ok"])
        self.assertEqual(probe["tool_count"], 1)
        r = mcp_client.call_tool(self.tmp, "fake_http", "ping", {"q": "abc"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "pong:abc")

    def test_mcp_lifecycle_hooks_wrap_real_call(self):
        events = []

        def observe(kind, payload):
            events.append((kind, dict(payload)))
            return {"blocked": False, "reason": "", "errors": []}

        with patch("hooks.run_workflow", side_effect=observe):
            result = mcp_client.call_tool(self.tmp, "fake_http", "ping", {"q": "hook"})
        self.assertTrue(result["ok"])
        self.assertEqual([kind for kind, _ in events], ["before_mcp", "after_mcp"])
        self.assertEqual(events[0][1]["connector"], "fake_http")
        self.assertEqual(events[0][1]["tool"], "ping")
        self.assertTrue(events[1][1]["ok"])
        self.assertGreaterEqual(events[1][1]["duration_ms"], 0)

    def test_before_mcp_hook_can_block_without_connecting(self):
        events = []

        def block(kind, payload):
            events.append(kind)
            return {"blocked": kind == "before_mcp", "reason": "需要人工审核", "errors": []}

        with patch("hooks.run_workflow", side_effect=block), \
                patch.object(mcp_client, "_session_for", side_effect=AssertionError("must not connect")):
            with self.assertRaises(mcp_client.MCPError) as ctx:
                mcp_client.call_tool(self.tmp, "fake_stdio", "echo", {"text": "blocked"})
        self.assertIn("人工审核", str(ctx.exception))
        self.assertEqual(events, ["before_mcp"])

    def test_after_mcp_hook_can_block_remote_result(self):
        def block_after(kind, _payload):
            return {"blocked": kind == "after_mcp", "reason": "结果需复核", "errors": []}

        with patch("hooks.run_workflow", side_effect=block_after):
            with self.assertRaises(mcp_client.MCPError) as ctx:
                mcp_client.call_tool(self.tmp, "fake_http", "ping", {"q": "review"})
        self.assertIn("结果未放行", str(ctx.exception))

    def test_default_servers_present_and_disabled_unreachable(self):
        configs = {s["key"]: s for s in mcp_client.server_configs(self.tmp)}
        self.assertIn("godot", configs)
        self.assertEqual(configs["godot"]["transport"], "stdio")
        self.assertEqual(configs["godot"]["args"][:1], ["godot-ai"])
        # unity 默认禁用：probe 应被门禁拒绝
        with self.assertRaises(mcp_client.MCPError):
            mcp_client.probe_server(self.tmp, "unity")

    def test_remove_custom_server(self):
        mcp_client.remove_server(self.tmp, "fake_http")
        keys = {s["key"] for s in mcp_client.server_configs(self.tmp)}
        self.assertNotIn("fake_http", keys)

    def test_disable_default_server_persists(self):
        mcp_client.remove_server(self.tmp, "unreal")
        user = mcp_client.load_user_servers(self.tmp)
        self.assertFalse(user["unreal"]["enabled"])

    def test_call_tool_with_fallback_tries_next_connector(self):
        def fake_call(_root, key, _name, _args, timeout=mcp_client.CALL_TIMEOUT):
            if key == "primary":
                raise mcp_client.MCPError("连接超时")
            return {"ok": True, "server": key, "text": "ok"}

        with patch.object(mcp_client, "call_tool", side_effect=fake_call):
            result = mcp_client.call_tool_with_fallback(
                self.tmp, "primary", "echo", {}, fallback_keys=["backup"])
        self.assertEqual(result["server"], "backup")
        self.assertEqual([item["key"] for item in result["attempts"]], ["primary", "backup"])

    def test_call_tool_with_fallback_records_all_failures(self):
        def fake_call(*_args, **_kwargs):
            raise mcp_client.MCPError("offline")

        with patch.object(mcp_client, "call_tool", side_effect=fake_call):
            with self.assertRaises(mcp_client.MCPError) as ctx:
                mcp_client.call_tool_with_fallback(
                    self.tmp, "primary", "echo", {}, fallback_keys=["backup"])
        self.assertEqual([item["key"] for item in ctx.exception.attempts],
                         ["primary", "backup"])

    def test_side_effect_fallback_is_opt_in(self):
        calls = []

        def fake_call(_root, key, _name, _args, timeout=mcp_client.CALL_TIMEOUT):
            calls.append(key)
            raise mcp_client.MCPError("timeout")

        with patch.object(mcp_client, "call_tool", side_effect=fake_call):
            with self.assertRaises(mcp_client.MCPError) as ctx:
                mcp_client.call_tool_with_fallback(
                    self.tmp, "primary", "write", {}, fallback_keys=["backup"],
                    side_effect=True)
        self.assertEqual(calls, ["primary"])
        self.assertFalse(ctx.exception.fallback_allowed)

    def test_mcp_retry_hook_block_stops_fallback_chain(self):
        calls = []

        def block_retry(kind, payload):
            if kind == "mcp_retry":
                return {"blocked": True, "reason": "网络变更需审核", "errors": []}
            return {"blocked": False, "reason": "", "errors": []}

        def fake_call(_root, key, _name, _args, timeout=mcp_client.CALL_TIMEOUT):
            calls.append(key)
            return {"ok": True, "server": key, "text": "should not run"}

        with patch("hooks.run_workflow", side_effect=block_retry), \
                patch.object(mcp_client, "call_tool", side_effect=fake_call):
            with self.assertRaises(mcp_client.MCPError) as ctx:
                mcp_client.call_tool_with_fallback(
                    self.tmp, "primary", "echo", {}, fallback_keys=["backup"])
        self.assertEqual(calls, [])
        self.assertTrue(ctx.exception.attempts[0]["blocked"])
        self.assertTrue(ctx.exception.fallback_allowed)


if __name__ == "__main__":
    unittest.main()
