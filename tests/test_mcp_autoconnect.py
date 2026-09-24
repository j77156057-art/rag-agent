"""mcp_autoconnect 后端单元测试：抽取 / 信任闸门 R1-R9 / 试连 / 注册降级。

覆盖纯函数与编排；不依赖网络。probe_candidate 仅用必然会失败的命令验证「受控执行抛 MCPError」，
不真正连外部服务。
"""
import os
import sys
import tempfile
import threading
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
    def _github_cand(self):
        return {"transport": "http", "command": "", "args": [],
                "url": "https://github.com/owner/repo/mcp",
                "env": {}, "headers": {}, "provenance": {"url": "https://github.com/owner/repo"}}

    def test_degrades_to_l2_when_browser_unavailable(self):
        # 无 Edge / 无 playwright → L2（本机 playwright 缺失时也走此分支，故补丁使其确定）
        with patch.object(mcp_autoconnect, "_edge_available", lambda: False), \
             patch.object(mcp_autoconnect, "_playwright_available", lambda: False):
            res = mcp_autoconnect.browser_register(self.project, "k", self._github_cand(), "p")
        self.assertTrue(res["ok"])
        self.assertEqual(res["tier"], "L2")
        # 回传的是官方来源页（注册入口），便于前端弹 L2 引导用户创建凭证
        self.assertIn("github.com", res["url"])

    def test_unknown_provider_degrades_to_l2(self):
        res = mcp_autoconnect.browser_register(self.project, "k", {"provenance": {}}, "some-unknown-provider")
        self.assertEqual(res["tier"], "L2")
        self.assertTrue(res["note"])

    def test_l2_only_provider_never_launches(self):
        # Brave 首版不自动 → L2，且不应触碰浏览器能力探测
        with patch.object(mcp_autoconnect, "_launch_edge",
                          side_effect=AssertionError("L2 不应启动浏览器")):
            res = mcp_autoconnect.browser_register(
                self.project, "k", {"provenance": {"url": "https://api.search.brave.com/app/keys"}},
                "brave")
        self.assertEqual(res["tier"], "L2")


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def fill(self, value):
        self.page.actions.append(("fill", self.selector, value))

    def click(self):
        self.page.actions.append(("click", self.selector))

    def check(self):
        self.page.actions.append(("check", self.selector))

    def select_option(self, value):
        self.page.actions.append(("select", self.selector, value))

    def input_value(self):
        return self.page.values.get(self.selector, "")

    def inner_text(self):
        return self.page.values.get(self.selector, "")


class _FakePage:
    def __init__(self, url="", body="", values=None, goto_url=None):
        self.url = url
        self.body = body
        self.values = values or {}
        self.actions = []
        self.goto_url = goto_url  # goto 后的最终 url（模拟跨域重定向）；None 则等于入参

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def inner_text(self, selector):
        return self.body

    def goto(self, url, **kwargs):
        self.url = self.goto_url or url

    def wait_for_load_state(self, *args, **kwargs):
        return None


class _FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class _FakePW:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _BrowserHarness(_TmpProject):
    """mock playwright（不真拉浏览器）：覆盖适配器选择 / 挑战分支 / 会话流转 / 凭证边界。"""

    def _cand(self, url="https://github.com/owner/repo", domain="github.com"):
        return {"transport": "http", "command": "", "args": [], "url": url,
                "env": {}, "headers": {},
                "provenance": {"url": url, "domain": domain}}

    def _stripe_cand(self):
        return self._cand("https://dashboard.stripe.com/test/apikeys", "dashboard.stripe.com")

    def _patch_browser(self, page):
        ctx = _FakeContext(page)
        pw = _FakePW()
        return ctx, pw, patch.object(mcp_autoconnect, "_launch_edge", lambda d: (pw, ctx)), \
            patch.object(mcp_autoconnect, "_edge_available", lambda: True), \
            patch.object(mcp_autoconnect, "_playwright_available", lambda: True)

    def tearDown(self):
        with mcp_autoconnect._BROWSER_SESSIONS_LOCK:
            mcp_autoconnect._BROWSER_SESSIONS.clear()
        super().tearDown()

    def _sessions_raw(self):
        with open(mcp_autoconnect._sessions_path(), encoding="utf-8") as fh:
            return fh.read()

    # ---- 适配器选择（数据驱动，纯函数）
    def test_adapter_selection_tiers(self):
        sel = mcp_autoconnect.select_provider_adapter
        # §7：L0 = Stripe 测试键；L1 = GitHub/Figma/Notion/Slack；L2 = Brave/Google Drive
        self.assertEqual(sel("github")["tier"], "L1")
        self.assertEqual(sel("figma")["tier"], "L1")
        self.assertEqual(sel("stripe test key")["tier"], "L0")
        self.assertEqual(sel("notion")["tier"], "L1")
        self.assertEqual(sel("slack")["tier"], "L1")
        self.assertEqual(sel("brave")["tier"], "L2")
        self.assertEqual(sel("google drive")["tier"], "L2")
        self.assertEqual(sel("github", self._cand())["name"], "github")
        self.assertIsNone(sel("totally-unknown-xyz"))

    # ---- 挑战检测（本地确定性）
    def test_detect_challenge_branches(self):
        self.assertEqual(mcp_autoconnect._detect_challenge(_FakePage(body="Welcome to the dashboard")), "")
        self.assertNotEqual(mcp_autoconnect._detect_challenge(_FakePage(body="Enter the code we emailed you")), "")
        self.assertNotEqual(mcp_autoconnect._detect_challenge(_FakePage(url="https://github.com/login")), "")

    # ---- L0：揭示已存在凭证（无创建动作，如 Stripe 测试键）→ 捕获 → secrets_store
    def test_l0_captures_credential_to_secrets_store(self):
        token = "sk_test_" + "a" * 24
        page = _FakePage(url="https://dashboard.stripe.com/test/apikeys", body="Test keys",
                         values={"input[readonly][type='text']": token})
        ctx, pw, p_launch, p_edge, p_pw = self._patch_browser(page)
        with p_launch, p_edge, p_pw, self.assertLogs("docmind.mcp_autoconnect", level="INFO") as logs:
            res = mcp_autoconnect.browser_register(self.project, "stripe", self._stripe_cand(), "stripe")
        self.assertEqual(res["tier"], "L0")
        self.assertEqual(res["note"].count("secrets_store"), 1)
        self.assertEqual(secrets_store.load(self.project, "stripe"), token)
        # 凭证绝不落会话 JSON / 绝不进日志
        self.assertNotIn(token, self._sessions_raw())
        self.assertNotIn(token, "\n".join(logs.output))
        sess = mcp_autoconnect._load_session(res["task_id"])
        self.assertEqual(sess["status"], "done")
        self.assertEqual(sess["step"], "captured")
        # L0 完成应关闭 live 会话
        self.assertNotIn(res["task_id"], mcp_autoconnect._BROWSER_SESSIONS)

    # ---- B1：创建长期令牌（§5 denylist）绝不自动提交 → 停 L1「submit」
    def test_github_never_auto_submits_token_creation(self):
        token = "ghp_" + "a" * 36
        page = _FakePage(url="https://github.com/settings/tokens/new", body="New token form",
                         values={"#new-oauth-token": token})
        ctx, pw, p_launch, p_edge, p_pw = self._patch_browser(page)
        with p_launch, p_edge, p_pw:
            res = mcp_autoconnect.browser_register(self.project, "github", self._cand(), "github")
        self.assertEqual(res["tier"], "L1")
        self.assertEqual(mcp_autoconnect._load_session(res["task_id"])["step"], "submit")
        # 关键：绝不自动点击「创建令牌」submit
        self.assertNotIn(("click", "button:has-text('Generate token')"), page.actions)
        # 也未捕获/落库任何凭证
        self.assertEqual(secrets_store.load(self.project, "github"), "")

    def test_figma_never_auto_submits_token_creation(self):
        token = "figd_" + "a" * 24
        page = _FakePage(url="https://www.figma.com/settings/", body="Settings",
                         values={"input[readonly][type='text']": token})
        ctx, pw, p_launch, p_edge, p_pw = self._patch_browser(page)
        with p_launch, p_edge, p_pw:
            res = mcp_autoconnect.browser_register(
                self.project, "figma",
                self._cand("https://www.figma.com/settings/", "figma.com"), "figma")
        self.assertEqual(res["tier"], "L1")
        self.assertEqual(mcp_autoconnect._load_session(res["task_id"])["step"], "submit")
        self.assertNotIn(("click", "button:has-text('Generate token')"), page.actions)
        self.assertEqual(secrets_store.load(self.project, "figma"), "")

    def test_fill_and_submit_respects_auto_submit_flag(self):
        base = {"fields": [{"selector": "#f", "action": "fill", "value": "v"}], "submit": "#go"}
        p_no = _FakePage()
        ok_no, _ = mcp_autoconnect._fill_and_submit(p_no, dict(base, auto_submit=False))
        self.assertTrue(ok_no)
        self.assertIn(("fill", "#f", "v"), p_no.actions)
        self.assertNotIn(("click", "#go"), p_no.actions)  # 未授权 → 不点
        self.assertTrue(mcp_autoconnect._submit_pending(dict(base, auto_submit=False)))

        p_yes = _FakePage()
        ok_yes, _ = mcp_autoconnect._fill_and_submit(p_yes, dict(base, auto_submit=True))
        self.assertTrue(ok_yes)
        self.assertIn(("click", "#go"), p_yes.actions)  # 授权 → 点
        self.assertFalse(mcp_autoconnect._submit_pending(dict(base, auto_submit=True)))

    def test_default_auto_submit_is_false(self):
        # 显式默认：声明 submit 的适配器默认不自动提交；仅揭示型（stripe）显式开
        self.assertFalse(mcp_autoconnect.PROVIDER_ADAPTERS["github"].get("auto_submit"))
        self.assertFalse(mcp_autoconnect.PROVIDER_ADAPTERS["figma"].get("auto_submit"))
        self.assertTrue(mcp_autoconnect.PROVIDER_ADAPTERS["stripe"].get("auto_submit"))
        # _submit_pending：有 submit 且未授权 → 需要用户点
        self.assertTrue(mcp_autoconnect._submit_pending({"submit": "#go"}))
        self.assertFalse(mcp_autoconnect._submit_pending({"submit": "", "auto_submit": False}))
        self.assertFalse(mcp_autoconnect._submit_pending({"submit": "#go", "auto_submit": True}))

    # ---- N1：越域重定向不得填表/捕获
    def test_untrusted_redirect_stops_before_fill(self):
        page = _FakePage(body="sign in", goto_url="https://evil.example/steal")
        ctx, pw, p_launch, p_edge, p_pw = self._patch_browser(page)
        with p_launch, p_edge, p_pw:
            res = mcp_autoconnect.browser_register(self.project, "github", self._cand(), "github")
        self.assertEqual(res["tier"], "L1")
        self.assertEqual(mcp_autoconnect._load_session(res["task_id"])["step"], "untrusted")
        self.assertEqual(page.actions, [])  # 越域不得发生任何填表动作

    def test_untrusted_redirect_after_fill_blocks_capture(self):
        class _RedirectOnFillPage(_FakePage):
            def locator(self, selector):
                page = self

                class _L(_FakeLocator):
                    def fill(self, value):
                        super().fill(value)
                        page.url = "https://evil.example/after"  # 填表后跳去越域

                return _L(page, selector)

        adapter = {"tier": "L0", "domains": ("github.com",),
                   "fields": [{"selector": "#f", "action": "fill", "value": "v"}],
                   "submit": "", "auto_submit": True,
                   "token_selectors": ["#t"], "token_pattern": r"ghp_[A-Za-z0-9]{30,}",
                   "secret_provider": "x"}
        page = _RedirectOnFillPage(url="https://github.com/settings/tokens/new",
                                   values={"#t": "ghp_" + "a" * 36})
        with patch.object(mcp_autoconnect, "_launch_edge", lambda d: (_FakePW(), _FakeContext(page))):
            out = mcp_autoconnect._browser_run("ac_untrusted", "/tmp/x", adapter,
                                               "https://github.com/settings/tokens/new")
        self.assertEqual(out["outcome"], "l1")
        self.assertEqual(out["step"], "untrusted")  # 未捕获越域页上的 token
        self.assertNotIn("token", out)

    # ---- L1：命中挑战 → 保活 + waiting_user + user_prompt
    def test_l1_on_challenge_keeps_session(self):
        page = _FakePage(url="https://github.com/settings/tokens/new",
                         body="Please verify your email to continue")
        ctx, pw, p_launch, p_edge, p_pw = self._patch_browser(page)
        with p_launch, p_edge, p_pw:
            res = mcp_autoconnect.browser_register(self.project, "github", self._cand(), "github")
        self.assertEqual(res["tier"], "L1")
        sess = mcp_autoconnect._load_session(res["task_id"])
        self.assertEqual(sess["status"], "waiting_user")
        self.assertEqual(sess["step"], "challenge")
        self.assertTrue(sess["user_prompt"])
        self.assertEqual(sess["provider"], "github")
        # live 会话保活（用户要看验证码）
        self.assertIn(res["task_id"], mcp_autoconnect._BROWSER_SESSIONS)

    # ---- L1 → resume 回 L0 判定
    def test_resume_advances_l1_to_l0(self):
        page_challenge = _FakePage(url="https://github.com/login", body="sign in to continue")
        ctx, pw, p_launch, p_edge, p_pw = self._patch_browser(page_challenge)
        with p_launch, p_edge, p_pw:
            start = mcp_autoconnect.browser_register(self.project, "github", self._cand(), "github")
        self.assertEqual(start["tier"], "L1")

        token = "ghp_" + "b" * 36
        page_ok = _FakePage(url="https://github.com/settings/tokens", body="Done",
                            values={"#new-oauth-token": token})
        with mcp_autoconnect._BROWSER_SESSIONS_LOCK:
            mcp_autoconnect._BROWSER_SESSIONS[start["task_id"]] = {
                "pw": pw, "context": ctx, "page": page_ok,
                "context_dir": "/nonexistent", "adapter": mcp_autoconnect.select_provider_adapter("github")}
        res = mcp_autoconnect.register_resume(start["task_id"])
        self.assertEqual(res["tier"], "L0")
        self.assertEqual(res["status"], "done")
        self.assertEqual(secrets_store.load(self.project, "github"), token)
        self.assertNotIn(token, self._sessions_raw())

    def test_resume_unknown_session_errors(self):
        res = mcp_autoconnect.register_resume("ac_does_not_exist")
        self.assertFalse(res["ok"])
        self.assertEqual(res["tier"], "L2")

    # ---- commit：只回 provider 名，凭证只进 secrets_store
    def test_commit_stores_and_marks_done(self):
        mcp_autoconnect._persist_session("ac_commit", "L1", "https://github.com/x", "/tmp/x",
                                         status="waiting_user", provider="github", root=self.project)
        token = "ghp_" + "c" * 36
        res = mcp_autoconnect.register_commit(self.project, "ac_commit", {"github": token})
        self.assertTrue(res["ok"])
        self.assertEqual(res["stored"], ["github"])
        self.assertNotIn(token, str(res))
        self.assertEqual(secrets_store.load(self.project, "github"), token)
        self.assertEqual(mcp_autoconnect._load_session("ac_commit")["status"], "done")
        self.assertNotIn(token, self._sessions_raw())

    def test_commit_empty_without_capture_fails(self):
        # 空凭证 + 会话无已捕获凭证 → 不静默成功
        res = mcp_autoconnect.register_commit(self.project, "ac_no_such_session", {})
        self.assertFalse(res["ok"])
        self.assertEqual(res["stored"], [])
        self.assertTrue(res["error"])

    def test_commit_empty_reuses_captured_provider(self):
        # 空凭证但会话已在本轮捕获到凭证 → stored=[provider]，不重复要明文
        token = "sk_test_" + "e" * 24
        page = _FakePage(url="https://dashboard.stripe.com/test/apikeys", body="Test keys",
                         values={"input[readonly][type='text']": token})
        ctx, pw, p_launch, p_edge, p_pw = self._patch_browser(page)
        with p_launch, p_edge, p_pw:
            start = mcp_autoconnect.browser_register(self.project, "stripe", self._stripe_cand(), "stripe")
        self.assertEqual(start["tier"], "L0")
        res = mcp_autoconnect.register_commit(self.project, start["task_id"], {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["stored"], ["stripe"])
        self.assertNotIn(token, str(res))

    def test_commit_idempotent(self):
        mcp_autoconnect._persist_session("ac_idem", "L1", "https://github.com/x", "/tmp/x",
                                         status="waiting_user", provider="github", root=self.project)
        token = "ghp_" + "f" * 36
        first = mcp_autoconnect.register_commit(self.project, "ac_idem", {"github": token})
        second = mcp_autoconnect.register_commit(self.project, "ac_idem", {"github": token})
        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(first["stored"], second["stored"])
        self.assertEqual(secrets_store.load(self.project, "github"), token)

    # ---- 线程亲和：所有触 playwright 的操作必须在同一 executor 线程
    def test_browser_ops_run_on_single_executor_thread(self):
        token = "sk_test_" + "d" * 24
        names = []

        def fake_launch(context_dir):
            names.append(threading.current_thread().name)
            page = _FakePage(url="https://dashboard.stripe.com/test/apikeys", body="Test keys",
                             values={"input[readonly][type='text']": token})
            return _FakePW(), _FakeContext(page)

        with patch.object(mcp_autoconnect, "_launch_edge", fake_launch), \
             patch.object(mcp_autoconnect, "_edge_available", lambda: True), \
             patch.object(mcp_autoconnect, "_playwright_available", lambda: True):
            r1 = mcp_autoconnect.browser_register(self.project, "stripe", self._stripe_cand(), "stripe")
        self.assertEqual(r1["tier"], "L0")
        self.assertTrue(names)
        self.assertTrue(all(n.startswith("mcp-browser") for n in names), names)
        self.assertNotEqual(names[0], threading.current_thread().name)
        self.assertTrue(all(n == names[0] for n in names), names)  # 单 worker → 同线程

        # resume 复用 live page 时不触发 relaunch，但仍必须由同一 executor 线程执行
        mcp_autoconnect._persist_session("ac_resume_thread", "L1",
                                         "https://dashboard.stripe.com/test/apikeys",
                                         "/nonexistent", status="waiting_user",
                                         provider="stripe", root=self.project)
        with mcp_autoconnect._BROWSER_SESSIONS_LOCK:
            mcp_autoconnect._BROWSER_SESSIONS["ac_resume_thread"] = {
                "pw": _FakePW(), "context": _FakeContext(_FakePage()),
                "page": _FakePage(url="https://dashboard.stripe.com/test/apikeys",
                                  values={"input[readonly][type='text']": token}),
                "context_dir": "/nonexistent",
                "adapter": mcp_autoconnect.select_provider_adapter("stripe")}
        r2 = mcp_autoconnect.register_resume("ac_resume_thread")
        self.assertEqual(r2["tier"], "L0")
        self.assertTrue(all(n.startswith("mcp-browser") for n in names), names)

    # ---- N3：resume 落库缺 root 不得假成功
    def test_resume_l0_without_root_not_done(self):
        token = "sk_test_" + "g" * 24
        mcp_autoconnect._persist_session("ac_noroot", "L1",
                                         "https://dashboard.stripe.com/test/apikeys",
                                         "/nonexistent", status="waiting_user",
                                         provider="stripe", root="")  # root 缺失
        with mcp_autoconnect._BROWSER_SESSIONS_LOCK:
            mcp_autoconnect._BROWSER_SESSIONS["ac_noroot"] = {
                "pw": _FakePW(), "context": _FakeContext(_FakePage()),
                "page": _FakePage(url="https://dashboard.stripe.com/test/apikeys",
                                  values={"input[readonly][type='text']": token}),
                "context_dir": "/nonexistent",
                "adapter": mcp_autoconnect.select_provider_adapter("stripe")}
        res = mcp_autoconnect.register_resume("ac_noroot")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("error"))
        # 绝不置 done 造成假成功
        self.assertNotEqual(mcp_autoconnect._load_session("ac_noroot").get("status"), "done")

    # ---- 导航越域拒绝（防跨域填凭证）
    def test_provider_url_trust_gate(self):
        adapter = mcp_autoconnect.select_provider_adapter("github")
        self.assertTrue(mcp_autoconnect._provider_url_trusted("https://github.com/settings/tokens", adapter))
        self.assertFalse(mcp_autoconnect._provider_url_trusted("https://evil.example/x", adapter))


if __name__ == "__main__":
    unittest.main()
