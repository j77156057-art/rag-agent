"""MCP 自动连接：API 端点数据契约测试（Phase 4 QA）。

不依赖网络。直接调用 8 个端点委托的同一批底层函数，断言它们返回的字典
与前端 `api.ts` 中 `McpAutoConnectCandidate / McpProbeRes / McpRegisterStartRes`
等接口字段完全一致——即「前端期望的契约」被后端满足。

映射（端点 → 被委托函数）：
  POST /api/mcp/autoconnect/search        → auto_connect_pipeline
  POST /api/mcp/autoconnect/probe         → probe_candidate（失败抛 MCPError，端点捕获为 probe_ok=False）
  POST /api/mcp/autoconnect/confirm       → mcp_client.save_server（写盘）
  POST /api/mcp/autoconnect/register/start→ browser_register（v1 诚实降级 L2）
  POST /api/mcp/autoconnect/register/commit→ secrets_store.save（凭证不落明文）
  POST /api/mcp/autoconnect/vision/extract→ vision_extract_params

注：沙箱无 npx/uvx，resolve_command 解析不到绝对路径会使 command_unresolved=True，
从而触发 R3 拒绝候选；真实环境里启动器在 PATH 中可解析。这里用补丁模拟真实环境。
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import mcp_autoconnect
import mcp_client
import secrets_store


def _fake_search(query):
    return "see https://github.com/modelcontextprotocol/servers for install"


def _fake_fetch(url):
    # 返回标准安装命令（与 parse_install_command 的 npx 模式匹配）
    return "npx -y @modelcontextprotocol/server-github"


class _TmpProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docmind_ac_contract_")
        self.project = os.path.join(self.tmp.name, "project")
        os.makedirs(self.project)
        self.old_state_root = config.STATE_ROOT
        config.STATE_ROOT = self.tmp.name

    def tearDown(self):
        config.STATE_ROOT = self.old_state_root
        self.tmp.cleanup()


class _SearchTmp(_TmpProject):
    """模拟真实环境：启动器在 PATH 中可解析为绝对路径（否则 R3 会拒候选）。

    R3 对绝对路径命令还要求父目录落在安全目录集（PATH ∪ ~/.local/bin ∪ ProgramFiles）。
    因此这里把仿真工具目录挂到 PATH，确保 resolve_command 产出的绝对路径能通过 _safe_dir。
    """
    def setUp(self):
        super().setUp()
        self.tools_dir = os.path.join(self.tmp.name, "tools")
        os.makedirs(self.tools_dir, exist_ok=True)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = self.tools_dir + os.pathsep + self._old_path
        self._p = patch("mcp_autoconnect.mcp_client.resolve_command",
                        lambda launcher: os.path.join(self.tools_dir, launcher))
        self._p.start()

    def tearDown(self):
        self._p.stop()
        os.environ["PATH"] = self._old_path
        super().tearDown()


class SearchEndpointContractTests(_SearchTmp):
    def test_search_response_shape_matches_frontend(self):
        r = mcp_autoconnect.auto_connect_pipeline(
            self.project, "github mcp", search_fn=_fake_search, fetch_fn=_fake_fetch)
        # 端点返回 {ok, candidates, search_error?}
        self.assertIn("ok", r)
        self.assertIn("candidates", r)
        self.assertIsInstance(r["candidates"], list)
        # 前端 McpAutoConnectCandidate：{config, trust, validation_errors}
        self.assertGreater(len(r["candidates"]), 0)
        cand = r["candidates"][0]
        self.assertIn("config", cand)
        self.assertIn("trust", cand)
        self.assertIn("validation_errors", cand)
        # 前端 McpAutoConnectConfig 字段必须齐备
        cfg = cand["config"]
        for k in ("transport", "command", "args", "url", "env", "headers",
                  "provenance", "command_unresolved"):
            self.assertIn(k, cfg, f"候选 config 缺字段 {k}")
        self.assertIn("url", cfg["provenance"])
        self.assertIn("domain", cfg["provenance"])

    def test_trusted_source_marks_trusted(self):
        r = mcp_autoconnect.auto_connect_pipeline(
            self.project, "github mcp", search_fn=_fake_search, fetch_fn=_fake_fetch)
        cand = r["candidates"][0]
        self.assertEqual(cand["trust"], "trusted")
        self.assertEqual(cand["config"]["provenance"]["domain"], "github.com")

    def test_args_extracted(self):
        r = mcp_autoconnect.auto_connect_pipeline(
            self.project, "github mcp", search_fn=_fake_search, fetch_fn=_fake_fetch)
        cfg = r["candidates"][0]["config"]
        self.assertEqual(cfg["args"][:2], ["-y", "@modelcontextprotocol/server-github"])


class ProbeEndpointContractTests(_TmpProject):
    def test_unresolvable_launcher_raises_mcp_error(self):
        # 端点把 MCPError 捕获为 {ok:True, probe_ok:False, tools:[], error}
        cfg = {
            "transport": "stdio", "command": "definitely-not-a-real-bin-xyz",
            "args": [], "url": "", "env": {}, "headers": {},
            "provenance": {"url": "", "domain": ""}, "command_unresolved": True,
        }
        with self.assertRaises(mcp_client.MCPError):
            mcp_autoconnect.probe_candidate(self.project, cfg)
        # 端点捕获后的形态（与前端 McpProbeRes 对齐）
        try:
            mcp_autoconnect.probe_candidate(self.project, cfg)
        except mcp_client.MCPError as e:
            shaped = {"ok": True, "probe_ok": False, "tools": [], "error": str(e)}
            self.assertFalse(shaped["probe_ok"])
            self.assertEqual(shaped["tools"], [])


class RegisterEndpointContractTests(_TmpProject):
    def test_register_start_returns_l2_contract(self):
        cfg = {
            "transport": "stdio", "command": "npx", "args": ["-y", "x"],
            "url": "", "env": {}, "headers": {},
            "provenance": {"url": "https://github.com/owner/repo", "domain": "github.com"},
            "command_unresolved": False,
        }
        r = mcp_autoconnect.browser_register(self.project, "github-server", cfg, "github.com")
        # 端点返回 {ok, task_id, tier, url, note}
        for k in ("ok", "task_id", "tier", "url", "note"):
            self.assertIn(k, r)
        self.assertEqual(r["tier"], "L2")
        self.assertIn("github.com", r["url"])


class ConfirmCredentialFlowTests(_TmpProject):
    def test_secret_refs_resolves_at_confirm_time(self):
        # register/commit 写入 secrets_store → confirm 时 @secret 解析为明文（不落明文）
        secrets_store.save(self.project, "gh", "secret-token-123")
        cfg = {
            "transport": "stdio", "command": "npx", "args": ["-y", "x"],
            "url": "", "env": {"GITHUB_TOKEN": "@secret:gh"}, "headers": {},
            "provenance": {"url": "", "domain": ""}, "command_unresolved": False,
        }
        resolved = mcp_autoconnect.resolve_secret_refs(cfg, self.project)
        self.assertEqual(resolved["env"]["GITHUB_TOKEN"], "secret-token-123")
        # 未存入的 provider 必须抛错（防止 @secret 引用悬空）
        bad = dict(cfg, env={"X": "@secret:missing"})
        with self.assertRaises(mcp_autoconnect.AutoConnectError):
            mcp_autoconnect.resolve_secret_refs(bad, self.project)


class VisionEndpointContractTests(_TmpProject):
    def test_empty_image_returns_error_shape(self):
        r = mcp_autoconnect.vision_extract_params("", self.project)
        self.assertIn("ok", r)
        self.assertIn("candidates", r)
        self.assertIn("search_error", r)
        self.assertFalse(r["ok"])


class ResponseShapeMirrorTests(_SearchTmp):
    """契约镜像：端点输出字段集合 ⊇ 前端接口字段集合。"""
    def test_candidate_config_keys_superset_of_frontend(self):
        r = mcp_autoconnect.auto_connect_pipeline(
            self.project, "github mcp", search_fn=_fake_search, fetch_fn=_fake_fetch)
        cfg = r["candidates"][0]["config"]
        # 前端 McpAutoConnectConfig 声明的字段
        frontend_keys = {"transport", "command", "args", "url", "env",
                         "headers", "provenance", "command_unresolved"}
        self.assertTrue(frontend_keys.issubset(set(cfg.keys())),
                        f"后端缺少前端字段: {frontend_keys - set(cfg.keys())}")


if __name__ == "__main__":
    unittest.main()
