"""Browser backed visual acceptance for the autonomous development cockpit.

The production workflow uses this small adapter for ordinary web previews.  It
serves only the current project directory, captures a real browser frame, and
returns the image through the normal tool vision channel.  Domain specific
engines can register richer adapters later; a missing browser or unsupported
entry point is reported as an explicit verification failure.
"""
from __future__ import annotations

import base64
import contextlib
import ctypes
import functools
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .dev_server import loopback_url
import urllib.request


class VisualAcceptanceError(RuntimeError):
    """A visual capture could not produce trustworthy evidence."""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        # The browser always probes /favicon.ico; a 404 there would be reported
        # as a real page failure, so answer it with an empty success instead.
        if self.path.split("?")[0].rstrip("/").lower().endswith("/favicon.ico"):
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass


def _edge_binary() -> str:
    configured = str(os.getenv("DOCMIND_EDGE_BIN") or "").strip()
    candidates = [configured] if configured else []
    candidates.extend([
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("msedge") or "",
        shutil.which("microsoft-edge") or "",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    raise VisualAcceptanceError(
        "未找到 Edge/Chromium 浏览器，无法进行真实画面验收；可配置 DOCMIND_EDGE_BIN。"
    )


_BASE_FLAGS = ("--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--remote-debugging-port=0",
               "--remote-allow-origins=*")
_MAX_EXTRA_FLAGS = 32
_PROFILE_PREFIX = "docmind-visual-preview-"
# 单次回环最长几十秒，但同事的会话是并发的，阈值必须远大于一次运行才不至于删到活的。
_PROFILE_MAX_AGE_SECONDS = 6 * 3600


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


def _kernel32():
    """显式声明每个入口的 restype/argtypes。

    不写的话 ctypes 默认按 C int 传参，x64 上句柄会被截成 32 位——表现为赋值偶尔失败，
    而失败被下面那个 best-effort 分支吃掉，比直接崩更难查。
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                                 ctypes.c_void_p, ctypes.c_ulong)
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _reap_with_parent(process) -> Any:
    """把浏览器绑进 kill-on-close 的作业对象，交回作业句柄；不适用时回 None。

    finally 里的 taskkill 只有父进程【正常退出】才跑得到。实测 44 套孤儿全都在
    `parent=GONE` 状态——Agent 会话被硬杀或重启时 finally 根本不执行，headless Edge 却
    一直活着，462 个进程把 CPU 顶到 99°C。作业对象把这层保证交给操作系统：句柄随进程
    关闭，整棵树跟着没。
    """
    if os.name != "nt":
        return None
    handle = None
    try:
        kernel32 = _kernel32()
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        limits = _JobExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError("SetInformationJobObject rejected")
        # 必须在子进程已经创建、但还没派生渲染进程之前绑上；靠 Chromium 自己在浏览器
        # 进程消失时终止其余进程来覆盖绑定时机之外的那几个。
        if not kernel32.AssignProcessToJobObject(handle, process._handle):
            raise OSError("AssignProcessToJobObject rejected")
        return handle
    except OSError:
        if handle:
            try:
                _kernel32().CloseHandle(handle)
            except OSError:
                pass
        return None


def _close_reaper(job: Any) -> None:
    if job is None:
        return
    try:
        _kernel32().CloseHandle(job)
    except OSError:
        pass


def _release_profile(profile) -> bool:
    """删掉 profile，扛住 Edge 退出前那段文件锁；返回是否真的删干净了。

    正常退出路径实测 10/10 次都留下目录：`process.wait(3)` 一超时我们就开始删，而 Edge
    还锁着自己的文件。`ignore_cleanup_errors=True` 只是不抛异常，不等于删掉了，且
    `TemporaryDirectory.cleanup()` 只执行一次，重试只能自己做。删不净的由
    `sweep_stale_profiles` 兜底。
    """
    path = profile.name
    for _ in range(20):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.isdir(path):
            return True
        time.sleep(0.15)
    profile.cleanup()
    return not os.path.isdir(path)


def sweep_stale_profiles(directory: str | Path) -> list[str]:
    """回收硬杀父进程留下的旧 profile 目录，返回被删掉的路径。

    与 `_reap_with_parent` 同一个成因：`finally` 里的 `profile.cleanup()` 也只跑得到正常
    退出，实测一夜留下 41 个目录、8.3 GB。只认我们自己的前缀、且必须老到远超一次运行，
    并发同事正在用的目录不会被碰。
    """
    parent = Path(str(directory or ""))
    removed: list[str] = []
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return removed
    deadline = time.time() - _PROFILE_MAX_AGE_SECONDS
    for entry in entries:
        if not entry.name.startswith(_PROFILE_PREFIX):
            continue
        try:
            if not entry.is_dir() or entry.stat().st_mtime >= deadline:
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
            # 用 path 而不是 entry 复查：DirEntry 会缓存 stat，删完再问它仍然说「在」。
            if not os.path.isdir(entry.path):
                removed.append(entry.path)
        except OSError:
            continue
    return removed



def sanitize_flags(flags: Any) -> list[str]:
    """浏览器启动参数只接受 `--开关` / `--开关=值` 形态。

    参数会以 argv 直接交给浏览器（不过 shell），但一个能自由写 argv 的入口仍然是
    注入面：`--user-data-dir`、`--load-extension` 这类能改变浏览器行为与落盘位置的
    开关一律拒绝，长度与非空白字符也限死。
    """
    denied = ("--user-data-dir", "--load-extension", "--disable-extensions-except",
              "--extension-process", "--profiler-output", "--inspect", "--remote-debugging-pipe")
    accepted: list[str] = []
    for item in flags or ():
        text = str(item).strip()
        if not text:
            continue
        if not text.startswith("--") or len(text) > 1_000:
            raise VisualAcceptanceError("浏览器启动参数必须是 --开关 形态且不能过长：%r" % text[:60])
        if any(ch in text for ch in ("\n", "\r", "\x00")):
            raise VisualAcceptanceError("浏览器启动参数含有非法字符：%r" % text[:60])
        name = text.partition("=")[0]
        if any(name.startswith(blocked) for blocked in denied):
            raise VisualAcceptanceError("浏览器启动参数不允许：%s" % name)
        accepted.append(text)
    if len(accepted) > _MAX_EXTRA_FLAGS:
        raise VisualAcceptanceError("浏览器启动参数最多 %s 条。" % _MAX_EXTRA_FLAGS)
    return accepted


def serve_static(root: str | Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """把一个目录临时当静态站服务（只绑回环、随机端口）；调用方负责 `stop_static`。"""
    path = Path(root).resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 functools.partial(_QuietHandler, directory=str(path)))
    thread = threading.Thread(target=server.serve_forever, name="visual-preview-http",
                              daemon=True)
    thread.start()
    return server, thread


def stop_static(server: Any, thread: Any) -> None:
    if server is None:
        return
    server.shutdown()
    server.server_close()
    if thread is not None:
        thread.join(timeout=2)


@contextlib.contextmanager
def browser_session(timeout: float = 25.0, extra_flags: Any = ()):
    """起一个带 CDP 的真实浏览器，交回 `_DevTools`；退出时进程与 profile 一定收干净。

    拆出来是因为媒体夹具需要在同一批启动参数上追加假设备开关——两条启动路径各自演化
    迟早会出现「预览能过、媒体过不了」这种无法解释的差集。

    **新建任何"要看一眼界面"的脚本都必须走这里，不要引 playwright。** playwright 启动的
    浏览器拿不到进程句柄，绑不了 kill-on-close 的作业对象（`_reap_with_parent`），父进程被
    硬杀（会话重启、工具超时）时必然留下孤儿 headless 进程与 `playwright_chromiumdev_profile-*`
    目录——实测几百个 msedge 把 CPU 顶到 99°C。这条路径由作业对象 + 显式 taskkill /T /F +
    profile 重试回收 + 启动前 sweep 四道兜住。
    """
    edge = _edge_binary()
    try:
        import websocket
    except Exception as exc:  # pragma: no cover - depends on desktop bundle
        raise VisualAcceptanceError("浏览器控制依赖 websocket-client 未安装。") from exc
    flags = sanitize_flags(extra_flags)
    sweep_stale_profiles(tempfile.gettempdir())
    profile = tempfile.TemporaryDirectory(prefix=_PROFILE_PREFIX,
                                          ignore_cleanup_errors=True)
    process = None
    connection = None
    job = None
    try:
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen([edge, *_BASE_FLAGS, *flags,
                                    "--user-data-dir=%s" % profile.name, "about:blank"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=creation)
        job = _reap_with_parent(process)
        active = Path(profile.name) / "DevToolsActivePort"
        deadline = time.monotonic() + min(15.0, max(3.0, float(timeout) / 2))
        while not active.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not active.exists():
            raise VisualAcceptanceError("浏览器调试端口未就绪。")
        port = active.read_text(encoding="utf-8").splitlines()[0].strip()
        with urllib.request.urlopen("http://127.0.0.1:%s/json" % port, timeout=5) as response:
            tabs = json.load(response)
        page = next((row for row in tabs if row.get("type") == "page"), None)
        if not page or not page.get("webSocketDebuggerUrl"):
            raise VisualAcceptanceError("浏览器没有可用页面标签。")
        connection = websocket.create_connection(page["webSocketDebuggerUrl"],
                                                 timeout=max(5.0, float(timeout)),
                                                 suppress_origin=True)
        devtools = _DevTools(connection, recv_timeout=max(5.0, float(timeout)))
        devtools.call("Page.enable")
        devtools.call("Runtime.enable")
        devtools.call("Log.enable")
        devtools.call("Network.enable")
        yield devtools
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if process is not None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=5)
                else:
                    process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        # 顺序要紧：先让上面那条显式关闭跑完，再解绑作业。提前关句柄会当场杀掉浏览器，
        # 把「谁关的、为什么关」这条排查线索抹掉。
        _close_reaper(job)
        job = None
        _release_profile(profile)


_MAX_CONSOLE = 30
_MAX_REQUESTS = 30
_IGNORED_REQUEST_SUFFIX = ("favicon.ico",)


class _DevTools:
    """Minimal CDP client that also records the signals a screenshot cannot show."""

    def __init__(self, connection, runtime_errors: list[str] | None = None,
                 recv_timeout: float = 5.0):
        self.connection = connection
        self.sequence = 0
        self.recv_timeout = max(1.0, float(recv_timeout))
        self.runtime_errors = runtime_errors if runtime_errors is not None else []
        self.console: list[dict[str, Any]] = []
        self.failed_requests: list[dict[str, Any]] = []
        self._requests: dict[str, str] = {}

    def _record_console(self, level: str, text: str) -> None:
        if "favicon.ico" in str(text).lower():
            return
        entry = {"level": level, "text": str(text)[:300]}
        if entry not in self.console and len(self.console) < _MAX_CONSOLE:
            self.console.append(entry)

    def _record_failure(self, kind: str, url: str, detail: str) -> None:
        if str(url).split("?")[0].rstrip("/").lower().endswith(_IGNORED_REQUEST_SUFFIX):
            return
        # Chrome reports an HTTP failure twice: once as the status and once as an
        # aborted load. Keep the status, drop the redundant abort noise.
        if kind == "network" and "ERR_ABORTED" in str(detail):
            base = str(url).split("?")[0]
            if any(item["url"].split("?")[0] == base and item["kind"].startswith("http_")
                   for item in self.failed_requests):
                return
        entry = {"kind": kind, "url": str(url)[:300], "detail": str(detail)[:200]}
        if entry not in self.failed_requests and len(self.failed_requests) < _MAX_REQUESTS:
            self.failed_requests.append(entry)

    def _handle_event(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = str(details.get("text") or details.get("exception", {}).get("description") or "runtime error")
            if text not in self.runtime_errors:
                self.runtime_errors.append(text[:500])
        elif method == "Runtime.consoleAPICalled":
            level = str(params.get("type") or "").lower()
            if level in {"error", "warning"}:
                parts = []
                for argument in params.get("args") or []:
                    value = argument.get("value")
                    parts.append(str(value if value is not None
                                     else (argument.get("description") or argument.get("type") or "")))
                self._record_console(level, " ".join(part for part in parts if part) or str(params.get("text") or ""))
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            level = str(entry.get("level") or "").lower()
            if level in {"error", "warning"}:
                self._record_console(level, f"{entry.get('source') or ''}: {entry.get('text') or ''}".strip(": ").strip())
        elif method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            self._requests[str(params.get("requestId"))] = str(request.get("url") or "")
        elif method == "Network.responseReceived":
            response = params.get("response") or {}
            try:
                status = int(response.get("status") or 0)
            except (TypeError, ValueError):
                status = 0
            if status >= 400:
                url = str(response.get("url") or self._requests.get(str(params.get("requestId")), ""))
                self._record_failure(f"http_{status}", url, response.get("statusText") or "")
        elif method == "Network.loadingFailed":
            url = self._requests.get(str(params.get("requestId")), "")
            self._record_failure("network", url, params.get("errorText") or "")

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        ident = self.sequence
        self.connection.send(json.dumps({"id": ident, "method": method,
                                         "params": params or {}}))
        while True:
            message = json.loads(self.connection.recv())
            self._handle_event(message)
            if message.get("id") == ident:
                if message.get("error"):
                    raise VisualAcceptanceError(str(message["error"]))
                return message.get("result") or {}

    def set_timeout(self, seconds: float) -> None:
        """改回阻塞收包的等待时长。

        `drain` 为不卡住会把 recv 超时压到 0.3s，之后任何一次 `call` 都可能因此假性
        超时——所以要么显式恢复，要么用 drain 的默认恢复值。
        """
        self.recv_timeout = max(1.0, float(seconds))
        try:
            self.connection.settimeout(self.recv_timeout)
        except Exception:
            pass

    def drain(self, seconds: float = 1.5, *, restore: bool = True) -> None:
        """Collect late console/network events without waiting on a response."""
        deadline = time.monotonic() + max(0.1, float(seconds))
        try:
            self.connection.settimeout(0.3)
        except Exception:
            pass
        while time.monotonic() < deadline:
            try:
                message = json.loads(self.connection.recv())
            except Exception:
                continue
            self._handle_event(message)
        if restore:
            self.set_timeout(self.recv_timeout)

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        })
        if result.get("exceptionDetails"):
            raise VisualAcceptanceError(str(result["exceptionDetails"]))
        return (result.get("result") or {}).get("value")


def _entry_file(root: Path, entry: str = "") -> Path:
    requested = str(entry or "").strip().replace("\\", "/")
    candidates = [requested] if requested else ["index.html", "web/index.html"]
    for rel in candidates:
        candidate = (root / rel).resolve()
        if candidate.is_file() and (candidate == root or root in candidate.parents):
            if candidate.suffix.lower() in {".html", ".htm"}:
                return candidate
    raise VisualAcceptanceError("当前项目没有可用于真实网页验收的 index.html 入口。")


def preview_target(root: str | Path, entry: str = "",
                   url: str = "") -> tuple[str, "Path | None"]:
    """这次要打开的本机地址：给了 `url` 就用已经在跑的服务，否则用项目里的 HTML 入口。

    拆出来是为了让「url 模式不需要 index.html」这件事可以不启动浏览器就验证到。
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise VisualAcceptanceError("当前项目目录不存在。")
    if str(url or "").strip():
        target, err = loopback_url(url)
        if err:
            raise VisualAcceptanceError(err)
        return target, None
    return "", _entry_file(root_path, entry)


def capture_project_preview(root: str | Path, *, entry: str = "", evidence_dir: str | Path = "",
                            width: int = 960, height: int = 540, timeout: float = 25.0,
                            url: str = "", extra_flags: Any = ()) -> dict[str, Any]:
    """Capture a same-project browser preview and return bounded evidence.

    默认把项目目录临时当静态站服务并打开其中的 HTML 入口。给 `url`（只允许本机回环，
    如 dev_serve 起来的 http://127.0.0.1:5173/）时【不再起静态服务】，直接截那个已经
    在跑的服务——Vite/Vue 这类必须跑 dev server 的前端才预览得到。

    The returned ``image`` is transient model input.  The saved relative path
    is the durable workflow artifact and is served only through the workflow
    preview endpoint after the project-root check.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise VisualAcceptanceError("当前项目目录不存在。")
    target, entry_path = preview_target(root_path, entry, url)
    width = max(320, min(1920, int(width)))
    height = max(220, min(1200, int(height)))
    target_dir = Path(evidence_dir).resolve() if evidence_dir else (
        root_path / ".docmind" / "visual-evidence" /
        (time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()))
    if target_dir != root_path and root_path not in target_dir.parents:
        raise VisualAcceptanceError("视觉证据目录必须位于当前项目内。")
    target_dir.mkdir(parents=True, exist_ok=True)
    server = None
    server_thread = None
    if not target:
        # 已经指定了本机 URL 就直接打那个服务，不再多起一个静态站
        server, server_thread = serve_static(root_path)
    try:
        with browser_session(timeout, extra_flags) as devtools:
            devtools.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": False,
            })
            if target:
                separator = "&" if "?" in target else "?"
                preview_url = "%s%sdocmind=%d" % (target, separator, time.time_ns())
                shown_entry = preview_url
            else:
                rel_entry = entry_path.relative_to(root_path).as_posix()
                preview_url = ("http://127.0.0.1:%d/%s?docmind=%d"
                               % (server.server_port, rel_entry, time.time_ns()))
                shown_entry = rel_entry
            devtools.call("Page.navigate", {"url": preview_url})
            devtools.evaluate(
                "(async()=>{for(let n=0;n<120;n++){if(document.readyState==='complete')return true;"
                "await new Promise(r=>setTimeout(r,50))}throw Error('页面加载超时')})()"
            )
            devtools.drain(min(2.0, max(0.5, timeout * 0.1)))
            title = str(devtools.evaluate("document.title || ''") or "")[:240]
            body_text = str(devtools.evaluate("document.body?.innerText || ''") or "")[:1200]
            shot = devtools.call("Page.captureScreenshot", {"format": "png"})
            encoded = str(shot.get("data") or "")
            if not encoded:
                raise VisualAcceptanceError("浏览器没有返回截图。")
            raw = base64.b64decode(encoded)
            path = target_dir / "preview.png"
            path.write_bytes(raw)
            rel = path.relative_to(root_path).as_posix()
            console_errors = [item for item in devtools.console if item["level"] == "error"]
            console_warnings = [item for item in devtools.console if item["level"] == "warning"]
            checks = {
                "page_loaded": True,
                "no_runtime_errors": not devtools.runtime_errors,
                "no_console_errors": not console_errors,
                "no_failed_requests": not devtools.failed_requests,
            }
            return {
                "ok": all(checks.values()), "passed": all(checks.values()),
                "checks": checks, "title": title, "body_excerpt": body_text,
                "runtime_errors": devtools.runtime_errors,
                "console_errors": console_errors,
                "console_warnings": console_warnings,
                "failed_requests": devtools.failed_requests,
                "screenshot": rel,
                "image": encoded, "width": width, "height": height,
                "artifacts": [{
                    "id": "visual-preview",
                    "kind": "image",
                    "adapter": "visual",
                    "path": rel,
                    "label": "真实浏览器预览截图",
                    "summary": "正式开发舱 visual adapter 捕获的当前项目画面",
                    "evidence": ["browser:Page.captureScreenshot",
                                 "browser:Runtime.consoleAPICalled",
                                 "browser:Network.responseReceived",
                                 "entry:" + shown_entry],
                    "metadata": {"width": str(width), "height": str(height), "title": title,
                                 "console_errors": str(len(console_errors)),
                                 "failed_requests": str(len(devtools.failed_requests))},
                }],
            }
    except VisualAcceptanceError:
        raise
    except Exception as exc:
        raise VisualAcceptanceError(f"真实画面捕获失败：{type(exc).__name__}") from exc
    finally:
        stop_static(server, server_thread)


__all__ = ["VisualAcceptanceError", "browser_session", "capture_project_preview",
           "preview_target", "sanitize_flags", "serve_static", "stop_static"]
