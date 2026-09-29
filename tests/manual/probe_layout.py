"""诊断界面布局：把开发舱截下来并量各容器的盒子（需要后端在 127.0.0.1:8000 已启动）。

用法：.venv\\Scripts\\python.exe -B tests\\manual\\probe_layout.py [宽] [高]

输出：控制台打印各容器的 box/computed style，落盘 `.tmp/layout.json`（含舞台子树清单）
与 `.tmp/shot_layout.png`（截图，人眼复核用）。量不到时会直接打印页面摘要（例如后端没起
会显示 `ERR_CONNECTION_REFUSED`），不用对着一堆 MISSING 猜。

**为什么不用 playwright**（本文件曾经用它）：playwright 启动的浏览器拿不到进程句柄，
没法绑 kill-on-close 的作业对象；父进程被硬杀（会话重启、工具超时）时它必然留下孤儿
headless 进程——实测 TEMP 里攒了一堆 `playwright_chromiumdev_profile-*`，几百个 msedge
把 CPU 顶到 99°C。这里改用 `agent_runtime.visual_acceptance.browser_session`：它把浏览器
绑进作业对象（父进程一死整棵树跟着没）、退出时显式 `taskkill /T /F`、profile 带重试回收、
启动前还会清扫陈旧 profile。**新建浏览器验证一律走这条路径，不要再引 playwright。**
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent_runtime.visual_acceptance import browser_session  # noqa: E402

URL = "http://127.0.0.1:8000/workbench"
WIDTH = int(sys.argv[1]) if len(sys.argv) > 1 else 2521
HEIGHT = int(sys.argv[2]) if len(sys.argv) > 2 else 1366

MEASURE = r"""
(() => {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return { sel, missing: true };
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      sel,
      box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      display: cs.display, flexDirection: cs.flexDirection, justifyContent: cs.justifyContent,
      justifyItems: cs.justifyItems, alignItems: cs.alignItems,
      width: cs.width, maxWidth: cs.maxWidth,
      marginLeft: cs.marginLeft, marginRight: cs.marginRight,
      paddingLeft: cs.paddingLeft, paddingRight: cs.paddingRight,
      position: cs.position,
    };
  };
  const outline = (rootSel, depth = 3) => {
    const root = document.querySelector(rootSel);
    if (!root) return [{ sel: rootSel, missing: true }];
    const rows = [];
    const walk = (el, level, path) => {
      const r = el.getBoundingClientRect();
      rows.push({
        path,
        role: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
          ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : ''),
        box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
        text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 46),
      });
      if (level >= depth) return;
      [...el.children].forEach((child, i) => walk(child, level + 1, path + '>' + i));
    };
    walk(root, 0, rootSel);
    return rows;
  };
  const sels = [
    '.acp', '.acp-stage', '.acp-stage-content', '.acp-stage-bar',
    '.acp-execute-region', '.acp-preview-region', '.acp-runtime-slot',
    '.acp-preview-output', '.acp-workflow-surface', '.acp-collaborator',
    '#wb-cockpit-runtime-slot', '.sr-panel', '.sr-panel.sr-docked',
    '.pb-pop', '.pb-pop-inline', '.pb-body', '.pb-main', '.pb-side', '.pb-framewrap', '.pb-bugs',
    '.acp-realtime-alert-banner', '.acp-vision-alert-banner',
  ];
  return {
    viewport: [window.innerWidth, window.innerHeight],
    scroll: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
    items: sels.map(pick),
    stageOutline: outline('.acp-stage-content', 2),
    bodyOutline: outline('.pb-body, .sr-panel', 2),
  };
})()
"""


CLICK_COCKPIT = r"""
(() => {
  const nodes = [...document.querySelectorAll('button,a,[role="tab"],[role="button"]')];
  const hit = nodes.find(el => (el.textContent || '').trim() === '开发舱');
  if (!hit) return false;
  hit.click();
  return true;
})()
"""


def _wait_complete(devtools, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if str(devtools.evaluate("document.readyState")) == "complete":
            return
        time.sleep(0.2)


def main() -> int:
    # 落在仓库根下的 .tmp/：从任何工作目录运行都写到同一处（.tmp 是仓库既有的临时约定）
    out_dir = ROOT / ".tmp"
    out_dir.mkdir(exist_ok=True)
    out_json = out_dir / "layout.json"
    out_png = out_dir / "shot_layout.png"
    # browser_session 是 contextmanager：无论正常结束还是异常，浏览器与 profile 都收干净。
    with browser_session(timeout=45.0) as devtools:
        devtools.call("Emulation.setDeviceMetricsOverride",
                      {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False})
        devtools.call("Page.navigate", {"url": URL})
        _wait_complete(devtools)
        time.sleep(4.0)  # 等 SPA 挂载（开发舱是独立分包）
        clicked = devtools.evaluate(CLICK_COCKPIT)
        if clicked:
            time.sleep(4.0)  # 切换工作区要等分包加载完
        data = devtools.evaluate(MEASURE)
        if data["items"][0].get("missing"):
            # 量不到就说明没进开发舱：把页面摘要打出来，别让人对着 "MISSING" 猜
            print("未挂载开发舱（点击命中:", clicked, "）；页面摘要：")
            print(devtools.evaluate("(document.body.innerText || '').slice(0, 300)"))
        shot = devtools.call("Page.captureScreenshot", {"format": "png"})
        out_png.write_bytes(base64.b64decode(shot["data"]))

    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("viewport", data["viewport"], "scroll", data["scroll"])
    for item in data["items"]:
        if item.get("missing"):
            print("  MISSING", item["sel"])
        else:
            print(f"  {item['sel']:<26} box={item['box']} display={item['display']}"
                  f"/{item.get('flexDirection')} maxW={item['maxWidth']}"
                  f" mL={item['marginLeft']} mR={item['marginRight']}")
    print("screenshot ->", out_png, "| metrics ->", out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
