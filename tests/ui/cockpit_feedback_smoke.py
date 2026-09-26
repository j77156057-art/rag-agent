"""Run the real Vue feedback UI against controlled services in an isolated Edge.

Start frontend Vite on 127.0.0.1:5179 first. Requires the project's websocket-client.
No real project, browser profile, model, or backend is accessed.
"""
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
CACHE = ROOT / "frontend" / ".docmind"
PAGE = CACHE / "cockpit-feedback-smoke.html"
PREVIEW = CACHE / "cockpit-feedback-preview.html"
PIXEL = CACHE / "cockpit-feedback-pixel.png"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    CACHE.mkdir(parents=True, exist_ok=True)
    if any(path.exists() for path in (PAGE, PREVIEW, PIXEL)):
        raise RuntimeError("Refusing to overwrite an existing UI test fixture")
    process = None
    connection = None
    with tempfile.TemporaryDirectory(prefix="docmind-feedback-browser-", ignore_cleanup_errors=True) as profile:
        try:
            shutil.copyfile(Path(__file__).with_name("cockpit-feedback.html"), PAGE)
            Image.new("RGB", (320, 180), "#183655").save(PIXEL)
            PREVIEW.write_text('''<html><meta charset="utf-8"><style>body{margin:0;background:#183655;color:white;font:18px Arial}h1{margin:0;height:50px;font-size:22px}button{display:block;margin:0;width:200px;height:44px;border:0;background:#e84c4c;color:white}canvas{display:block;margin-top:26px;width:200px;height:60px}</style><body><h1>普通网页与画布</h1><button>运行项目</button><canvas width="200" height="60"></canvas><script>window.parent.previewLoads=(window.parent.previewLoads||0)+1;document.querySelector("button").style.background=localStorage.getItem("smoke-preview-color")||"#e84c4c";const c=document.querySelector("canvas").getContext("2d");c.fillStyle="#70aaff";c.fillRect(0,0,200,60)</script></body></html>''', encoding="utf-8")
            process = subprocess.Popen([str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", "--remote-allow-origins=*", f"--user-data-dir={profile}", "--window-size=1440,1100", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            active = Path(profile) / "DevToolsActivePort"
            for _ in range(100):
                if active.exists():
                    break
                time.sleep(.1)
            port = active.read_text().splitlines()[0]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as response:
                tabs = json.load(response)
            connection = websocket.create_connection(next(tab["webSocketDebuggerUrl"] for tab in tabs if tab["type"] == "page"), timeout=35, suppress_origin=True)
            seq = 0
            diagnostics = []

            def call(method, params=None):
                nonlocal seq
                seq += 1
                connection.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(connection.recv())
                    if message.get("method") in {"Runtime.exceptionThrown", "Runtime.consoleAPICalled", "Log.entryAdded", "Network.loadingFailed"}:
                        diagnostics.append(message)
                    if message.get("id") == seq:
                        if "error" in message:
                            raise RuntimeError(message["error"])
                        return message["result"]

            call("Page.enable")
            call("Runtime.enable")
            call("Log.enable")
            call("Network.enable")
            call("Page.navigate", {"url": "http://127.0.0.1:5179/.docmind/cockpit-feedback-smoke.html"})
            result = call("Runtime.evaluate", {"expression": "(async()=>{for(let i=0;i<150;i++){if(window.runSmoke)return await window.runSmoke();await new Promise(r=>setTimeout(r,100))}throw new Error('Test page did not load')})()", "awaitPromise": True, "returnByValue": True})
            if "exceptionDetails" in result:
                page = call("Runtime.evaluate", {"expression": "document.body.innerText.slice(0,2000)", "returnByValue": True})
                raise RuntimeError(json.dumps({"result": result, "page": page, "diagnostics": [item for item in diagnostics if item.get("method") == "Runtime.exceptionThrown"]}, ensure_ascii=True))
            screenshot = call("Page.captureScreenshot", {"format": "png"})
            output = ROOT / ".docmind" / "cockpit-feedback-smoke.png"
            output.parent.mkdir(exist_ok=True)
            output.write_bytes(base64.b64decode(screenshot["data"]))
            print(json.dumps(result["result"]["value"], ensure_ascii=False, indent=2))
            print(str(output))
        finally:
            if connection:
                connection.close()
            if process:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            PAGE.unlink(missing_ok=True)
            PREVIEW.unlink(missing_ok=True)
            PIXEL.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
