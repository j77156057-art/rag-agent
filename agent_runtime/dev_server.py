"""本地开发服务生命周期：起一个 dev server、确认它真的能应答，再把 URL 交回给 Agent。

vibecoding 的主循环是「改代码 → 跑起来 → 看真实页面」。harness 这边的
`preview_project` 只会把项目目录当静态文件临时服务一下（ThreadingHTTPServer），
所以 Vite/Vue 这类【必须先跑 dev server】的前端，不 build 就预览不到。本模块补那一段：
用已有的后台任务设施启动进程、探测就绪、把 URL 交给 `preview_project(url=...)` 去截
真实画面并读 console/失败请求。

安全边界：只允许【回环地址】。这不是网页浏览器，不能被拿去访问内网或外网机器；
命令本身仍要先过 run_command 的黑名单，进程仍在 code_root（或其子目录）内启动。
"""
from __future__ import annotations

import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import process_runner

DEFAULT_WAIT = 20.0
MAX_WAIT = 120.0
PROBE_INTERVAL = 0.25
PROBE_TIMEOUT = 1.5
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_PORT_LINE = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\]):(\d{2,5})")


def free_port(host: str = "127.0.0.1") -> int:
    """要一个当下空闲的端口，交给调用方的命令去 bind。

    这只是【建议】：从返回到子进程真正 bind 之间仍可能被抢，所以下面的就绪判定
    一律以实际连通为准，绝不把 free_port 成功当成服务已起来的证据。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def loopback_url(raw: str, *, default_port: int = 0) -> tuple[str, str]:
    """校验并归一化成本机可预览 URL，返回 (url, 错误信息)。"""
    text = str(raw or "").strip().strip("'\"")
    if not text:
        return "", "缺少 url。"
    if "://" not in text:
        text = "http://" + text
    try:
        parsed = urllib.parse.urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        return "", f"url 解析失败：{exc}"
    if parsed.scheme not in ("http", "https"):
        return "", f"只支持 http/https，收到 {parsed.scheme!r}。"
    host = (parsed.hostname or "").strip("[]")
    if host not in LOOPBACK and not host.startswith("127."):
        return "", ("只允许本机回环地址（127.0.0.1 / localhost / ::1），收到 %r。"
                    "本工具不是网页浏览器，不能用来访问其他机器。" % host)
    if port is None:
        if not default_port:
            return "", "url 缺少端口，例如 http://127.0.0.1:5173/"
        port = default_port
    url = "%s://%s:%d%s" % (parsed.scheme, host, port, parsed.path or "/")
    if parsed.query:
        url += "?" + parsed.query
    return url, ""


def parse_port_hint(text: str) -> int:
    """从服务自己的日志里认端口（vite/next/webpack-dev-server 都会打印自己的地址）。"""
    match = _PORT_LINE.search(str(text or ""))
    return int(match.group(1)) if match else 0


def probe(url: str, *, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """先看端口通不通，再发一次 GET。返回 (是否就绪, 说明)。"""
    target, err = loopback_url(url)
    if err:
        return False, err
    parsed = urllib.parse.urlsplit(target)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        return False, "端口 %d 还没有在听：%s" % (port, exc)
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            return True, "HTTP %s" % (getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 也说明服务已经起来并且能应答——这同样算就绪
        return True, "HTTP %s（非 2xx，但服务已在响应）" % exc.code
    except (OSError, ValueError) as exc:
        return False, "端口通但请求失败：%s" % exc


def _view(job_id: str, offset: int = 0) -> dict[str, Any]:
    rep = process_runner.job_logs(job_id, offset=offset)
    return rep if isinstance(rep, dict) else {}


def start(root: str, command: str, *, argv: list[str], cwd: str = "", port: int = 0,
          wait: float = DEFAULT_WAIT, env: dict[str, str] | None = None,
          job_timeout: int = 900) -> dict[str, Any]:
    """后台启动一个本地服务，并等到它真的能应答（或超时）。"""
    out: dict[str, Any] = {"ok": False, "ready": False, "job_id": "", "url": "",
                           "port": 0, "error": "", "notes": [], "log_tail": ""}
    if not root or not os.path.isdir(root):
        out["error"] = "代码根目录不存在或未配置。"
        return out
    if not argv:
        out["error"] = "缺少要执行的命令。"
        return out
    workdir = os.path.normpath(os.path.join(root, cwd)) if cwd else os.path.normpath(root)
    root_abs = os.path.normpath(root)
    if not (workdir == root_abs or workdir.startswith(root_abs + os.sep)):
        out["error"] = f"拒绝在代码根目录外启动服务：{cwd}"
        return out
    if not os.path.isdir(workdir):
        out["error"] = f"工作目录不存在：{cwd or '.'}"
        return out
    rep = process_runner.start_job(list(argv), cwd=workdir, timeout=job_timeout, env=env,
                                   command_text=command or " ".join(argv))
    if not rep.get("ok"):
        out["error"] = str(rep.get("error") or "后台任务启动失败")
        return out
    job_id = str(rep.get("job_id") or "")
    out["job_id"] = job_id
    try:
        seconds = max(0.0, min(float(wait), MAX_WAIT))
    except (TypeError, ValueError):
        seconds = DEFAULT_WAIT
    deadline = time.monotonic() + seconds

    seen = 0
    offset = 0
    log = ""
    want_port = int(port or 0)
    reason = ""
    while time.monotonic() < deadline:
        view = _view(job_id, offset)
        chunk = str(view.get("logs") or "")
        if chunk:
            log += chunk
            offset = int(view.get("next_offset") or offset + len(chunk))
        detected = want_port or parse_port_hint(log)
        if detected:
            ready, reason = probe("http://127.0.0.1:%d/" % detected)
            if ready:
                out.update({"ok": True, "ready": True, "port": detected,
                            "url": "http://127.0.0.1:%d/" % detected})
                out["notes"].append("就绪探测：%s" % reason)
                break
        state = str(view.get("state") or "")
        if state in ("done", "failed", "timeout", "cancelled"):
            out["error"] = "服务进程已经退出（state=%s），看下面的输出尾部。" % state
            out["log_tail"] = log[-1200:]
            return out
        time.sleep(PROBE_INTERVAL)

    if not out["ready"]:
        view = _view(job_id, offset)
        log += str(view.get("logs") or "")
        out["ok"] = True          # 进程确实起来了，只是没探测到就绪——两件事分开报
        out["error"] = ("等了约 %.0fs 没探测到就绪（%s）。进程仍在运行，"
                        "可以用 dev_job_logs 继续观察，或确认命令与端口。"
                        % (seconds, reason or "日志里还没认到端口"))
        out["log_tail"] = log[-1200:]
    else:
        out["log_tail"] = log[-400:]
    return out


def stop(job_id: str) -> dict[str, Any]:
    ident = str(job_id or "").strip()
    if not ident:
        return {"ok": False, "error": "缺少 job_id。"}
    rep = process_runner.job_cancel(ident)
    if not rep.get("ok"):
        return {"ok": False, "error": str(rep.get("error") or "回收失败")}
    return {"ok": True, "job_id": ident, "state": rep.get("state"),
            "exit_code": rep.get("exit_code")}


def render(result: dict[str, Any]) -> str:
    if not result.get("job_id"):
        return "dev_serve 未完成：" + (result.get("error") or "未知原因")
    if result.get("ready"):
        lines = ["服务已就绪：%s（job_id=%s，端口 %s）"
                 % (result.get("url"), result.get("job_id"), result.get("port"))]
        lines.append("接着用 preview_project 的 `url: %s` 打这个地址，拿真实截图与 console/失败请求。"
                     % result.get("url"))
    else:
        lines = ["进程已启动但没探测到就绪（job_id=%s）：%s"
                 % (result.get("job_id"), result.get("error"))]
    lines += ["提示: " + note for note in result.get("notes") or []]
    tail = str(result.get("log_tail") or "").strip()
    if tail:
        lines.append("输出尾部：\n" + tail)
    if result.get("ready") and not result.get("stopped"):
        lines.append("用完记得回收：dev_serve action: stop, job_id: %s" % result.get("job_id"))
    return "\n".join(lines)
