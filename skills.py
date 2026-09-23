"""技能热插拔：加载内置与用户 SKILL.md，注入提示并提供 dev_use_skill。

内置技能位于 `<BASE_DIR>/agent_skills/`，会随源码和桌面包发布；用户技能位于
`<STATE_ROOT>/.docmind/skills/`（可用 `DOCMIND_SKILLS_DIR` 覆盖），同名用户技能
覆盖内置技能。支持 `<dir>/SKILL.md` 与技能根目录下的 `<name>.md` 两种布局。
文件头可选 YAML frontmatter：

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
import re
import json
import hashlib
import time
import threading

from config import BASE_DIR, STATE_ROOT, state_path

SKILLS_DIR = state_path("DOCMIND_SKILLS_DIR", os.path.join(STATE_ROOT, ".docmind", "skills"))
BUILTIN_SKILLS_DIR = os.path.join(BASE_DIR, "agent_skills")
CATALOG_MAX = int(os.getenv("DOCMIND_SKILL_CATALOG_MAX", "20"))   # 注入目录最多几条
BODY_MAX = int(os.getenv("DOCMIND_SKILL_BODY_MAX", "6000"))       # 单技能正文回传上限

_lock = threading.Lock()
_skills = {}        # name -> {name, description, when_to_use, path, body}
_errors = []
_conflicts = []
_stats = {}         # name -> {uses, successes, failures, last_used}
_loaded = False


def _events_path():
    return os.path.join(SKILLS_DIR, "skill_events.jsonl")


def _record_event(name, event, **payload):
    row = {"ts": int(time.time()), "name": str(name)[:120], "event": str(event)[:40], **payload}
    try:
        path = os.path.abspath(_events_path())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _skill_version(value):
    value = str(value or "1.0.0").strip()
    return value[:40] if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]*", value) else "1.0.0"


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


def _candidate_files(root):
    """Return root-level markdown and nested SKILL.md files only.

    Supporting reference markdown inside a skill is intentionally excluded;
    otherwise one installed skill can accidentally occupy the whole catalog.
    """
    if not os.path.isdir(root):
        return []
    candidates = []
    for current, dirs, files in os.walk(root):
        # Version snapshots live under .history and must never become skills.
        dirs[:] = [item for item in dirs if not item.startswith(".")]
        for filename in files:
            lower = filename.lower()
            if lower == "skill.md" or (current == root and lower.endswith(".md")):
                candidates.append(os.path.join(current, filename))
    return sorted(candidates)


def reload():
    """重新扫描内置与用户技能；后加载的用户技能可覆盖同名内置技能。"""
    global _loaded
    with _lock:
        _skills.clear()
        _conflicts.clear()
        _stats.clear()
        try:
            with open(os.path.abspath(_events_path()), encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not row.get("name"):
                        continue
                    stats = _stats.setdefault(row["name"], {"uses": 0, "successes": 0, "failures": 0})
                    if row.get("event") == "use":
                        stats["uses"] += 1
                        stats["last_used"] = row.get("ts")
                    elif row.get("event") == "result":
                        stats["successes" if row.get("passed") else "failures"] += 1
                        if row.get("score") is not None:
                            stats["last_score"] = row.get("score")
        except (OSError, ValueError):
            pass
        _errors.clear()
        sources = (("builtin", BUILTIN_SKILLS_DIR), ("user", SKILLS_DIR))
        for source, root in sources:
            for path in _candidate_files(root):
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
                    if not name:
                        continue
                    # 首行标题可作 description 兜底
                    desc = meta.get("description", "").strip()
                    if not desc:
                        for line in body.splitlines():
                            line = line.strip()
                            if line.startswith("#"):
                                desc = line.lstrip("#").strip()
                                break
                    version = _skill_version(meta.get("version"))
                    item = {
                        "name": name,
                        "description": desc[:200],
                        "when_to_use": meta.get("when_to_use", "").strip()[:200],
                        "path": os.path.relpath(path, root).replace("\\", "/"),
                        "source": source,
                        "version": version,
                        "checksum": hashlib.sha256(body[:BODY_MAX].encode("utf-8")).hexdigest()[:16],
                        "body": body[:BODY_MAX],
                    }
                    if name in _skills:
                        _conflicts.append({"name": name, "kept": source,
                                           "replaced": _skills[name].get("source"),
                                           "kept_version": version,
                                           "replaced_version": _skills[name].get("version")})
                    _skills[name] = item
                except (OSError, ValueError) as e:
                    _errors.append({"path": path, "error": f"{type(e).__name__}: {e}"})
        _loaded = True
    return {"loaded": len(_skills), "errors": list(_errors),
                "skills_dir": SKILLS_DIR, "builtin_skills_dir": BUILTIN_SKILLS_DIR,
                "conflicts": list(_conflicts)}


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
    def history_versions(name):
        history_dir = os.path.join(os.path.abspath(SKILLS_DIR), ".history", name)
        rows = []
        if not os.path.isdir(history_dir):
            return rows
        for path in sorted((os.path.join(history_dir, item) for item in os.listdir(history_dir)
                            if item.lower().endswith(".md")), reverse=True)[:20]:
            try:
                with open(path, "r", encoding="utf-8") as stream:
                    meta, body = _parse_frontmatter(stream.read())
                rows.append({"version": _skill_version(meta.get("version")),
                             "checksum": hashlib.sha256(body[:BODY_MAX].encode("utf-8")).hexdigest()[:16]})
            except (OSError, ValueError):
                continue
        return rows
    return {
        "skills_dir": SKILLS_DIR,
        "builtin_skills_dir": BUILTIN_SKILLS_DIR,
        "exists": os.path.isdir(SKILLS_DIR),
        "builtin_exists": os.path.isdir(BUILTIN_SKILLS_DIR),
        "count": len(_skills),
        "errors": list(_errors),
        "conflicts": list(_conflicts),
        "items": [{"name": s["name"], "description": s["description"],
                   "when_to_use": s["when_to_use"], "path": s["path"],
                   "source": s["source"], "version": s.get("version", "1.0.0"),
                   "checksum": s.get("checksum", ""),
                   "history_versions": history_versions(s["name"]) if s["source"] == "user" else [],
                   "stats": dict(_stats.get(s["name"], {}))}
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
    stats = _stats.setdefault(name, {"uses": 0, "successes": 0, "failures": 0})
    stats["uses"] = int(stats.get("uses", 0)) + 1
    stats["last_used"] = int(time.time())
    _record_event(name, "use")
    return f"【技能：{name}】\n{body}"


def save_user_skill(name: str, description: str, body: str, version: str = "1.0.0") -> dict:
    """Persist a user-approved generated skill; never overwrites an existing skill."""
    clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "").strip()).strip(".-")[:80]
    if not clean_name:
        raise ValueError("技能名称不能为空")
    clean_description = str(description or "").replace("\x00", "").strip()[:240]
    clean_body = str(body or "").replace("\x00", "").strip()[:BODY_MAX]
    if not clean_body:
        raise ValueError("技能正文不能为空")
    root = os.path.abspath(SKILLS_DIR)
    target_dir = os.path.join(root, clean_name)
    target = os.path.join(target_dir, "SKILL.md")
    os.makedirs(root, exist_ok=True)
    if os.path.exists(target):
        raise FileExistsError("技能已存在：%s" % clean_name)
    os.makedirs(target_dir, exist_ok=False)
    clean_version = _skill_version(version)
    raw = "---\nname: %s\nversion: %s\ndescription: %s\nwhen_to_use: 游戏开发工作流成功复用时\n---\n\n%s\n" % (
        clean_name, clean_version, clean_description.replace("\n", " "), clean_body)
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
    except Exception:
        try:
            os.rmdir(target_dir)
        except OSError:
            pass
        raise
    reload()
    return {"name": clean_name, "description": clean_description,
            "version": clean_version,
            "checksum": hashlib.sha256(clean_body.encode("utf-8")).hexdigest()[:16],
            "path": os.path.relpath(target, root).replace("\\", "/"), "source": "user"}


def update_user_skill(name: str, description: str, body: str, version: str = "1.0.0") -> dict:
    """Write a new user-skill version while retaining the prior file.

    The regular save API remains create-only.  Updates are explicit so a
    generated Skill candidate cannot silently replace a user's instructions.
    """
    clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "").strip()).strip(".-")[:80]
    if not clean_name:
        raise ValueError("技能名称不能为空")
    target = os.path.join(os.path.abspath(SKILLS_DIR), clean_name, "SKILL.md")
    if not os.path.isfile(target):
        return save_user_skill(clean_name, description, body, version=version)
    with open(target, "r", encoding="utf-8") as stream:
        previous = stream.read()
    history_dir = os.path.join(os.path.abspath(SKILLS_DIR), ".history", clean_name)
    os.makedirs(history_dir, exist_ok=True)
    stamp = "%d-%s" % (int(time.time() * 1000), _skill_version(_parse_frontmatter(previous)[0].get("version")))
    snapshot = os.path.join(history_dir, stamp + ".md")
    with open(snapshot, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(previous)
    os.remove(target)
    try:
        os.rmdir(os.path.dirname(target))
    except OSError:
        pass
    try:
        updated = save_user_skill(clean_name, description, body, version=version)
    except Exception:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(previous)
        raise
    updated["history_saved"] = True
    return updated


def record_result(name: str, passed: bool, *, score: float | None = None) -> dict:
    """Record evaluator feedback without storing workflow/user content."""
    clean = str(name or "").strip()
    stats = _stats.setdefault(clean, {"uses": 0, "successes": 0, "failures": 0})
    key = "successes" if passed else "failures"
    stats[key] = int(stats.get(key, 0)) + 1
    if score is not None:
        stats["last_score"] = round(float(score), 4)
    _record_event(clean, "result", passed=bool(passed), score=score)
    return dict(stats)


def skill_statistics(name: str = "") -> dict:
    _ensure()
    if name:
        return dict(_stats.get(str(name).strip(), {"uses": 0, "successes": 0, "failures": 0}))
    return {key: dict(value) for key, value in _stats.items()}


def skill_regression(name: str, *, baseline_success_rate: float | None = None,
                     min_success_rate: float = 0.0) -> dict:
    """Evaluate whether a Skill's observed results regressed.

    The report is metadata-only and deliberately treats a skill with no
    evaluated runs as unscored rather than as a failure.
    """
    clean = str(name or "").strip()
    stats = skill_statistics(clean)
    successes = int(stats.get("successes", 0) or 0)
    failures = int(stats.get("failures", 0) or 0)
    evaluated = successes + failures
    rate = (successes / evaluated) if evaluated else None
    try:
        threshold = max(0.0, min(1.0, float(min_success_rate)))
    except (TypeError, ValueError):
        threshold = 0.0
    baseline = None if baseline_success_rate is None else max(
        0.0, min(1.0, float(baseline_success_rate)))
    regressed = bool(rate is not None and (
        rate < threshold or (baseline is not None and rate < baseline)))
    return {"name": clean, "uses": int(stats.get("uses", 0) or 0),
            "successes": successes, "failures": failures,
            "evaluated": evaluated, "success_rate": rate,
            "baseline_success_rate": baseline,
            "min_success_rate": threshold, "scored": rate is not None,
            "regressed": regressed}


def rollback_user_skill(name: str) -> dict:
    """Restore the latest prior user-skill version, or remove a new skill."""
    clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "").strip()).strip(".-")[:80]
    target_dir = os.path.join(os.path.abspath(SKILLS_DIR), clean_name)
    target = os.path.join(target_dir, "SKILL.md")
    if not os.path.isfile(target):
        raise FileNotFoundError("用户技能不存在：%s" % clean_name)
    history_dir = os.path.join(os.path.abspath(SKILLS_DIR), ".history", clean_name)
    snapshots = []
    if os.path.isdir(history_dir):
        snapshots = sorted(
            (os.path.join(history_dir, item) for item in os.listdir(history_dir)
             if item.lower().endswith(".md")), reverse=True)
    if snapshots:
        snapshot = snapshots[0]
        with open(snapshot, "r", encoding="utf-8") as stream:
            previous = stream.read()
        with open(target, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(previous)
        os.remove(snapshot)
        if not os.listdir(history_dir):
            os.rmdir(history_dir)
    else:
        os.remove(target)
        try:
            os.rmdir(target_dir)
        except OSError:
            pass
    _record_event(clean_name, "rollback")
    reload()
    return {"name": clean_name, "rolled_back": True,
            "restored_previous": bool(snapshots),
            "remaining_versions": len(snapshots) - (1 if snapshots else 0)}
