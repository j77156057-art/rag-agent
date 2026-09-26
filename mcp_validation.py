"""MCP 候选命令解析与 R1-R9 信任校验。"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Callable, Optional


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

# 值内嵌 @secret:<provider>（含整值形态，向后兼容）：provider 名为 token 字符集。
SECRET_INLINE_RE = re.compile(r"@secret:([A-Za-z0-9_.-]+)")
SHELL_META_RE = re.compile(r"[;|&$><\n\r(){}\[\]*?~`#!]")
ALLOWED_URL_RE = re.compile(r"^https://[a-z0-9.-]+(?::\d{1,5})?(/[^\s]*)?$", re.I)
HTTP_ENDPOINT_RE = re.compile(r"https://[^\s)'\"`]+/mcp[^\s)'\"`]*", re.I)

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


def validate_extracted_config(cand: dict[str, Any], *,
                              safe_dir: Optional[Callable[[str], bool]] = None) -> tuple[bool, list[str]]:
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
                if not (safe_dir or _safe_dir)(cmd) or base_name not in ALLOWED_LAUNCHERS:
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
        else:
            # 只拒绝 authority 段的 userinfo（http://u:p@host）；path 里的 "@"（如
            # smithery 规范形态 /@scope/pkg/mcp）是合法的，不得误杀。
            parts = urllib.parse.urlsplit(url)
            if parts.username or parts.password:
                errors.append("url 拒绝用户信息（http://u:p@host）")
            elif (parts.scheme or "").lower() != "https" or not ALLOWED_URL_RE.match(url):
                errors.append("url 必须是 https")
            else:
                host = parts.hostname or _domain_of(url)
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


