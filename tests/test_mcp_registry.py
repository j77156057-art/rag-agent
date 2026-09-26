"""mcp_registry 单元测试：URL / 解析 / 推导 / 排序 / 缓存 / Registry→精选回落。

**全部离线**：联网一律通过注入 `http_get` / `registry_fn` 打桩，不触碰真实网络。
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import mcp_autoconnect
import mcp_registry

META = mcp_registry.OFFICIAL_META_KEY


def _entry(name="io.github.foo/bar", desc="A bar MCP server",
           repo="https://github.com/foo/bar", version="1.0.0",
           packages=None, remotes=None, is_latest=True, status="active"):
    srv = {"name": name, "description": desc, "version": version, "repository": {"url": repo}}
    if packages is not None:
        srv["packages"] = packages
    if remotes is not None:
        srv["remotes"] = remotes
    meta = {META: {"isLatest": is_latest, "status": status}}
    return {"server": srv, "_meta": meta}


def _npm_pkg(identifier="@modelcontextprotocol/server-github", hint="npx",
             env=None, runtime_args=None):
    pkg = {"registryType": "npm", "identifier": identifier, "transport": {"type": "stdio"}}
    if hint:
        pkg["runtimeHint"] = hint
    if runtime_args is not None:
        pkg["runtimeArguments"] = runtime_args
    if env is not None:
        pkg["environmentVariables"] = env
    return pkg


class PureFunctionTests(unittest.TestCase):
    def test_eda_relevance_filters_registry_substring_matches(self):
        jeda = {"provenance": {"server_name": "ai.jeda/jeda-ai",
                               "description": "Visual AI for mindmaps", "url": ""}}
        easyeda = {"provenance": {"server_name": "io.github.biosshot/easyeda-copilot",
                                  "description": "Schematic and PCB design", "url": "https://github.com/biosshot/easyeda-copilot"}}
        self.assertTrue(mcp_registry.is_eda_query("EDA"))
        self.assertFalse(mcp_registry.is_eda_candidate(jeda))
        self.assertTrue(mcp_registry.is_eda_candidate(easyeda))

    def test_registry_search_url(self):
        url = mcp_registry.registry_search_url("git hub", 3)
        self.assertTrue(url.startswith(mcp_registry.REGISTRY_BASE + "/v0.1/servers?"))
        self.assertIn("search=git+hub", url)
        self.assertIn("limit=3", url)

    def test_parse_multiversion_keeps_latest_only(self):
        payload = {"servers": [
            _entry(version="0.9.0", is_latest=False),
            _entry(version="1.0.0", is_latest=True),
        ]}
        servers = mcp_registry.parse_registry_response(payload)
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["version"], "1.0.0")

    def test_parse_deprecated_and_deleted_filtered(self):
        payload = {"servers": [
            _entry(name="a/keep", status="active"),
            _entry(name="a/dep", status="deprecated"),
            _entry(name="a/gone", status="deleted"),
        ]}
        names = [s["name"] for s in mcp_registry.parse_registry_response(payload)]
        self.assertEqual(names, ["a/keep"])

    def test_parse_empty_and_garbage_no_raise(self):
        for payload in (None, {}, {"servers": "oops"}, [], "junk", {"servers": [1, 2]}):
            self.assertEqual(mcp_registry.parse_registry_response(payload), [])

    def test_packages_derivation_npx_y_and_secret_env(self):
        srv = _entry(packages=[_npm_pkg(env=[
            {"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "isSecret": True, "isRequired": True},
            {"name": "LOG_LEVEL", "isSecret": False, "default": "info"},
        ], runtime_args=[{"value": "-y", "type": "positional"}])])["server"]
        cands = mcp_registry.server_to_candidates(srv)
        self.assertEqual(len(cands), 1)
        cfg = cands[0]
        self.assertEqual(cfg["transport"], "stdio")
        self.assertEqual(cfg["command"], "npx")
        self.assertEqual(cfg["args"], ["-y", "@modelcontextprotocol/server-github"])
        self.assertEqual(cfg["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"],
                         "@secret:GITHUB_PERSONAL_ACCESS_TOKEN")
        self.assertEqual(cfg["env"]["LOG_LEVEL"], "")
        self.assertIn("GITHUB_PERSONAL_ACCESS_TOKEN", cfg["provenance"]["required_env"])
        self.assertEqual(cfg["provenance"]["secret_specs"], [
            {"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "required": True},
        ])

    def test_npx_first_install_can_start_without_interactive_prompt(self):
        cfg = mcp_registry.server_to_candidates(_entry(packages=[_npm_pkg(
            identifier="easyeda-copilot-mcp", runtime_args=[]
        )])["server"])[0]
        self.assertEqual(cfg["args"], ["-y", "easyeda-copilot-mcp"])

    def test_optional_secret_is_metadata_only(self):
        srv = _entry(packages=[_npm_pkg(env=[
            {"name": "OPTIONAL_TOKEN", "isSecret": True, "isRequired": False},
        ])])["server"]
        cfg = mcp_registry.server_to_candidates(srv)[0]
        self.assertEqual(cfg["env"]["OPTIONAL_TOKEN"], "@secret:OPTIONAL_TOKEN")
        self.assertEqual(cfg["provenance"]["secret_specs"], [
            {"name": "OPTIONAL_TOKEN", "required": False},
        ])
        view = mcp_autoconnect._candidate_view(cfg, "trusted", [])
        self.assertEqual(view["secrets"], [{"name": "OPTIONAL_TOKEN", "required": False}])

    def test_launcher_from_registry_type_without_hint(self):
        pkg = {"registryType": "pypi", "identifier": "mcp-server-git"}
        cfg = mcp_registry.server_to_candidates(_entry(packages=[pkg])["server"])[0]
        self.assertEqual(cfg["command"], "uvx")
        self.assertEqual(cfg["args"], ["mcp-server-git"])

    def test_launcher_not_in_allowlist_dropped(self):
        pkg = {"registryType": "npm", "runtimeHint": "curl", "identifier": "evil/pkg"}
        self.assertEqual(mcp_registry.server_to_candidates(_entry(packages=[pkg])["server"]), [])

    def test_remotes_derivation_http_and_secret_header(self):
        rem = {"type": "streamable-http", "url": "https://smithery.ai/server/x",
               "headers": [
                   {"name": "Authorization", "isSecret": True,
                    "value": "Bearer sk-REALPLAINTEXTKEY000000000000"},
                   {"name": "X-Trace", "value": "on"},
               ]}
        cands = mcp_registry.server_to_candidates(_entry(remotes=[rem])["server"])
        self.assertEqual(len(cands), 1)
        cfg = cands[0]
        self.assertEqual(cfg["transport"], "http")
        self.assertEqual(cfg["url"], "https://smithery.ai/server/x")
        self.assertEqual(cfg["headers"]["Authorization"], "@secret:Authorization")  # 无明文
        self.assertEqual(cfg["headers"]["X-Trace"], "on")
        self.assertNotIn("REALPLAINTEXTKEY", json.dumps(cfg))

    def test_remotes_header_placeholder_keeps_prefix_and_uses_placeholder_name(self):
        # ①真机 bug：{"value":"Bearer {smithery_api_key}","isSecret":true}
        # → provider 取占位名、保留 "Bearer " 前缀（旧口径丢前缀 + provider 取表头名）
        rem = {"type": "streamable-http", "url": "https://server.smithery.ai/mcp",
               "headers": [{"name": "Authorization", "value": "Bearer {smithery_api_key}",
                            "isSecret": True, "isRequired": True}]}
        cfg = mcp_registry.server_to_candidates(_entry(remotes=[rem])["server"])[0]
        self.assertEqual(cfg["headers"]["Authorization"], "Bearer @secret:smithery_api_key")
        self.assertIn("smithery_api_key", cfg["headers"]["Authorization"])   # provider = 占位名
        self.assertNotIn("{", cfg["headers"]["Authorization"])               # 占位符已转引用
        self.assertNotIn("{smithery_api_key}", json.dumps(cfg))
        self.assertEqual(cfg["provenance"]["secret_specs"], [
            {"name": "smithery_api_key", "required": True},
        ])

    def test_remotes_secret_header_without_placeholder_uses_header_name(self):
        # ③无占位但 isSecret → 回退 @secret:<header_name>（与 env 口径一致）
        rem = {"type": "streamable-http", "url": "https://server.smithery.ai/mcp",
               "headers": [{"name": "X-Api-Key", "value": "", "isSecret": True}]}
        cfg = mcp_registry.server_to_candidates(_entry(remotes=[rem])["server"])[0]
        self.assertEqual(cfg["headers"]["X-Api-Key"], "@secret:X-Api-Key")

    def test_remotes_plain_header_passthrough(self):
        rem = {"type": "streamable-http", "url": "https://server.smithery.ai/mcp",
               "headers": [{"name": "X-Trace", "value": "on"}]}
        cfg = mcp_registry.server_to_candidates(_entry(remotes=[rem])["server"])[0]
        self.assertEqual(cfg["headers"]["X-Trace"], "on")

    def test_allowlist_parity_with_autoconnect(self):
        self.assertEqual(mcp_registry.ALLOWED_LAUNCHERS, mcp_autoconnect.ALLOWED_LAUNCHERS)

    def test_rank_prefers_packages_and_official_and_dedupes_repo(self):
        servers = [
            _entry(name="ai.smithery/remote", repo="https://github.com/foo/remote",
                   remotes=[{"type": "streamable-http", "url": "https://smithery.ai/s/r"}]),
            _entry(name="io.github.foo/local", repo="https://github.com/foo/remote",
                   packages=[_npm_pkg()]),
        ]
        cands = []
        for s in servers:
            cands.extend(mcp_registry.server_to_candidates(s["server"]))
        ranked = mcp_registry.rank_candidates(cands, "github")
        # 同 repository.url 只留最高分：本地 stdio（有 packages）胜出
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["transport"], "stdio")


class _TmpProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docmind_reg_")
        self.project = os.path.join(self.tmp.name, "project")
        os.makedirs(self.project)
        self.old_state_root = config.STATE_ROOT
        config.STATE_ROOT = self.tmp.name

    def tearDown(self):
        config.STATE_ROOT = self.old_state_root
        self.tmp.cleanup()


class _FakeCache:
    def __init__(self):
        self.data = {}
        self.gets = 0

    def get(self, key):
        self.gets += 1
        return self.data.get(key)

    def set(self, key, servers, now=None):
        self.data[key] = {"fetched_at": time.time() if now is None else now, "servers": servers}


class SearchRegistryTests(unittest.TestCase):
    def test_fetch_then_cache_hit_no_refetch(self):
        calls = {"n": 0}

        def http_get(url):
            calls["n"] += 1
            return json.dumps({"servers": [_entry(packages=[_npm_pkg()])]})

        cache = _FakeCache()
        cwd = os.getcwd()
        r1 = mcp_registry.search_registry("github", http_get=http_get, cache=cache, now=1000.0)
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["source"], "registry")
        self.assertEqual(calls["n"], 1)
        r2 = mcp_registry.search_registry("github", http_get=http_get, cache=cache, now=1001.0)
        self.assertEqual(r2["source"], "cache")
        self.assertEqual(calls["n"], 1)      # 缓存命中不重复拉取
        self.assertEqual(cwd, os.getcwd())   # 未因读写缓存改变工作目录

    def test_unreachable_uses_stale_cache(self):
        def boom(url):
            raise OSError("network down")

        cache = _FakeCache()
        cache.set("github", mcp_registry.parse_registry_response(
            {"servers": [_entry(packages=[_npm_pkg()])]}), now=1.0)
        r = mcp_registry.search_registry("github", http_get=boom, cache=cache, now=10 ** 9)
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "cache-stale")
        self.assertTrue(r["error"])

    def test_unreachable_no_cache_returns_none(self):
        def boom(url):
            raise OSError("network down")
        r = mcp_registry.search_registry("github", http_get=boom)
        self.assertFalse(r["ok"])
        self.assertEqual(r["source"], "none")

    def test_garbage_body_no_raise(self):
        r = mcp_registry.search_registry("x", http_get=lambda url: "<html>nope</html>")
        self.assertFalse(r["ok"])

    def test_transient_timeout_retried_then_ok(self):
        # 首次超时、第二次成功：有界重试生效，getter 恰好被调用 2 次，结果来自 registry。
        calls = {"n": 0}

        def getter(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("The read operation timed out")
            return json.dumps({"servers": [_entry(packages=[_npm_pkg()])]})

        r = mcp_registry.search_registry(
            "weather", http_get=getter, attempts=2, backoff=0.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "registry")
        self.assertEqual(calls["n"], 2)      # 首次失败重试一次，成功即止，绝不第三次

    def test_persistent_failure_gives_up(self):
        # getter 恒超时：重试用尽（attempts 次）后放弃，仍走软降级返回 ok=False。
        calls = {"n": 0}

        def getter(url):
            calls["n"] += 1
            raise TimeoutError("The read operation timed out")

        r = mcp_registry.search_registry(
            "weather", http_get=getter, attempts=2, backoff=0.0)
        self.assertFalse(r["ok"])
        self.assertEqual(calls["n"], 2)      # 调用次数 == attempts，不无限重试


class DiscoveryIntegrationTests(_TmpProject):
    def test_registry_hit_reported_source(self):
        cfg = {"transport": "stdio", "command": "npx",
               "args": ["-y", "@modelcontextprotocol/server-github"], "url": "", "env": {},
               "headers": {}, "provenance": {"url": "https://github.com/modelcontextprotocol/servers",
                                             "domain": "github.com", "registry": True},
               "command_unresolved": False}
        res = mcp_autoconnect.discover_from_need(
            self.project, "some brand new need", web_enabled=True,
            registry_fn=lambda need: {"ok": True, "candidates": [cfg], "source": "registry"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "registry")
        self.assertEqual(res["candidates"][0]["trust"], "trusted")

    def test_registry_disabled_without_web(self):
        calls = {"n": 0}

        def reg(need):
            calls["n"] += 1
            return {"ok": True, "candidates": [], "source": "registry"}

        res = mcp_autoconnect.discover_from_need(self.project, "github mcp",
                                                 web_enabled=False, registry_fn=reg)
        self.assertEqual(calls["n"], 0)              # 未联网 → 不触发 registry
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "offline")   # 走离线精选

    def test_registry_unreachable_falls_back_to_curated(self):
        res = mcp_autoconnect.auto_connect_pipeline(
            self.project, "github mcp",
            registry_fn=lambda need: {"ok": False, "candidates": [], "source": "none",
                                      "error": "boom"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "curated")
        self.assertTrue(res["candidates"])

    def test_registry_candidate_must_pass_validation(self):
        bad = {"transport": "stdio", "command": "bash", "args": ["; rm -rf /"],
               "url": "", "env": {}, "headers": {}, "provenance": {"registry": True},
               "command_unresolved": False}
        res = mcp_autoconnect.discover_from_need(
            self.project, "zzz-no-curated-match-xyz", web_enabled=True,
            registry_fn=lambda need: {"ok": True, "candidates": [bad], "source": "registry"})
        self.assertFalse(res["ok"])                  # 恶意 registry 候选被 R1-R9 拦下

    def test_registry_remotes_scope_path_not_dropped(self):
        # 回归 P1：remotes 的 /@scope/pkg/mcp 形态曾被 R6 误杀（path 含 's' + path 里 '@'），
        # 且被 _registry_layer 静默丢弃（views=0, err=''）。此处必须进入最终 candidates。
        rem = {"type": "streamable-http",
               "url": "https://server.smithery.ai/@smithery-ai/github/mcp", "headers": []}
        srv = _entry(name="ai.smithery/github", repo="https://github.com/smithery-ai/mcp",
                     remotes=[rem])["server"]
        cands = mcp_registry.server_to_candidates(srv)
        self.assertTrue(cands, "remotes 候选不应为空")
        res = mcp_autoconnect.discover_from_need(
            self.project, "zzz-no-curated-xyz", web_enabled=True,
            registry_fn=lambda need: {"ok": True, "candidates": cands, "source": "registry"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "registry")
        self.assertEqual(res["candidates"][0]["config"]["url"], rem["url"])
        self.assertEqual(res["candidates"][0]["trust"], "trusted")

    def test_registry_source_cache_passthrough(self):
        # O1：discover_from_need 的 source 应与 search_registry 词表对齐，透传 cache 来源。
        srv = _entry(name="io.github.foo/bar", packages=[_npm_pkg()])["server"]
        cands = mcp_registry.server_to_candidates(srv)
        res = mcp_autoconnect.discover_from_need(
            self.project, "zzz-no-curated-xyz", web_enabled=True,
            registry_fn=lambda need: {"ok": True, "candidates": cands, "source": "cache"})
        self.assertEqual(res["source"], "cache")


if __name__ == "__main__":
    unittest.main()
