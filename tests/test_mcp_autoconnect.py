"""mcp_autoconnect 后端单元测试：抽取 / 信任闸门 R1-R9 / 试连 / 注册降级。

覆盖纯函数与编排；不依赖网络。probe_candidate 仅用必然会失败的命令验证「受控执行抛 MCPError」，
不真正连外部服务。
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import config
import mcp_autoconnect
import mcp_client
import secrets_store


class _TmpProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docmind_ac_")
        self.project = os.path.join(self.tmp.name, "project")
        os.makedirs(self.project)
        self.old_state_root = config.STATE_ROOT
        config.STATE_ROOT = self.tmp.name

    def tearDown(self):
        config.STATE_ROOT = self.old_state_root
        self.tmp.cleanup()


class ParseInstallCommandTests(unittest.TestCase):
    def test_uvx(self):
        self.assertEqual(mcp_autoconnect.parse_install_command("uvx mcp-server-foo"),
                         ["uvx", "mcp-server-foo"])

    def test_npx_y_flag(self):
        self.assertEqual(mcp_autoconnect.parse_install_command("npx -y @modelcontextprotocol/server-github"),
                         ["npx", "-y", "@modelcontextprotocol/server-github"])

    def test_npx_without_y(self):
        self.assertEqual(mcp_autoconnect.parse_install_command("run: npx some-pkg arg1"),
                         ["npx", "some-pkg", "arg1"])

    def test_python_module(self):
        self.assertEqual(mcp_autoconnect.parse_install_command("python -m http.server 8000"),
                         ["python", "-m", "http.server", "8000"])

    def test_docker_run(self):
        self.assertEqual(mcp_autoconnect.parse_install_command("docker run -i --rm image/mcp"),
                         ["docker", "run", "-i", "--rm", "image/mcp"])

    def test_no_match_returns_none(self):
        self.assertIsNone(mcp_autoconnect.parse_install_command("just some random text"))
        self.assertIsNone(mcp_autoconnect.parse_install_command(""))

    def test_http_endpoint_not_parsed_as_launcher(self):
        # 纯 http URL 不应被识别成 stdio 启动器
        self.assertIsNone(mcp_autoconnect.parse_install_command("https://github.com/x/y/mcp"))


class TrustGateTests(_TmpProject):
    def _good_stdio(self, **over):
        cand = {
            "transport": "stdio", "command": sys.executable, "args": ["-m", "http.server"],
            "url": "", "env": {}, "headers": {},
            "provenance": {"url": "https://github.com/x/y", "domain": "github.com"},
            "command_unresolved": False,
        }
        cand.update(over)
        return cand

    def test_r1_rejects_unknown_field(self):
        ok, errs = mcp_autoconnect.validate_extracted_config(
            self._good_stdio(run_script="evil"))
        self.assertFalse(ok)
        self.assertTrue(any("未知字段" in e for e in errs))

    def test_r2_bad_transport(self):
        ok, errs = mcp_autoconnect.validate_extracted_config(self._good_stdio(transport="ftp"))
        self.assertFalse(ok)
        self.assertTrue(any("transport" in e for e in errs))

    def test_r3_unresolved_command(self):
        ok, errs = mcp_autoconnect.validate_extracted_config(
            self._good_stdio(command="unlikely-launcher-xyz", command_unresolved=True))
        self.assertFalse(ok)
        self.assertTrue(any("绝对路径" in e for e in errs))

    def test_r3_safe_absolute_path_passes(self):
        # sys.executable 目录加入 PATH，使 _safe_dir 命中
        with patch.dict(os.environ, {"PATH": os.path.dirname(sys.executable)
                                      + os.pathsep + os.environ.get("PATH", "")}):
            ok, errs = mcp_autoconnect.validate_extracted_config(self._good_stdio())
        self.assertTrue(ok, errs)

    def test_r4_shell_metachar_rejected(self):
        ok, errs = mcp_autoconnect.validate_extracted_config(
            self._good_stdio(args=["-m", "http.server; rm -rf /"]))
        self.assertFalse(ok)
        self.assertTrue(any("shell 元字符" in e for e in errs))

    def test_r5_args_shape(self):
        ok, errs = mcp_autoconnect.validate_extracted_config(
            self._good_stdio(args="not-a-list"))
        self.assertFalse(ok)
        self.assertTrue(any("args" in e for e in errs))

    def test_r6_url_host_trusted(self):
        cand = {"transport": "http", "command": "", "args": [],
                "url": "https://github.com/x/y/mcp", "env": {}, "headers": {},
                "provenance": {"domain": "github.com"}, "command_unresolved": False}
        ok, errs = mcp_autoconnect.validate_extracted_config(cand)
        self.assertTrue(ok, errs)

    def test_r6_url_with_credentials_rejected(self):
        cand = {"transport": "http", "command": "", "args": [],
                "url": "https://u:p@github.com/x/y/mcp", "env": {}, "headers": {},
                "provenance": {"domain": "github.com"}, "command_unresolved": False}
        ok, errs = mcp_autoconnect.validate_extracted_config(cand)
        self.assertFalse(ok)
        self.assertTrue(any("用户信息" in e for e in errs))

    def test_r7_secret_in_env_rejected(self):
        ok, errs = mcp_autoconnect.validate_extracted_config(
            self._good_stdio(env={"API_KEY": "sk-abcdEFGH1234567890wxyz"}))
        self.assertFalse(ok)
        self.assertTrue(any("密钥" in e for e in errs))

    def test_r8_untrusted_domain_rejected(self):
        cand = self._good_stdio(provenance={"url": "https://evil.example/mcp", "domain": "evil.example"})
        ok, errs = mcp_autoconnect.validate_extracted_config(cand)
        self.assertFalse(ok)
        self.assertTrue(any("不可信" in e for e in errs))


class BuildCandidateTests(_TmpProject):
    def test_resolves_launcher_to_absolute(self):
        with patch.dict(os.environ, {"PATH": os.path.dirname(sys.executable)
                                      + os.pathsep + os.environ.get("PATH", "")}):
            cand = mcp_autoconnect.build_candidate(["python", "-m", "http.server"])
        self.assertTrue(os.path.isabs(cand["command"]))
        self.assertFalse(cand["command_unresolved"])
        self.assertEqual(cand["args"], ["-m", "http.server"])

    def test_unresolved_launcher_flagged(self):
        cand = mcp_autoconnect.build_candidate(["unlikely-launcher-xyz", "foo"])
        self.assertTrue(cand["command_unresolved"])


class IsTrustedDomainTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(mcp_autoconnect.is_trusted_domain("github.com"))

    def test_subdomain(self):
        self.assertTrue(mcp_autoconnect.is_trusted_domain("raw.githubusercontent.com"))

    def test_untrusted(self):
        self.assertFalse(mcp_autoconnect.is_trusted_domain("evil.example"))


class RenderDiagnosticSvgTests(unittest.TestCase):
    def test_contains_labels_and_failed_node(self):
        svg = mcp_autoconnect.render_diagnostic_svg(
            failed_at="extract",
            stages=[{"key": k, "label": lbl, "state": "done" if k != "extract" else "fail"}
                    for k, lbl in mcp_autoconnect.DEFAULT_DIAG_STAGES],
        )
        self.assertIn("搜索", svg)
        self.assertIn("确认", svg)
        self.assertIn('data-state="fail"', svg)
        self.assertNotIn("配置完整", svg)  # 必须用 Phase 2 集合，非 Phase 1 旧集合


class AutoConnectPipelineTests(_TmpProject):
    def test_http_endpoint_from_trusted_domain_extracted(self):
        def search_fn(_q):
            return "see https://github.com/owner/repo"
        def fetch_fn(url):
            return "Connect via Streamable HTTP at https://github.com/owner/repo/mcp"
        res = mcp_autoconnect.auto_connect_pipeline(
            self.project, "my-custom-http-connector", search_fn=search_fn, fetch_fn=fetch_fn)
        self.assertTrue(res["ok"])
        self.assertTrue(res["candidates"])
        top = res["candidates"][0]
        self.assertEqual(top["config"]["transport"], "http")
        self.assertTrue(top["config"]["url"].endswith("/mcp"))
        self.assertEqual(top["trust"], "trusted")

    def test_untrusted_domain_marked_not_auto_filled(self):
        def search_fn(_q):
            return "https://evil.example/page"
        def fetch_fn(url):
            return "Install: npx -y @evil/mcp"
        res = mcp_autoconnect.auto_connect_pipeline(
            self.project, "evil mcp", search_fn=search_fn, fetch_fn=fetch_fn)
        # R8：不可信来源不产出可信候选
        self.assertFalse(any(c["trust"] == "trusted" for c in res["candidates"]))

    def test_no_sources_no_candidates(self):
        res = mcp_autoconnect.auto_connect_pipeline(
            self.project, "anything", search_fn=lambda _q: "", fetch_fn=lambda _u: "")
        self.assertFalse(res["ok"])


class ResolveSecretRefsTests(_TmpProject):
    def test_resolves_stored_provider(self):
        secrets_store.save(self.project, "openai-edge", "topsecret-value")
        cfg = {"transport": "stdio", "command": "x", "args": [],
               "env": {"API_KEY": "@secret:openai-edge"}, "headers": {}}
        out = mcp_autoconnect.resolve_secret_refs(cfg, self.project)
        self.assertEqual(out["env"]["API_KEY"], "topsecret-value")

    def test_missing_provider_raises(self):
        cfg = {"transport": "stdio", "command": "x", "args": [],
               "env": {"API_KEY": "@secret:nope"}, "headers": {}}
        with self.assertRaises(mcp_autoconnect.AutoConnectError):
            mcp_autoconnect.resolve_secret_refs(cfg, self.project)


class ProbeCandidateTests(_TmpProject):
    def test_unresolvable_command_raises_mcp_error(self):
        cand = {"transport": "stdio", "command": "definitely-not-a-real-launcher-xyz",
                "args": [], "url": "", "env": {}, "headers": {}, "command_unresolved": True}
        with self.assertRaises(mcp_client.MCPError):
            mcp_autoconnect.probe_candidate(self.project, cand)


class BrowserRegisterTests(_TmpProject):
    def test_degrades_to_l2_with_url(self):
        cand = {"transport": "http", "command": "", "args": [],
                "url": "https://github.com/owner/repo/mcp",
                "env": {}, "headers": {}, "provenance": {"url": "https://github.com/owner/repo"}}
        res = mcp_autoconnect.browser_register(self.project, "k", cand, "p")
        self.assertTrue(res["ok"])
        self.assertEqual(res["tier"], "L2")
        # 回传的是官方来源页（注册入口），便于前端弹 L2 引导用户创建凭证
        self.assertIn("github.com", res["url"])


if __name__ == "__main__":
    unittest.main()
