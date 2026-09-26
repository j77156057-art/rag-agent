"""Exercise the current WorkflowPreview Vue component in isolated Edge."""
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
PAGE = CACHE / "workflow-preview-smoke.html"
PREVIEW = CACHE / "workflow-preview-test.html"
PIXEL = CACHE / "workflow-preview-pixel.png"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    CACHE.mkdir(parents=True, exist_ok=True)
    if any(path.exists() for path in (PAGE, PREVIEW, PIXEL)):
        raise RuntimeError("Refusing to overwrite an existing UI fixture")
    process = None
    connection = None
    with tempfile.TemporaryDirectory(prefix="docmind-workflow-preview-", ignore_cleanup_errors=True) as profile:
        try:
            shutil.copyfile(Path(__file__).with_suffix(".html"), PAGE)
            PREVIEW.write_text(
                '<html><meta charset="utf-8"><style>body{margin:0;background:#183655;color:white;font:18px Arial}'
                'button{display:block;width:200px;height:44px;border:0;background:#e84c4c;color:white}'
                'canvas{display:block;margin-top:26px}</style><body><h1>项目画面</h1><button>运行项目</button>'
                '<canvas width="200" height="60"></canvas><script>'
                'window.parent.previewLoads=(window.parent.previewLoads||0)+1;'
                'document.querySelector("button").style.background=localStorage.getItem("preview-smoke-color")||"#e84c4c";'
                'const c=document.querySelector("canvas").getContext("2d");c.fillStyle="#70aaff";c.fillRect(0,0,200,60)'
                '</script></body></html>',
                encoding="utf-8",
            )
            Image.new("RGB", (320, 180), "#183655").save(PIXEL)
            process = subprocess.Popen(
                [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                 "--no-default-browser-check", "--remote-debugging-port=0",
                 "--remote-allow-origins=*", f"--user-data-dir={profile}",
                 "--window-size=1440,1100", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            active = Path(profile) / "DevToolsActivePort"
            for _ in range(100):
                if active.exists():
                    break
                time.sleep(.1)
            if not active.exists():
                raise RuntimeError("Edge remote debugging did not start")
            port = active.read_text().splitlines()[0]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as response:
                tabs = json.load(response)
            connection = websocket.create_connection(
                next(tab["webSocketDebuggerUrl"] for tab in tabs if tab["type"] == "page"),
                timeout=45, suppress_origin=True,
            )
            seq = 0

            def call(method, params=None):
                nonlocal seq
                seq += 1
                connection.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(connection.recv())
                    if message.get("id") == seq:
                        if "error" in message:
                            raise RuntimeError(message["error"])
                        return message["result"]

            call("Page.enable")
            call("Runtime.enable")
            call("Page.navigate", {"url": "http://127.0.0.1:5179/.docmind/workflow-preview-smoke.html"})
            result = call("Runtime.evaluate", {
                "expression": "(async()=>{for(let i=0;i<150;i++){if(window.runSmoke)return await window.runSmoke();"
                              "await new Promise(r=>setTimeout(r,100))}throw new Error('UI did not load')})()",
                "awaitPromise": True, "returnByValue": True,
            })
            if "exceptionDetails" in result:
                raise RuntimeError(json.dumps(result["exceptionDetails"], ensure_ascii=False))
            print(json.dumps(result["result"]["value"], ensure_ascii=False, indent=2))
        finally:
            if connection:
                connection.close()
            if process:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
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
