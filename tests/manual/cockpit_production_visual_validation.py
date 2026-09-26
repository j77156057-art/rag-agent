"""Opt-in live check for the production visual acceptance tool.

This deliberately uses the shipped ``tools.preview_project`` registry entry,
not a test-only ToolSpec.  It edits a disposable copy of the ordinary HTML
fixture and stores the model/tool evidence under ``.docmind``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.6:35b-a3b-agent256k")
    args = parser.parse_args()
    run_root = Path(tempfile.mkdtemp(prefix="docmind-production-visual-",
                                      dir=str(REPO / ".docmind")))
    project = run_root / "project"
    evidence = run_root / "evidence"
    project.mkdir(parents=True)
    evidence.mkdir()
    shutil.copyfile(REPO / "tests" / "fixtures" / "cockpit-live" / "index.html",
                    project / "index.html")
    os.environ["DOCMIND_STATE_ROOT"] = str(run_root / "runtime")

    import config
    import tools
    from agent import Agent
    from agent_runtime.vision import analyze_images
    from agent_runtime.visual_acceptance import capture_project_preview
    from llm import LLMClient

    config.set_runtime("code_root", str(project))
    config.set_runtime("edit_confirm", False)
    client = LLMClient(provider="ollama", model=args.model)
    if client.capability.get("vision") != "native":
        raise RuntimeError("当前模型未确认具备原生视觉能力")
    before = capture_project_preview(project)
    registry = {name: tools.TOOLS[name] for name in
                ("read_file", "apply_edit", "preview_project", "self_verify")}
    agent = Agent(llm=client, tool_mode="native", tool_registry=registry)
    agent.tool_step_override = 12
    images, context, audit = analyze_images([before["image"]], current_capability=client.capability)
    prompt = (
        "请修改当前项目的 index.html。请先读取文件并根据附图真实画面修复按钮："
        "按钮必须完整位于画面内，宽至少200像素、高至少44像素、字号至少16像素，"
        "保持点击计数功能。必须实际调用 apply_edit 修改文件，修改后调用生产工具 "
        "preview_project 获取最新真实浏览器画面；不要只给建议或凭代码猜测。"
        "最后如实说明预览和自验证结果。当前是隔离示例项目，修改已授权。"
    )
    before_hash = hashlib.sha256((project / "index.html").read_bytes()).hexdigest()
    events = []
    started = time.monotonic()
    for event in agent.run(prompt, images=images, system_context=context,
                           thinking_enabled=False, stream=True):
        if event.get("type") != "token":
            events.append(event)
    after_hash = hashlib.sha256((project / "index.html").read_bytes()).hexdigest()
    preview_events = [event for event in events
                      if event.get("type") == "action" and "preview_project" in event.get("text", "")]
    artifact_events = [event for event in events if event.get("artifacts")]
    report = {
        "model": client.model,
        "provider": client.provider,
        "project": str(project),
        "seconds": round(time.monotonic() - started, 2),
        "vision": audit,
        "file_changed": before_hash != after_hash,
        "model_called_production_preview": bool(preview_events),
        "artifact_event_count": len(artifact_events),
        "verified": bool(agent.last_turn_record and agent.last_turn_record.get("verified")),
        "events": events,
        "passed": bool(before_hash != after_hash and preview_events and artifact_events),
    }
    (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(evidence / "report.json", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
