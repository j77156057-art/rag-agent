"""Opt-in live model validation: actual Agent, standard file tools, real browser.

Creates an isolated sample under .docmind/cockpit-validation, never switches the
user's current project or persists model/GPU settings. Needs local Ollama, Edge,
websocket-client, Pillow, and project dependencies. Not an automated CI test.
The preview/checker are validation adapters, not a shipped product endpoint.
"""
from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websocket

REPO = Path(__file__).resolve().parents[2]
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
sys.path.insert(0, str(REPO))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass  # Browser shutdown can close an outstanding static response.


class ProjectBrowser:
    def __init__(self, project: Path, evidence: Path, marker: str):
        self.project, self.evidence, self.marker = project, evidence, marker
        self.lock = threading.RLock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(project)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/index.html"
        self.profile = tempfile.TemporaryDirectory(prefix="docmind-live-preview-", ignore_cleanup_errors=True)
        self.process = self.connection = None
        self.sequence = self.capture_count = 0
        self.errors = []
        try:
            self.process = subprocess.Popen([str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", "--remote-allow-origins=*", f"--user-data-dir={self.profile.name}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            active = Path(self.profile.name) / "DevToolsActivePort"
            for _ in range(100):
                if active.exists():
                    break
                time.sleep(.1)
            port = active.read_text().splitlines()[0]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
                tabs = json.load(response)
            self.connection = websocket.create_connection(next(row["webSocketDebuggerUrl"] for row in tabs if row["type"] == "page"), timeout=20, suppress_origin=True)
            self.call("Page.enable")
            self.call("Runtime.enable")
            self.call("Emulation.setDeviceMetricsOverride", {"width": 960, "height": 540, "deviceScaleFactor": 1, "mobile": False})
        except Exception:
            self.close()
            raise

    def call(self, method, params=None):
        self.sequence += 1
        self.connection.send(json.dumps({"id": self.sequence, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.connection.recv())
            if message.get("method") == "Runtime.exceptionThrown":
                self.errors.append(message["params"]["exceptionDetails"].get("text", "runtime error"))
            if message.get("id") == self.sequence:
                if message.get("error"):
                    raise RuntimeError(message["error"])
                return message["result"]

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def snapshot(self, phase=1, name=None):
        with self.lock:
            self.errors = []
            self.call("Page.navigate", {"url": self.url + "?v=" + str(time.time_ns())})
            self.evaluate("(async()=>{for(let n=0;n<100;n++){if(document.readyState==='complete'&&document.getElementById('action'))return;await new Promise(r=>setTimeout(r,30))}throw Error('Project page not ready')})()")
            # The random marker exists only in the rendered screenshot. It is
            # deliberately absent from model prompts, files and tool text.
            self.evaluate("(()=>{const d=document.createElement('div');d.textContent='画面编号 '+" + json.dumps(self.marker) + ";d.style.cssText='position:fixed;right:16px;top:12px;background:#183655;color:white;font:22px Arial;padding:12px;border-radius:8px;z-index:9999';document.body.appendChild(d)})()")
            metrics = self.evaluate("""(()=>{
              const b=document.getElementById('action'),s=getComputedStyle(b),r=b.getBoundingClientRect();
              const visible=r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;
              const contrast=(a,z)=>{const lum=c=>{const v=(c.match(/[\\d.]+/g)||[]).slice(0,3).map(x=>{x=Number(x)/255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4});return .2126*v[0]+.7152*v[1]+.0722*v[2]};const x=lum(a),y=lum(z);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
              b.click();const one=document.getElementById('status').textContent;
              b.click();const two=document.getElementById('status').textContent;
              return {x:r.x,y:r.y,width:r.width,height:r.height,centerX:r.x+r.width/2,visible,font:parseFloat(s.fontSize),statusFont:parseFloat(getComputedStyle(document.getElementById('status')).fontSize),contrast:contrast(s.color,s.backgroundColor),background:s.backgroundColor,label:b.textContent.trim(),one,two};
            })()""")
            source = (self.project / "index.html").read_text(encoding="utf-8")
            styles = re.findall(r"<style\b[^>]*>(.*?)</style\s*>", source, re.I | re.S)
            metrics["tool_result_in_styles"] = any(re.search(r"^\s*Observation:\s*\[apply_edit result\]\s*$", style, re.M) for style in styles)
            metrics["protocol_marker_in_source"] = "<reset_dir_prompt>" in source
            checks = {
                "button_in_viewport": metrics["visible"],
                "button_centered": abs(metrics["centerX"] - 480) <= 40,
                "button_readable_size": metrics["height"] >= 44 and metrics["width"] >= 200 and metrics["font"] >= 16,
                "button_text_contrast": metrics["contrast"] >= 4.5,
                "page_runtime": not self.errors,
                "clean_style_source": not metrics["tool_result_in_styles"],
                "clean_project_source": not metrics["protocol_marker_in_source"],
                "status_readable_style": metrics["statusFont"] >= 18,
            }
            if phase == 2:
                color = [int(value) for value in metrics["background"].replace("rgb(", "").replace(")", "").split(",")]
                checks.update(green_button=color[1] > color[0] and color[1] > color[2], button_label=metrics["label"] == "再次运行", click_once=metrics["one"] == "运行次数：1", click_twice=metrics["two"] == "运行次数：2")
            else:
                checks["click_counter"] = "1" in metrics["one"] and "2" in metrics["two"]
            self.capture_count += 1
            name = name or f"preview-{self.capture_count}.png"
            raw = base64.b64decode(self.call("Page.captureScreenshot", {"format": "png"})["data"])
            path = self.evidence / name
            path.write_bytes(raw)
            return {"checks": checks, "metrics": metrics, "screenshot": str(path), "image": base64.b64encode(raw).decode(), "passed": all(checks.values())}

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
        if self.process:
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            self.process.wait(timeout=10)
            self.process = None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.profile.cleanup()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="")
    parser.add_argument("--resume-report", type=Path, help="Resume a failed second round from an isolated validation report; preserve its evidence")
    args = parser.parse_args()
    previous = None
    if args.resume_report:
        validation_root = (REPO / ".docmind" / "cockpit-validation").resolve()
        if not args.resume_report.resolve().is_relative_to(validation_root):
            raise ValueError("Resume report must belong to an isolated validation run")
        previous = json.loads(args.resume_report.read_text(encoding="utf-8"))
        prior_rounds = previous.get("previous_rounds", []) + previous.get("rounds", [])
        if not any(row.get("phase") == 1 and row.get("passed") for row in prior_rounds):
            raise ValueError("Resume requires a previously passed first round")
        resume_source = (Path(previous["project"]) / "index.html").resolve()
        if not resume_source.is_relative_to(validation_root):
            raise ValueError("Resume source must belong to the isolated validation directory")
    work = REPO / ".docmind" / "cockpit-validation" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2))
    project, evidence = work / "project", work / "evidence"
    project.mkdir(parents=True)
    evidence.mkdir()
    # Isolate accounting, project settings, model capability caches and traces
    # before importing the application; preserve existing inference options.
    os.environ["DOCMIND_STATE_ROOT"] = str(work / "runtime")
    from agent import Agent
    from agent_runtime.tools import Capability, ToolResult, ToolSpec
    from agent_runtime.vision import analyze_images
    import config
    import tools
    from llm import LLMClient

    shutil.copyfile(resume_source if previous else REPO / "tests" / "fixtures" / "cockpit-live" / "index.html", project / "index.html")
    config.set_runtime("code_root", str(project))
    config.set_runtime("edit_confirm", False)  # Only this disposable, pre-authorized test project.
    client = LLMClient(provider="ollama", model=args.model or config.LLM_MODEL)
    if client.capability.get("vision") != "native":
        raise RuntimeError("This live check requires a confirmed native vision model")
    marker = str(secrets.randbelow(900000) + 100000)
    browser = ProjectBrowser(project, evidence, marker)
    phase = {"value": 2 if previous else 1}
    snapshots = []
    preview_calls = {"value": 0}

    def preview(_):
        snapshot = browser.snapshot(phase["value"])
        preview_calls["value"] += 1
        snapshots.append({key: value for key, value in snapshot.items() if key != "image"})
        return ToolResult(True, json.dumps({"checks": snapshot["checks"], "screenshot": snapshot["screenshot"]}, ensure_ascii=False), data={"images": [snapshot["image"]]})

    def verify(_):
        snapshot = browser.snapshot(phase["value"])
        snapshots.append({key: value for key, value in snapshot.items() if key != "image"})
        messages = {
            "clean_style_source": "<style> 中混入 Observation: [apply_edit result]，请清除工具结果占位文字，避免后续 CSS 规则失效。",
            "clean_project_source": "项目源码中仍有 <reset_dir_prompt> 对话标记，请从源码删除；它可以出现在 old_text 中用于匹配，但不得再附加到 new_text 或任何工具输入末尾。",
            "status_readable_style": "状态文字字号不足18像素，请检查 #status 样式是否被非法 CSS 吞掉并修复。",
            "button_text_contrast": f"按钮文字对比度为 {snapshot['metrics']['contrast']:.2f}，要求至少4.5；请加深按钮背景或调整文字颜色后复验。",
        }
        failures = [{"scope": "browser", "file": "index.html", "error": messages.get(check, check)} for check, passed in snapshot["checks"].items() if not passed]
        return json.dumps({"ran": list(snapshot["checks"]), "passed": snapshot["passed"], "failures": failures}, ensure_ascii=False)

    registry = {name: tools.TOOLS[name] for name in ("read_file", "apply_edit")}
    registry["preview_project"] = ToolSpec("preview_project", "实际运行当前 index.html，返回截图和布局/点击行为检查。修改后必须调用此工具查看新画面。input 可写 index.html。", preview, capability=Capability.READ_LOCAL, group="code", parallel_safe=False)
    registry["self_verify"] = ToolSpec("self_verify", "验证当前项目的真实浏览器布局、字号、对比度和按钮点击行为。input: scope: auto\\nfiles: index.html", verify, group="code", parallel_safe=False)
    agent = Agent(llm=client, tool_mode="native", tool_registry=registry)
    agent.tool_step_override = 24  # Allow repair plus preview; test-local, not a product policy.
    report = {"model": client.model, "provider": client.provider, "project": str(project), "capability": client.capability, "rounds": [], "passed": False, "scope": "live Agent and standard file tools with browser validation adapters; full desktop UI not exercised"}
    if previous:
        report["resumed_from"] = str(args.resume_report.resolve())
        report["previous_rounds"] = prior_rounds
    try:
        initial = browser.snapshot(phase["value"], name="before.png")
        report["before"] = {key: value for key, value in initial.items() if key != "image"}
        prompts = [
            "请修改当前项目的 index.html。附图是当前真实画面：按钮位置、尺寸和可读性有问题。请把按钮移到面板中间标题下方，让按钮完整可见、宽至少200像素、高至少44像素、字号至少16像素，采用蓝色底和高对比文字，并保留点击计数功能。先 read_file，再 apply_edit 实际修改，修改后调用 preview_project 检查新画面，不要只给建议。最终答复请写出图片右上角的六位画面编号（编号只存在于图片中）。本独立示例项目的以上修改已授权。",
            "继续修改当前项目的 index.html：将按钮改成绿色，文字改为‘再次运行’，点击一次后页面显示‘运行次数：1’，再次点击显示‘运行次数：2’。保留上一轮的居中位置、尺寸与可读性。请读取最新文件，实际修改并调用 preview_project 查看新画面。修改已授权。",
        ]
        if previous:
            failed = [name for name, passed in initial["checks"].items() if not passed]
            prompts = [prompts[1] + " 上一次修改尚未通过验收；请依据当前截图和文件检查失败点，修复后再次验证，不要直接宣布完成。"
                       + f" 当前未通过项：{', '.join(failed)}；按钮文字对比度 {initial['metrics']['contrast']:.2f}，要求至少4.5。"
                       + " 如源码含 <reset_dir_prompt>，请删除它；工具参数不要附加任何对话标记，new_text 只能是应写入文件的代码。"]
        current_image = initial["image"]
        for index, prompt in enumerate(prompts, 2 if previous else 1):
            phase["value"] = index
            previews_before = preview_calls["value"]
            before_hash = hashlib.sha256((project / "index.html").read_bytes()).hexdigest()
            images, context, audit = analyze_images([current_image], current_capability=client.capability)
            events, started = [], time.monotonic()
            print(f"ROUND {index}: {client.model}", flush=True)
            for event in agent.run(prompt, images=images, system_context=context, thinking_enabled=False, stream=True):
                if event.get("type") == "token":
                    continue  # Final text is recorded once; avoid a quadratic token log.
                events.append(event)
                if event.get("type") in {"action", "observation", "final", "reflection"}:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                (evidence / f"round-{index}-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
            after = browser.snapshot(index, f"after-{index}.png")
            final = next((event.get("text", "") for event in reversed(events) if event.get("type") == "final"), "")
            result = {"phase": index, "seconds": round(time.monotonic() - started, 2), "vision": audit, "final": final, "file_changed": before_hash != hashlib.sha256((project / "index.html").read_bytes()).hexdigest(), "model_called_preview": preview_calls["value"] > previews_before, "verification": agent.last_turn_record.get("verified") if agent.last_turn_record else False, "after": {key: value for key, value in after.items() if key != "image"}}
            if index == 1:
                result["screenshot_marker_read"] = marker in final
            result["passed"] = result["file_changed"] and result["model_called_preview"] and result["verification"] and after["passed"] and result.get("screenshot_marker_read", True)
            report["rounds"].append(result)
            (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if not result["passed"]:
                raise RuntimeError(f"Round {index} failed real acceptance checks; see {evidence / 'report.json'}")
            current_image = after["image"]
        report["passed"] = True
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["snapshots"] = snapshots
        (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
        print(str(evidence / "report.json"), flush=True)
    print("LIVE VALIDATION PASSED", flush=True)


if __name__ == "__main__":
    main()
