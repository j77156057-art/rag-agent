"""MCP 自动连接：抽取 → 信任闸门 → 试连 → 注册代管。

核心不变量（见 ARCHITECTURE.md §2.2 / IMPL-PLAN §1）：
- 抽取层**只产出数据**，绝不调用 subprocess（`subprocess.Popen` 只在 `mcp_client.py:405`）。
- 真实执行只有两处：`probe_candidate`（受控命令探 tools/list，用毕即关）与
  `confirm`（用户显式 `user_ack` 后 `save_server` 写盘）。
- 不可信网页/截图正文先过 `parse_install_command` 确定性解析，再过 R1-R9 信任闸门，
  到不了 `Popen`。

本模块全部为纯函数 + 编排；浏览器代管 `browser_register` 的 playwright 仅在被调用时
惰性 import，不影响其它路径。
"""
from __future__ import annotations

import json
import os
import re
import time
import base64
import urllib.request
from typing import Any, Callable, Optional

import mcp_client
import secrets_store
import project_state
from mcp_server_index import match_curated_server, curated_entry_to_config


# ---------------------------------------------------------------- 常量（白名单，与 R3 并存）

ALLOWED_LAUNCHERS: frozenset[str] = frozenset({
    "uvx", "npx", "bunx", "bun", "node", "python", "python3",
    "pipx", "docker", "deno", "go", "poetry",
})

TRUSTED_SOURCE_DOMAINS: frozenset[str] = frozenset({
    "github.com", "raw.githubusercontent.com", "pypi.org", "npmjs.com",
    "www.npmjs.com", "modelcontextprotocol.io", "smithery.ai", "glama.ai",
})

# 候选只允许这些字段；任何额外键（如 run_script / post_install）来自不可信文本 → 丢弃
ALLOWED_KEYS: frozenset[str] = frozenset({
    "transport", "command", "args", "url", "env", "headers",
    "label", "key", "provenance", "command_unresolved",
})

SECRET_RE = re.compile(r"^@secret:(.+)$")
SHELL_META_RE = re.compile(r"[;|&$><\n\r(){}\[\]*?~`#!]")
ALLOWED_URL_RE = re.compile(r"^https://[a-z0-9.-]+(/[^\\s]*)?$", re.I)
HTTP_ENDPOINT_RE = re.compile(r"https://[^\s)'\"`]+/mcp[^\s)'\"`]*", re.I)

# 诊断图默认 6 节点（Phase 2 C5 集合，非 Phase 1 §6.1 旧集合）
DEFAULT_DIAG_STAGES = [
    ("search", "搜索"), ("fetch", "抓取"), ("extract", "抽取"),
    ("validate", "校验"), ("probe", "试连"), ("confirm", "确认"),
]

# 浏览器级 UA：GitHub 等站点对朴素 UA 直接 403 / 反爬；贴近 Chrome 才能稳定取正文。
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class AutoConnectError(Exception):
    """抽取/校验/注册层的可展示错误（非 MCPError）。"""


# ---------------------------------------------------------------- 纯函数（无网络、无 subprocess、可离线单测）

def _split_tokens(rest: str) -> list[str]:
    return [t for t in rest.split() if t]


# 每条：正则 + 把匹配组拼成 argv（argv[0] 为裸启动器 token）
_LAUNCHER_PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], list[str]]]] = [
    (re.compile(r"\buvx\s+([^\s]+)(.*)", re.S),
     lambda m: ["uvx", m.group(1)] + _split_tokens(m.group(2))),
    (re.compile(r"\bnpx\s+(-y\s+)?([^\s]+)(.*)", re.S),
     lambda m: ["npx"] + (["-y"] if m.group(1) else []) + [m.group(2)] + _split_tokens(m.group(3))),
    (re.compile(r"\bbunx\s+([^\s]+)(.*)", re.S),
     lambda m: ["bunx", m.group(1)] + _split_tokens(m.group(2))),
    (re.compile(r"\bpython3?\s+-m\s+([^\s]+)(.*)", re.S),
     lambda m: ["python", "-m", m.group(1)] + _split_tokens(m.group(2))),
    (re.compile(r"\bnode\s+([^\s]+)(.*)", re.S),
     lambda m: ["node", m.group(1)] + _split_tokens(m.group(2))),
    (re.compile(r"\bpipx\s+run\s+([^\s]+)(.*)", re.S),
     lambda m: ["pipx", "run", m.group(1)] + _split_tokens(m.group(2))),
    (re.compile(r"\bdocker\s+run\b(.*)", re.S),
     lambda m: ["docker", "run"] + _split_tokens(m.group(1))),
    (re.compile(r"\bdeno\s+run\s+(.*)", re.S),
     lambda m: ["deno", "run"] + _split_tokens(m.group(1))),
    (re.compile(r"\bgo\s+run\s+(.*)", re.S),
     lambda m: ["go", "run"] + _split_tokens(m.group(1))),
    (re.compile(r"\bpoetry\s+run\s+(.*)", re.S),
     lambda m: ["poetry", "run"] + _split_tokens(m.group(1))),
]


def parse_install_command(text: str) -> Optional[list[str]]:
    """确定性解析官方安装范式，返回 argv 列表（argv[0] 为裸启动器 token）或 None。

    识别：uvx / npx -y / bunx / python -m / node / pipx run / docker run /
    deno run / go run / poetry run。不触 PATH、不 subprocess。
    返回 None 表示确定性解析未命中。
    """
    if not text:
        return None
    samples = [text] + [ln.strip() for ln in text.splitlines() if ln.strip()]
    for sample in samples:
        for pat, build in _LAUNCHER_PATTERNS:
            m = pat.search(sample)
            if m:
                return build(m)
    return None


def _domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)/?", url or "")
    return m.group(1).lower() if m else ""


def is_trusted_domain(host: str) -> bool:
    """host 在 TRUSTED_SOURCE_DOMAINS 或与其同注册域（含子域）。"""
    host = (host or "").lower()
    if not host:
        return False
    if host in TRUSTED_SOURCE_DOMAINS:
        return True
    return any(host == d or host.endswith("." + d) for d in TRUSTED_SOURCE_DOMAINS)


def _safe_dir(cmd: str) -> bool:
    """绝对路径命令所在目录是否落在安全目录集（PATH ∪ ~/.local/bin ∪ ProgramFiles）。"""
    if not os.path.isabs(cmd):
        return False
    parent = os.path.dirname(cmd)
    safe = set(os.environ.get("PATH", "").split(os.pathsep))
    safe.add(os.path.join(os.path.expanduser("~"), ".local", "bin"))
    prog = os.environ.get("ProgramFiles")
    if prog:
        safe.add(prog)
    progx = os.environ.get("ProgramFiles(x86)")
    if progx:
        safe.add(progx)
    return parent in safe


def _looks_like_secret(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    if re.search(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._-]{20,})", v):
        return True
    if len(v) >= 40 and re.fullmatch(r"[A-Za-z0-9+/=_.-]{40,}", v):
        return True
    return False


def validate_extracted_config(cand: dict[str, Any]) -> tuple[bool, list[str]]:
    """信任闸门 R1-R9。返回 (ok, errors)。

    模型辅助输出也强制过此（权威裁决）。R3 已收紧为「抽取阶段解析绝对路径，
    候选不保留裸启动器」——若 command_unresolved 为 True 直接 error。
    """
    errors: list[str] = []
    if not isinstance(cand, dict):
        return False, ["候选必须是对象"]

    # R1 结构白名单
    for k in cand:
        if k not in ALLOWED_KEYS:
            errors.append(f"拒绝未知字段 {k}（可能来自不可信文本）")

    # R2 transport
    transport = cand.get("transport")
    if transport not in ("stdio", "http"):
        errors.append("transport 仅支持 stdio 或 http")

    # R3 启动器 / 路径
    if transport == "stdio":
        if cand.get("command_unresolved"):
            errors.append("启动器未在环境中解析到绝对路径，需手动确认/安装")
        else:
            cmd = cand.get("command") or ""
            if not cmd:
                errors.append("stdio 缺少 command")
            elif os.path.isabs(cmd):
                base = os.path.basename(cmd)
                base_name = base[:-4] if base.lower().endswith(".exe") else base
                if not _safe_dir(cmd) or base_name not in ALLOWED_LAUNCHERS:
                    errors.append("command 绝对路径不在安全目录或启动器不在白名单")
            else:
                base = os.path.basename(cmd)
                base_name = base[:-4] if base.lower().endswith(".exe") else base
                if base_name not in ALLOWED_LAUNCHERS:
                    errors.append("command 启动器不在白名单")

    # R4 shell 元字符拒绝
    scan = [str(cand.get("command") or "")]
    scan += [str(a) for a in (cand.get("args") or [])]
    for sec in ("env", "headers"):
        for val in (cand.get(sec) or {}).values():
            scan.append(str(val))
    for piece in scan:
        if SHELL_META_RE.search(piece):
            errors.append("拒绝 shell 元字符（命令/参数/环境变量中检测到 ; | & $ 等）")
            break

    # R5 args 形状
    args = cand.get("args")
    if args is not None:
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            errors.append("args 必须是字符串列表")
        else:
            for a in args:
                if len(a) > 256:
                    errors.append("args 单元素超过 256 字符上限")
                    break

    # R6 url 方案/主机（http）
    if transport == "http":
        url = cand.get("url") or ""
        if not url:
            errors.append("http 缺少 url")
        elif not ALLOWED_URL_RE.match(url) or "@" in url:
            errors.append("url 必须是 https 且无用户信息（拒绝 http://u:p@host）")
        else:
            host = _domain_of(url)
            if not is_trusted_domain(host):
                errors.append(f"url 主机 {host} 不在可信来源域")

    # R7 密钥不来自网页
    for sec in ("env", "headers"):
        for val in (cand.get(sec) or {}).values():
            if _looks_like_secret(str(val)):
                errors.append("疑似密钥，请走注册代管写入 secrets_store，勿从网页直接填入")
                break

    # R8 来源可信度（决策#1：拒非官方域名命令）
    prov = cand.get("provenance") or {}
    domain = prov.get("domain") or ""
    if transport == "stdio" and cand.get("command") and domain and not is_trusted_domain(domain):
        errors.append(f"来源 {domain} 不可信，拒绝自动抽取命令（请手动确认）")

    return (len(errors) == 0, errors)


def render_diagnostic_svg(stages: Optional[list[dict[str, Any]]] = None,
                          failed_at: Optional[str] = None) -> str:
    """确定性生成连接诊断 SVG（无 emoji、stroke=currentColor，引用 Token 变量）。

    stages 为 [{'key','label','state'}]；state ∈ done/active/fail/warn/idle。
    failed_at 为卡住的 stage key（高亮 fail/warn）。无 stages 时用默认 6 节点。
    """
    stages = stages or [{"key": k, "label": lbl, "state": "idle"} for k, lbl in DEFAULT_DIAG_STAGES]
    parts = ['<svg viewBox="0 0 680 96" class="mcp-diag" role="img" aria-label="连接诊断" '
             'xmlns="http://www.w3.org/2000/svg">']
    parts.append('<style>'
                 '.mcp-seg-done{stroke:var(--diag-seg-done,#1c9e66);}'
                 '.mcp-seg-idle{stroke:var(--diag-seg-idle,#dde3ee);}'
                 '.mcp-ring{fill:var(--bg-raised,#fff);stroke-width:2;}'
                 '.n-done{stroke:var(--diag-done,#1c9e66);}'
                 '.n-fail{stroke:var(--diag-fail,#e0484f);}'
                 '.n-warn{stroke:var(--diag-warn,#c8811c);}'
                 '.n-active{stroke:var(--diag-active,#2f6fed);}'
                 '.n-idle{stroke:var(--diag-idle,#98a3b4);}'
                 '.mcp-lbl{font:510 12px var(--font-ui,system-ui);fill:var(--text,#222b38);}'
                 '</style>')
    n = len(stages)
    step = 600 / max(n, 1)
    prev_x = 40
    for i, st in enumerate(stages):
        x = 40 + i * step
        state = st.get("state", "idle")
        if failed_at and st.get("key") == failed_at:
            state = "fail" if state in ("fail", "idle") else state
        cls = {"done": "n-done", "fail": "n-fail", "warn": "n-warn",
               "active": "n-active", "idle": "n-idle"}.get(state, "n-idle")
        if i > 0:
            seg = "mcp-seg-done" if state in ("done", "fail", "warn", "active") else "mcp-seg-idle"
            parts.append(f'<line class="{seg}" x1="{prev_x}" y1="40" x2="{x}" y2="40"/>')
        parts.append(f'<g class="mcp-node" data-state="{state}" data-step="{i+1}" '
                     f'transform="translate({x},40)">')
        parts.append(f'<circle class="mcp-ring {cls}" r="16"/>')
        parts.append(f'<text class="mcp-lbl" y="36" text-anchor="middle">{st.get("label","")}</text>')
        parts.append('</g>')
        prev_x = x
    parts.append('</svg>')
    return "".join(parts)


# ---------------------------------------------------------------- 编排（不执行、不写盘；仅 search/fetch/解析/校验）

def build_candidate(parsed: list[str], *, provenance: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """把 parse_install_command 的 argv 经 resolve_command（mcp_client.py:72）解析为绝对路径
    写入 cand['command']；解析失败置 cand['command_unresolved']=True。组装候选。
    """
    launcher = parsed[0]
    resolved = mcp_client.resolve_command(launcher)
    abs_ok = (resolved != launcher) and os.path.isabs(resolved)
    return {
        "transport": "stdio",
        "command": resolved if abs_ok else launcher,
        "args": list(parsed[1:]),
        "url": "",
        "env": {},
        "headers": {},
        "provenance": dict(provenance or {}),
        "command_unresolved": not abs_ok,
    }


def _candidate_view(cand: dict[str, Any], trust: str, errors: list[str]) -> dict[str, Any]:
    return {"config": cand, "trust": trust, "validation_errors": errors}


def _dedupe(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for c in cands:
        cfg = c["config"]
        key = (cfg.get("transport"), cfg.get("command") or cfg.get("url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _github_readme_markdown(url: str) -> str:
    """把 github.com 仓库/子路径链接改写为 api.github.com readme/contents，解码 base64 → markdown。

    live 路径兜底：官方文档多是 JS 渲染 HTML，urllib 剥离 script 后无可读文本；改用 GitHub
    REST API 取 README 原文（已验证 api.github.com 对无鉴权 GET 返回 200 + base64 markdown）。
    失败（非 github 链接 / 网络异常 / 解码失败）返回 ''，由调用方继续走原 fetch 或放弃。
    """
    m = re.match(r"https?://github\.com/([^/?#]+)/([^/?#]+)(/?.*)?", url or "")
    if not m:
        return ""
    owner, repo = m.group(1), m.group(2)
    sub = (m.group(3) or "").strip("/")
    branch = None
    if sub:
        parts = sub.split("/")
        if parts and parts[0] in ("main", "master", "dev"):
            branch = parts[0]
            sub = "/".join(parts[1:])
    try:
        if sub:
            api = f"https://api.github.com/repos/{owner}/{repo}/contents/{sub}"
            if branch:
                api += f"?ref={branch}"
        else:
            api = f"https://api.github.com/repos/{owner}/{repo}/readme"
            if branch:
                api += f"?ref={branch}"
        req = urllib.request.Request(
            api, headers={"User-Agent": _BROWSER_UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        # contents 目录返回列表：挑 README.* 再取一次
        if isinstance(data, list):
            readme = next((d for d in data if d.get("name", "").lower().startswith("readme")), None)
            if not readme:
                return ""
            req2 = urllib.request.Request(readme["url"], headers={"User-Agent": _BROWSER_UA})
            with urllib.request.urlopen(req2, timeout=12) as r2:
                data = json.loads(r2.read().decode("utf-8", "replace"))
        content = data.get("content")
        if not content:
            return ""
        return base64.b64decode(content).decode("utf-8", "replace")
    except Exception:
        return ""


def auto_connect_pipeline(root: str, query: str, *,
                          search_fn: Optional[Callable[[str], str]] = None,
                          fetch_fn: Optional[Callable[[str], str]] = None,
                          llm_fn: Optional[Callable[[str, str], str]] = None) -> dict[str, Any]:
    """搜索 → fetch 官方正文 → 抽取 → 校验 → 返回候选。内部吞 search_fn/fetch_fn 异常。

    命中顺序（互补、互不阻塞）：
      1) 离线精选索引（瞬时、可信、无需联网，对「未联网」路径也生效）；
      2) 联网搜索 → fetch 官方正文（github 链接兜底走 API readme，绕过 JS 渲染壳）；
      3) 仍无正文 → 退化 LLM 直给命令（若提供 llm_fn）。

    返回 {'ok': bool, 'candidates': list[dict], 'search_error': str}。
    抛 AutoConnectError 仅用于致命配置错误（如 root 无效）。
    """
    if not root or not os.path.isdir(root):
        raise AutoConnectError("无效的项目根目录")
    candidates: list[dict[str, Any]] = []
    search_error = ""
    sources: list[str] = []

    # 1) 离线精选索引优先：已知 server 直接出候选，省去联网抓取与 JS 渲染失败。
    curated = match_curated_server(query)
    for entry in curated:
        cfg = curated_entry_to_config(entry)
        ok, errs = validate_extracted_config(cfg)
        if ok:
            domain = cfg["provenance"].get("domain", "")
            trust = "trusted" if is_trusted_domain(domain) else "source_untrusted"
            candidates.append(_candidate_view(cfg, trust, errs))
    if candidates:
        return {"ok": True, "candidates": _dedupe(candidates), "search_error": ""}

    try:
        if search_fn:
            raw = search_fn(f"{query} MCP server official GitHub installation connection usage")
            sources = list(dict.fromkeys(re.findall(r"https?://[^\s)]+", raw or "")))[:8]
    except Exception as exc:
        search_error = str(exc)[:300]

    docs: list[tuple[str, str]] = []
    for url in sources:
        body = None
        try:
            if fetch_fn:
                body = fetch_fn(url)
        except Exception:
            body = None
        # github 链接兜底：JS 渲染 HTML 抓不到正文，改写走 API readme 取 markdown
        if not body or "网页读取失败" in body[:20] or "读取失败" in body[:20]:
            gh = _github_readme_markdown(url)
            if gh:
                body = gh
        if body and "网页读取失败" not in body[:20] and "读取失败" not in body[:20]:
            docs.append((url, body))

    # 无来源则无法安全抽取（保留 :117 安全语义：正文才成候选，不凭摘要捏命令）
    if not docs and llm_fn:
        try:
            hint = llm_fn(query, "请只给出官方安装命令，如 uvx <pkg> 或 npx -y <pkg>")
            if hint:
                docs.append(("", hint))
        except Exception:
            pass

    for url, body in docs:
        domain = _domain_of(url) if url else ""
        trusted = is_trusted_domain(domain) if domain else False
        # http 端点
        m = HTTP_ENDPOINT_RE.search(body or "")
        if m:
            url_cand = m.group(0)
            cand = {
                "transport": "http", "command": "", "args": [], "url": url_cand,
                "env": {}, "headers": {}, "provenance": {"url": url, "domain": domain},
                "command_unresolved": False,
            }
            ok, errs = validate_extracted_config(cand)
            if ok:
                candidates.append(_candidate_view(cand, "trusted" if trusted else "source_untrusted", errs))
        # stdio 命令
        parsed = parse_install_command(body or "")
        if parsed:
            cand = build_candidate(parsed, provenance={"url": url, "domain": domain})
            ok, errs = validate_extracted_config(cand)
            if ok:
                # R8：不可信来源不自动填参，仅标记 source_untrusted 供前端走手动确认
                trust = "trusted" if trusted else "source_untrusted"
                candidates.append(_candidate_view(cand, trust, errs))

    # 诊断：空候选时给出可展示原因，避免前端只有空态无解释。
    if not candidates and not search_error:
        if not search_fn:
            search_error = "未启用联网搜索，无法从官方文档提取连接命令（可勾选“允许联网补充来源”后重试）"
        elif not sources:
            search_error = "联网搜索未返回可用的官方文档链接"
        elif not docs:
            search_error = "官方文档链接均未能读取正文（可能被网络/反爬拦截）"
        else:
            search_error = "已读取官方文档，但未从中解析出可信的安装命令（请手动填写）"

    return {"ok": bool(candidates), "candidates": _dedupe(candidates), "search_error": search_error}


def discover_from_need(root: str, need: str, *, web_enabled: bool = False,
                       github_search_fn: Optional[Callable[[str], str]] = None) -> dict[str, Any]:
    """从自然语言需求发现可装配的 MCP 连接器候选（不写盘、不自动启用路由）。

    命中顺序（离线优先；联网仅作长尾兜底，且严格走 GitHub 域 —— 规避弱泛搜索）：
      1) 离线精选索引 match_curated_server(need)：已知热门 server 秒级、可信；
      2) web_enabled 且提供 github_search_fn 时：GitHub 域限定搜索（platform:github）→
         取仓库 URL → _github_readme_markdown 取 README 原文（绕过 JS 渲染壳）→
         parse_install_command 抽取命令 → R1-R9 信任闸门校验。

    每个候选带 provenance（来源域 / 链接）与 trust 标记；调用方须向用户展示候选，
    逐条经 dev_mcp_add（审批闸门）落盘，绝不可静默写入。
    返回 {'ok': bool, 'candidates': list[dict], 'search_error': str, 'source': str}。
    """
    if not root or not os.path.isdir(root):
        raise AutoConnectError("无效的项目根目录")
    need = (need or "").strip()
    if not need:
        return {"ok": False, "candidates": [], "search_error": "需求描述为空", "source": "none"}

    candidates: list[dict[str, Any]] = []

    # 1) 离线精选索引优先：已知 server 直接出候选，省去联网与 JS 渲染失败。
    for entry in match_curated_server(need):
        cfg = curated_entry_to_config(entry)
        ok, errs = validate_extracted_config(cfg)
        if ok:
            domain = cfg.get("provenance", {}).get("domain", "")
            trust = "trusted" if is_trusted_domain(domain) else "source_untrusted"
            candidates.append(_candidate_view(cfg, trust, errs))

    # 2) 联网：仅 GitHub 域（site 限定 + GitHub API readme 兜底），绝不走泛搜索。
    search_error = ""
    if not candidates and web_enabled and github_search_fn:
        try:
            raw = github_search_fn(need) or ""
        except Exception as exc:
            raw = ""
            search_error = f"GitHub 搜索失败：{type(exc).__name__}: {exc}"[:300]
        urls = list(dict.fromkeys(re.findall(r"https?://github\.com/[^\s)'\"<>]+", raw)))[:5]
        for url in urls:
            md = _github_readme_markdown(url)
            if not md:
                continue
            parsed = parse_install_command(md)
            if not parsed:
                continue
            cand = build_candidate(parsed, provenance={
                "url": url, "domain": "github.com", "github_repo": url, "source": "github_search"})
            ok, errs = validate_extracted_config(cand)
            if ok:
                candidates.append(_candidate_view(cand, "trusted", errs))
        if not candidates and not search_error:
            search_error = ("GitHub 搜索未返回可用仓库链接" if not urls
                            else "已读取 GitHub README，但未解析出可信安装命令（请手动确认）")

    source = ("offline" if (candidates and not web_enabled)
              else "github" if candidates else "none")
    return {"ok": bool(candidates), "candidates": _dedupe(candidates),
            "search_error": search_error, "source": source}


def resolve_secret_refs(cfg: dict[str, Any], root: str) -> dict[str, Any]:
    """@secret:<provider> → secrets_store.load(root, provider) 明文。

    仅返回新 dict，不改原 cfg。引用了未存入的 provider 时抛 AutoConnectError。
    """
    out = dict(cfg)
    for sec in ("env", "headers"):
        src = cfg.get(sec) or {}
        if not src:
            continue
        new_sec: dict[str, str] = {}
        for k, v in src.items():
            sm = SECRET_RE.match(str(v))
            if sm:
                provider = sm.group(1)
                plain = secrets_store.load(root, provider)
                if not plain:
                    raise AutoConnectError(f"凭证提供方 {provider} 未存入 secrets_store，无法解析 @secret 引用")
                new_sec[k] = plain
            else:
                new_sec[k] = str(v)
        out[sec] = new_sec
    return out


def probe_candidate(root: str, cand: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    """临时进程 initialize + tools/list，读后 close()（不持久化、不路由）。

    复用 mcp_client._StdioSession（stdio）/ _http_initialize + _http_post（http）。
    失败抛 mcp_client.MCPError。
    """
    cfg = dict(cand)
    cfg["command"] = mcp_client.resolve_command(cfg.get("command") or "")
    if cfg.get("transport") == "http":
        from mcp_client import _http_initialize, _http_post  # 惰性，避免顶层依赖私有符号
        mcp_client._http_initialize(cfg)
        result = _http_post(cfg["url"], {"jsonrpc": "2.0", "id": 2,
                                         "method": "tools/list", "params": {}}) or {}
        names = [t.get("name") for t in result.get("tools", [])]
        return {"ok": True, "probe_ok": True, "tools": names, "error": ""}
    sess = mcp_client._StdioSession(cfg, cwd=os.path.abspath(root))
    try:
        sess.initialize()
        tools = sess.request("tools/list", {}, timeout=min(timeout, 30)) or {}
        names = [t.get("name") for t in tools.get("tools", [])]
        return {"ok": True, "probe_ok": True, "tools": names, "error": ""}
    finally:
        sess.close()


def vision_extract_params(image_b64: str, root: str) -> dict[str, Any]:
    """图 → agent_runtime.vision.analyze_images → 观察文本 → build_candidate → validate（同闸门）。

    返回 auto_connect_pipeline 同形结构。模型读出命令也过 R1-R9，绝不因来自图片就放行。
    """
    from agent_runtime import vision
    if not image_b64:
        return {"ok": False, "candidates": [], "search_error": "未提供图片"}
    _, context, audit = vision.analyze_images([image_b64])
    text = context if isinstance(context, str) else ""
    if not text or audit.get("mode") in ("none", "unavailable"):
        return {"ok": False, "candidates": [], "search_error": "视觉模型未返回可观察文本"}
    # 观察文本当作不可信正文，走同一抽取+闸门
    parsed = parse_install_command(text)
    if not parsed:
        return {"ok": False, "candidates": [], "search_error": "未能从图片观察中安全提取安装命令"}
    cand = build_candidate(parsed, provenance={"url": "", "domain": ""})
    ok, errs = validate_extracted_config(cand)
    if not ok:
        return {"ok": False, "candidates": [], "search_error": "；".join(errs)}
    trust = "source_untrusted"  # 图片来源不可核，保守标记
    return {"ok": True, "candidates": [_candidate_view(cand, trust, errs)], "search_error": ""}


# ---------------------------------------------------------------- 注册代管（L0/L1/L2 降级链）

def browser_register(root: str, key: str, cand: dict[str, Any], provider: str) -> dict[str, Any]:
    """L0/L1/L2 降级链入口。

    v1 状态：系统 Edge + playwright 能力探测已实现；但 L0 全自动填表与 L1 点验证码续跑
    为 TODO 桩（IMPL-PLAN §3.3），尚未实现。故当前**诚实降级为 L2 手动回填**——不自动
    拉起浏览器，仅登记会话并返回官方 URL，由前端弹 L2 弹窗引导用户创建凭证。
    若未来启用 L0/L1，此处再按能力探测走 `playwright channel='msedge'` 长驻会话。
    """
    import shutil
    import tempfile

    edge_ok = bool(shutil.which("msedge")) or _edge_in_programfiles()
    playwright_ok = True
    try:
        import playwright  # 仅探测可用性，不在 v1 拉起浏览器
    except Exception:
        playwright_ok = False

    url = (cand.get("provenance") or {}).get("url") or ""
    # L0/L1 自动填表 TODO：能力不足或功能未实现都走 L2，调用方据此弹 L2 弹窗
    task_id = _new_task_id()
    context_dir = tempfile.mkdtemp(prefix="docmind_ac_")
    _persist_session(task_id, "L2", url, context_dir, status="waiting_user")
    note = "注册自动填充尚未启用（L0/L1 TODO），已降级 L2 手动回填"
    if not (edge_ok and playwright_ok):
        note += "（本机未检测到 Microsoft Edge 或 playwright，无法做 L0/L1）"
    return {"ok": True, "task_id": task_id, "tier": "L2", "url": url, "note": note}


def _edge_in_programfiles() -> bool:
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = os.environ.get(env)
        if pf and os.path.isfile(os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe")):
            return True
    return False


def _new_task_id() -> str:
    import uuid
    return f"ac_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"


def _persist_session(task_id: str, tier: str, url: str, context_dir: str, status: str = "running") -> None:
    """会话状态落 project_state（.docmind_autoconnect_sessions.json）。"""
    path = project_state.path(os.getcwd(), "autoconnect_sessions.json", legacy=".docmind_autoconnect_sessions.json")
    # browser_register 传入的 root 未必等于 cwd；会话文件落在 cwd 即可（运行时单用户桌面）
    data: dict[str, Any] = {}
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            data = {}
    data[task_id] = {
        "tier": tier, "url": url, "context_dir": context_dir,
        "resume_token": task_id, "created_at": time.time(), "status": status,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
