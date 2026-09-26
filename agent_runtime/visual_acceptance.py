"""Browser backed visual acceptance for the autonomous development cockpit.

The production workflow uses this small adapter for ordinary web previews.  It
serves only the current project directory, captures a real browser frame, and
returns the image through the normal tool vision channel.  Domain specific
engines can register richer adapters later; a missing browser or unsupported
entry point is reported as an explicit verification failure.
"""
from __future__ import annotations

import base64
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
import urllib.request


class VisualAcceptanceError(RuntimeError):
    """A visual capture could not produce trustworthy evidence."""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

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


class _DevTools:
    def __init__(self, connection, runtime_errors: list[str] | None = None):
        self.connection = connection
        self.sequence = 0
        self.runtime_errors = runtime_errors if runtime_errors is not None else []

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        ident = self.sequence
        self.connection.send(json.dumps({"id": ident, "method": method,
                                         "params": params or {}}))
        while True:
            message = json.loads(self.connection.recv())
            if message.get("method") == "Runtime.exceptionThrown":
                details = message.get("params", {}).get("exceptionDetails", {})
                text = str(details.get("text") or details.get("exception", {}).get("description") or "runtime error")
                if text not in self.runtime_errors:
                    self.runtime_errors.append(text[:500])
            if message.get("id") == ident:
                if message.get("error"):
                    raise VisualAcceptanceError(str(message["error"]))
                return message.get("result") or {}

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


def capture_project_preview(root: str | Path, *, entry: str = "", evidence_dir: str | Path = "",
                            width: int = 960, height: int = 540, timeout: float = 25.0) -> dict[str, Any]:
    """Capture a same-project browser preview and return bounded evidence.

    The returned ``image`` is transient model input.  The saved relative path
    is the durable workflow artifact and is served only through the workflow
    preview endpoint after the project-root check.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise VisualAcceptanceError("当前项目目录不存在。")
    entry_path = _entry_file(root_path, entry)
    width = max(320, min(1920, int(width)))
    height = max(220, min(1200, int(height)))
    edge = _edge_binary()
    try:
        import websocket
    except Exception as exc:  # pragma: no cover - depends on desktop bundle
        raise VisualAcceptanceError("浏览器控制依赖 websocket-client 未安装。") from exc

    target_dir = Path(evidence_dir).resolve() if evidence_dir else (
        root_path / ".docmind" / "visual-evidence" /
        (time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()))
    if target_dir != root_path and root_path not in target_dir.parents:
        raise VisualAcceptanceError("视觉证据目录必须位于当前项目内。")
    target_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 functools.partial(_QuietHandler, directory=str(root_path)))
    server_thread = threading.Thread(target=server.serve_forever,
                                     name="visual-preview-http", daemon=True)
    server_thread.start()
    profile = tempfile.TemporaryDirectory(prefix="docmind-visual-preview-",
                                           ignore_cleanup_errors=True)
    process = None
    connection = None
    runtime_errors: list[str] = []
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen([
            edge, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--remote-debugging-port=0",
            "--remote-allow-origins=*", f"--user-data-dir={profile.name}",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        active = Path(profile.name) / "DevToolsActivePort"
        deadline = time.monotonic() + min(15.0, max(3.0, timeout / 2))
        while not active.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not active.exists():
            raise VisualAcceptanceError("浏览器调试端口未就绪。")
        port = active.read_text(encoding="utf-8").splitlines()[0].strip()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
            tabs = json.load(response)
        page = next((row for row in tabs if row.get("type") == "page"), None)
        if not page or not page.get("webSocketDebuggerUrl"):
            raise VisualAcceptanceError("浏览器没有可用页面标签。")
        connection = websocket.create_connection(page["webSocketDebuggerUrl"],
                                                   timeout=max(5.0, timeout),
                                                   suppress_origin=True)
        devtools = _DevTools(connection, runtime_errors)
        devtools.call("Page.enable")
        devtools.call("Runtime.enable")
        devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1,
            "mobile": False,
        })
        url = f"http://127.0.0.1:{server.server_port}/{entry_path.relative_to(root_path).as_posix()}?docmind={time.time_ns()}"
        devtools.call("Page.navigate", {"url": url})
        devtools.evaluate(
            "(async()=>{for(let n=0;n<120;n++){if(document.readyState==='complete')return true;"
            "await new Promise(r=>setTimeout(r,50))}throw Error('页面加载超时')})()"
        )
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
        checks = {"page_loaded": True, "runtime_errors": not runtime_errors}
        return {
            "ok": all(checks.values()), "passed": all(checks.values()),
            "checks": checks, "title": title, "body_excerpt": body_text,
            "runtime_errors": runtime_errors, "screenshot": rel,
            "image": encoded, "width": width, "height": height,
            "artifacts": [{
                "id": "visual-preview",
                "kind": "image",
                "adapter": "visual",
                "path": rel,
                "label": "真实浏览器预览截图",
                "summary": "正式开发舱 visual adapter 捕获的当前项目画面",
                "evidence": ["browser:Page.captureScreenshot", "entry:" + entry_path.relative_to(root_path).as_posix()],
                "metadata": {"width": str(width), "height": str(height), "title": title},
            }],
        }
    except VisualAcceptanceError:
        raise
    except Exception as exc:
        raise VisualAcceptanceError(f"真实画面捕获失败：{type(exc).__name__}") from exc
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
        profile.cleanup()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


__all__ = ["VisualAcceptanceError", "capture_project_preview"]
