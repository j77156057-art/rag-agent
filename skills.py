"""技能热插拔：从目录扫 SKILL.md，注入系统提示 + 提供 dev_use_skill 工具。

技能目录：`<STATE_ROOT>/.docmind/skills/`（可用 `DOCMIND_SKILLS_DIR` 覆盖），
支持 `<dir>/SKILL.md` 与直接 `<dir>/<name>.md` 两种布局。文件头可选 YAML frontmatter：

  ---
  name: 音频工程
  description: 音频总线 / 混音 / 音效规范
  when_to_use: 用户问音频、混音、总线、音效时
  ---
  （正文：给模型的详细指引）

对 Agent 的作用：
  ① 目录（name/description/when_to_use）随系统提示注入，模型知道"有哪些技能可查"；
  ② 正文默认**不**注入（省 token），模型用 `dev_use_skill(name)` 按需取回。
`reload()` 支持热插拔——新增/修改 .md 后调 `POST /api/skills/reload` 即可生效，无需重启。
"""
from __future__ import annotations

import os
import threading

from config import STATE_ROOT, state_path

SKILLS_DIR = state_path("DOCMIND_SKILLS_DIR", os.path.join(STATE_ROOT, ".docmind", "skills"))
CATALOG_MAX = int(os.getenv("DOCMIND_SKILL_CATALOG_MAX", "20"))   # 注入目录最多几条
BODY_MAX = int(os.getenv("DOCMIND_SKILL_BODY_MAX", "6000"))       # 单技能正文回传上限

_lock = threading.Lock()
_skills = {}        # name -> {name, description, when_to_use, path, body}
_errors = []
_loaded = False


def _parse_frontmatter(text):
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            head = text[3:end].strip()
            body = text[end + 4:].lstrip("\n")
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, body


def reload():
    """重新扫描技能目录（热插拔）。返回 {loaded, errors, skills_dir}。"""
    global _loaded
    with _lock:
        _skills.clear()
        _errors.clear()
        if not os.path.isdir(SKILLS_DIR):
            _loaded = True
            return {"loaded": 0, "errors": [], "skills_dir": SKILLS_DIR}
        candidates = []
        for root, _dirs, files in os.walk(SKILLS_DIR):
            for fn in files:
                if fn.lower().endswith(".md"):
                    candidates.append(os.path.join(root, fn))
        for path in sorted(candidates):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
                meta, body = _parse_frontmatter(raw)
                # 技能名：frontmatter.name > 文件名（SKILL.md 用其父目录名）
                if fn_is_skill(path):
                    default_name = os.path.basename(os.path.dirname(path)) or "skill"
                else:
                    default_name = os.path.splitext(os.path.basename(path))[0]
                name = (meta.get("name") or default_name).strip()
                if not name or name in _skills:
                    continue
                # 首行标题可作 description 兜底
                desc = meta.get("description", "").strip()
                if not desc:
                    for line in body.splitlines():
                        line = line.strip()
                        if line.startswith("#"):
                            desc = line.lstrip("#").strip()
                            break
                _skills[name] = {
                    "name": name,
                    "description": desc[:200],
                    "when_to_use": meta.get("when_to_use", "").strip()[:200],
                    "path": os.path.relpath(path, SKILLS_DIR).replace("\\", "/"),
                    "body": body[:BODY_MAX],
                }
            except (OSError, ValueError) as e:
                _errors.append({"path": path, "error": f"{type(e).__name__}: {e}"})
        _loaded = True
        return {"loaded": len(_skills), "errors": list(_errors), "skills_dir": SKILLS_DIR}


def fn_is_skill(path):
    return os.path.basename(path).lower() == "skill.md"


def _ensure():
    if not _loaded:
        reload()


def get(name):
    _ensure()
    s = _skills.get(str(name or "").strip())
    return s["body"] if s else None


def list_skills():
    _ensure()
    return {
        "skills_dir": SKILLS_DIR,
        "exists": os.path.isdir(SKILLS_DIR),
        "count": len(_skills),
        "errors": list(_errors),
        "items": [{"name": s["name"], "description": s["description"],
                   "when_to_use": s["when_to_use"], "path": s["path"]}
                  for s in _skills.values()],
    }


def catalog_text():
    """给系统提示用的技能目录（不含正文）。无技能返回空串。"""
    _ensure()
    if not _skills:
        return ""
    lines = ["【可用技能】以下技能是项目自带的专项指引；当问题落在某技能的适用范围内时，"
             "先调用 `dev_use_skill` 取回其正文再作答："]
    for s in list(_skills.values())[:CATALOG_MAX]:
        seg = f"- {s['name']}：{s['description']}"
        if s["when_to_use"]:
            seg += f"（适用：{s['when_to_use']}）"
        lines.append(seg)
    return "\n".join(lines)


def use_skill(arg):
    """dev_use_skill 工具实现：按名字取回技能正文。"""
    name = (arg or "").strip()
    # 容忍 "name: xxx" 写法
    if name.lower().startswith("name:"):
        name = name.split(":", 1)[1].strip()
    body = get(name)
    if body is None:
        avail = "、".join(s["name"] for s in list(_skills.values())) or "（无）"
        return f"未找到技能「{name}」。可用技能：{avail}"
    return f"【技能：{name}】\n{body}"
