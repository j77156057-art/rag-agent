"""Game-development helpers built on top of the region workspace."""
import json, os, re, subprocess, math, time, mimetypes, sys, ast as _ast
from datetime import datetime

def _root(root):
    return os.path.abspath(root) if root else ""

def _file(root, name):
    base = _root(root); p = os.path.abspath(os.path.join(base, name))
    if not (p == base or p.startswith(base + os.sep)): raise ValueError("path outside project")
    return p

def _jsonl(path):
    if not os.path.isfile(path): return []
    out=[]
    with open(path, encoding="utf-8") as f:
        for line in f:
            try: out.append(json.loads(line))
            except ValueError: pass
    return out

def list_tasks(root, status=""):
    rows = _jsonl(_file(root, ".docmind_tasks.jsonl"))
    return [x for x in rows if not status or x.get("status") == status]

def upsert_task(root, task):
    path = _file(root, ".docmind_tasks.jsonl"); rows = _jsonl(path)
    tid = (task.get("id") or "TASK-" + datetime.now().strftime("%Y%m%d-%H%M%S")).strip()
    item = {"id": tid, "title": task.get("title", ""), "description": task.get("description", ""), "region": task.get("region", ""), "priority": task.get("priority", "normal"), "status": task.get("status", "open"), "owner": task.get("owner", ""), "files": task.get("files", []), "updated_at": datetime.now().isoformat(timespec="seconds")}
    rows = [x for x in rows if x.get("id") != tid] + [item]
    with open(path, "w", encoding="utf-8") as f:
        for x in rows: f.write(json.dumps(x, ensure_ascii=False) + "\n")
    return item

def validate_data(root):
    errors=[]; checked=0
    for base, dirs, files in os.walk(_root(root)):
        dirs[:] = [d for d in dirs if d not in {".git",".venv","__pycache__","build","dist","node_modules",".chroma"}]
        for name in files:
            if os.path.splitext(name)[1].lower() not in {".json",".yaml",".yml",".csv",".toml"}: continue
            path=os.path.join(base,name); checked+=1
            if name.endswith(".json"):
                try:
                    with open(path, encoding="utf-8") as f: json.load(f)
                except Exception as e: errors.append({"path":os.path.relpath(path,root),"error":str(e)})
    return {"ok": not errors, "checked": checked, "errors": errors}

def localization_check(root):
    keys={}; missing=[]
    for base, dirs, files in os.walk(_root(root)):
        dirs[:] = [d for d in dirs if d not in {".git",".venv","__pycache__","build","dist","node_modules"}]
        for name in files:
            if not re.search(r"(locale|localization|i18n|translation)", name, re.I): continue
            try:
                with open(os.path.join(base,name), encoding="utf-8") as f: data=json.load(f)
                if isinstance(data,dict): keys[name]=set(data)
            except Exception: pass
    if len(keys)>1:
        ref=set.intersection(*keys.values())
        allk=set.union(*keys.values())
        missing=[{"file":n,"keys":sorted(allk-v)} for n,v in keys.items() if v != allk]
    return {"ok": not missing, "files": list(keys), "missing": missing}

def release_check(root):
    data=validate_data(root); loc=localization_check(root); warnings=[]
    if os.path.isfile(_file(root,".env")): warnings.append("项目包含 .env，请确认发布包未携带密钥")
    return {"ok": data["ok"] and loc["ok"] and not warnings, "data": data, "localization": loc, "warnings": warnings}

def project_memory(root, content=None):
    path=_file(root,"DOCMIND_MEMORY.md")
    if content is not None:
        with open(path,"w",encoding="utf-8") as f: f.write(content)
    return open(path,encoding="utf-8").read() if os.path.isfile(path) else ""

def simulate_growth(levels=50, base=100, growth=1.08):
    levels=max(1,min(int(levels),1000)); base=float(base); growth=float(growth)
    return [{"level":i,"value":round(base*(growth**(i-1)),4)} for i in range(1,levels+1)]

def asset_dependencies(root):
    refs=[]
    for base,dirs,files in os.walk(_root(root)):
        dirs[:]=[d for d in dirs if d not in {".git",".venv","build","dist",".chroma","__pycache__"}]
        for name in files:
            if os.path.splitext(name)[1].lower() not in {".py",".js",".ts",".cs",".json",".yaml",".yml"}: continue
            p=os.path.join(base,name)
            try: text=open(p,encoding="utf-8",errors="ignore").read()
            except OSError: continue
            for m in re.finditer(r"(?:asset|sprite|texture|sound|audio|resource)[_:/\"']+([A-Za-z0-9_./-]+)",text,re.I):
                refs.append({"source":os.path.relpath(p,root),"asset":m.group(1)})
    return refs

def preview_resource(root, rel):
    p=_file(root,rel); st=os.stat(p); mime=mimetypes.guess_type(p)[0] or "application/octet-stream"
    return {"path":rel.replace("\\","/"),"mime":mime,"size":st.st_size,"extension":os.path.splitext(p)[1].lower()}

def create_placeholder(root, rel, kind="text"):
    p=_file(root,rel); os.makedirs(os.path.dirname(p),exist_ok=True)
    if os.path.exists(p): raise ValueError("file already exists")
    if kind == "json": content="{}\n"
    elif kind == "svg": content='<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect width="128" height="128" fill="#777"/><text x="10" y="68" fill="white">PLACEHOLDER</text></svg>\n'
    else: content="# PLACEHOLDER\n"
    open(p,"w",encoding="utf-8").write(content); return preview_resource(root,rel)

def _rel(root_abs, src):
    """把检索到的 source 路径规整为相对 code_root 的路径（已是相对则原样返回）。"""
    try:
        if os.path.isabs(src) and src.startswith(root_abs):
            return os.path.relpath(src, root_abs)
    except Exception:  # noqa: BLE001
        pass
    return src


def impact_analysis(root, query, k=8):
    """返回受改动影响最大的源码文件（相对路径，去重排序）。

    优先用语义检索（需代码库已索引进 Chroma 的 code 集合，bge-m3 向量）；
    不可用时退回大小写不敏感子串 grep，保证任何环境都能用。
    """
    root_abs = _root(root)
    # 1) 语义检索
    try:
        from vectorstore import query as vs_query, CODE_COLLECTION_NAME, count as vs_count
        if vs_count(CODE_COLLECTION_NAME) > 0:
            from embeddings import EmbeddingClient
            vec = EmbeddingClient().embed([query])[0]
            res = vs_query(vec, k=k, collection=CODE_COLLECTION_NAME)
            metas = (res.get("metadatas") or [[]])[0]
            files = []
            for m in metas:
                src = (m or {}).get("source") if isinstance(m, dict) else None
                if src:
                    files.append(_rel(root_abs, src))
            if files:
                return sorted(set(files))
    except Exception:  # noqa: BLE001
        pass
    # 2) 兜底：子串 grep
    hits = []
    for base, dirs, files in os.walk(root_abs):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "build", "dist", ".chroma", "__pycache__"}]
        for name in files:
            if os.path.splitext(name)[1].lower() not in {".py", ".js", ".ts", ".cs", ".json", ".yaml", ".yml"}:
                continue
            p = os.path.join(base, name)
            try:
                if query.lower() in open(p, encoding="utf-8", errors="ignore").read().lower():
                    hits.append(os.path.relpath(p, root))
            except OSError:
                pass
    return sorted(hits)

_CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".cs", ".java", ".go", ".rs",
              ".cpp", ".c", ".h", ".hpp", ".lua", ".gd", ".kt", ".swift", ".rb"}


def _extract_symbols(path):
    """从源码文件提取可测符号（函数/类/方法名）。Python 走 ast，其它语言走启发式正则。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    if ext == ".py":
        try:
            tree = _ast.parse(text)
        except SyntaxError:
            return []
        syms = []
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                syms.append(("function", node.name))
            elif isinstance(node, _ast.ClassDef):
                syms.append(("class", node.name))
        return syms
    found = []
    for m in re.finditer(
        r"\b(?:function|func|def|class|public|private|protected|static|interface|struct|enum)\s+([A-Za-z_]\w*)",
        text,
    ):
        found.append(("symbol", m.group(1)))
    seen, out = set(), []
    for kind, name in found:
        if name not in seen:
            seen.add(name)
            out.append((kind, name))
    return out


def _read_contract_fields(region_abs):
    """读取分区内契约/模式文件（*.schema.json / manifest.json / contract.json），提取对外接口字段。"""
    fields = []
    names = os.listdir(region_abs) if os.path.isdir(region_abs) else []
    for name in names:
        low = name.lower()
        if not (low.endswith(".schema.json") or low in ("manifest.json", "contract.json")):
            continue
        p = os.path.join(region_abs, name)
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            props = data.get("properties") or data.get("fields") or {}
            if isinstance(props, dict):
                fields.extend(sorted(props.keys()))
    return sorted(set(fields))


def generate_test_scene(root, name, region="behaviors"):
    """基于分区真实代码与契约生成测试场景：扫描公共符号 + 契约字段，并产出可运行测试骨架。

    产出（返回相对路径列表）：
      <region>/tests/<name>.json      场景描述（步骤引用真实符号/契约字段，而非死模板）
      <region>/tests/test_<name>.py   可运行测试骨架（仅当分区含 .py 时生成，可被 playtest 的 unittest 发现）
    """
    region_abs = os.path.join(_root(root), region)
    tests_dir = os.path.join(region_abs, "tests")
    os.makedirs(tests_dir, exist_ok=True)
    json_rel = os.path.join(region, "tests", (name if name.endswith(".json") else name + ".json"))
    json_path = _file(root, json_rel)
    if os.path.exists(json_path):
        raise ValueError("test scene already exists")

    # 1) 收集分区内的代码文件与符号（跳过 tests/ 自身）
    code_files, targets, has_py = [], [], False
    for base, dirs, files in os.walk(region_abs):
        if os.path.basename(base) == "tests":
            continue
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _CODE_EXTS:
                continue
            p = os.path.join(base, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            code_files.append(rel)
            if ext == ".py":
                has_py = True
            for kind, sym in _extract_symbols(p):
                targets.append({"file": rel, "symbol": sym, "kind": kind})

    # 2) 读取契约字段
    contract_fields = _read_contract_fields(region_abs)

    # 3) 写场景 JSON（内容引用真实符号/字段）
    scene = {
        "name": name,
        "region": region,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "language_hint": "python" if has_py else "mixed",
        "code_files": code_files,
        "targets": targets,
        "contract_fields": contract_fields,
        "steps": (
            [{"action": "load_module", "module": t["file"]} for t in targets[:5]]
            + [{"action": "assert_symbol_exists", "target": t["symbol"], "file": t["file"]} for t in targets[:8]]
            + ([{"action": "assert_contract_fields", "fields": contract_fields}] if contract_fields else [])
        ),
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(scene, f, ensure_ascii=False, indent=2)

    created = [json_rel.replace("\\", "/")]

    # 4) 仅当含 .py 时，额外生成可运行测试骨架（与已接上的 playtest / unittest 衔接）
    if has_py:
        py_name = "test_" + (name[:-5] if name.endswith(".json") else name) + ".py"
        py_rel = os.path.join(region, "tests", py_name)
        py_path = _file(root, py_rel)
        if not os.path.exists(py_path):
            files_syms = {}
            for t in targets:
                files_syms.setdefault(t["file"], []).append(t["symbol"])
            file_entries = ",\n".join(
                f"        ({r!r}, {syms!r})" for r, syms in files_syms.items()
            ) or "        # 未检出可测符号"
            py_src = (
                "# 由 DocMind generate_test_scene 自动生成（基于真实代码符号）\n"
                "import importlib.util\nimport os\nimport sys\nimport unittest\n\n"
                "ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
                "# (相对 code_root 的文件路径, 该文件内期望存在的符号列表)\n"
                "FILES = [\n" + file_entries + "\n]\n\n\n"
                "def _load(rel_path):\n"
                "    abs_path = os.path.join(ROOT, rel_path)\n"
                "    d = os.path.dirname(abs_path)\n"
                "    if d not in sys.path:\n"
                "        sys.path.insert(0, d)\n"
                "    if ROOT not in sys.path:\n"
                "        sys.path.insert(0, ROOT)\n"
                "    spec = importlib.util.spec_from_file_location(\n"
                "        \"dm_gen_test_\" + rel_path.replace(os.sep, \"_\").replace(\".\", \"_\"),\n"
                "        abs_path,\n"
                "    )\n"
                "    mod = importlib.util.module_from_spec(spec)\n"
                "    spec.loader.exec_module(mod)\n"
                "    return mod\n\n\n"
                "class TestGenerated(unittest.TestCase):\n"
                "    def test_symbols_present(self):\n"
                "        \"\"\"断言各待测文件确实导出了期望的符号（基于 AST 扫描结果）。\"\"\"\n"
                "        for rel_path, symbols in FILES:\n"
                "            with self.subTest(file=rel_path):\n"
                "                try:\n"
                "                    mod = _load(rel_path)\n"
                "                except Exception as e:  # noqa: BLE001\n"
                "                    self.fail(f\"加载模块失败 {rel_path}: {e}\")\n"
                "                for sym in symbols:\n"
                "                    self.assertTrue(\n"
                "                        hasattr(mod, sym), f\"{rel_path} 缺少符号 {sym}\"\n"
                "                    )\n\n\n"
                "if __name__ == \"__main__\":\n"
                "    unittest.main()\n"
            )
            with open(py_path, "w", encoding="utf-8") as f:
                f.write(py_src)
            created.append(py_rel.replace("\\", "/"))

    return created

_AUTO_TOKENS = {"", "auto", "test", "tests", "pytest", "unittest"}


def _detect_test_runner(root_abs):
    """返回可用的测试运行命令（优先 pytest，否则 unittest discover）。无则 None。

    打包版（frozen）内没有独立 Python 解释器（sys.executable 是 DocMind.exe，
    onedir 不含 python.exe），`-m pytest/unittest` 只会再启动一次本程序而不是跑测试，
    因此 frozen 下直接返回 None，由调用方给出明确提示，避免挂起/弹浏览器/假绿灯。
    """
    if getattr(sys, "frozen", False):
        return None
    try:
        probe = subprocess.run([sys.executable, "-m", "pytest", "--version"],
                               cwd=root_abs, capture_output=True, text=True, timeout=20)
        if probe.returncode == 0:
            return f'"{sys.executable}" -m pytest -q'
    except Exception:  # noqa: BLE001
        pass
    # unittest 兜底：tests/ 子包优先，否则从根发现
    if os.path.isdir(os.path.join(root_abs, "tests")):
        return f'"{sys.executable}" -m unittest discover -s tests -t . -p "test_*.py"'
    return f'"{sys.executable}" -m unittest discover -s . -p "test_*.py"'


def _parse_test_summary(out):
    """从 pytest/unittest 输出里抓取通过/失败计数，便于工作台展示。"""
    import re as _re
    s = {}
    m = _re.search(r"(\d+)\s+passed", out)
    if m:
        s["passed"] = int(m.group(1))
    m = _re.search(r"(\d+)\s+failed", out)
    if m:
        s["failed"] = int(m.group(1))
    m = _re.search(r"Ran\s+(\d+)\s+tests?", out)
    if m:
        s["ran"] = int(m.group(1))
    return s


def playtest(root, command, timeout=30):
    root_abs = _root(root)
    cmd = (command or "").strip()
    auto = cmd.lower() in _AUTO_TOKENS
    if auto:
        runner = _detect_test_runner(root_abs)
        if not runner:
            if getattr(sys, "frozen", False):
                return {"ok": False, "error": "分发版（DocMind.exe）内未内置 Python 解释器，无法在应用内运行 pytest/unittest；请在源码环境（.venv）中执行测试。"}
            return {"ok": False, "error": "未检测到测试框架（需 pytest 或 unittest 测试文件）。"}
        cmd = runner
    else:
        # 自定义命令复用 run_command 的结构化黑名单（首词集合 + 危险模式），
        # 避免本模块旧的几条子串规则被双空格/变形轻易绕过。
        try:
            from tools import _cmd_is_blocked
            blocked, why = _cmd_is_blocked(cmd)
        except Exception:  # noqa: BLE001
            blocked, why = False, ""
        if blocked:
            return {"ok": False, "error": f"命令被 Playtest 安全策略拦截（命中「{why}」）"}
    started = time.time()
    try:
        p = subprocess.run(cmd, shell=True, cwd=root_abs, capture_output=True, text=True, timeout=min(int(timeout), 120))
        out = (p.stdout + p.stderr)[-4000:]
        return {"ok": p.returncode == 0, "code": p.returncode,
                "duration": round(time.time() - started, 2), "output": out, **_parse_test_summary(out)}
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "error": "timeout", "output": str(e)}


def performance_sample(root, command):
    root_abs = _root(root)
    cmd = (command or "").strip()
    # 对 python 脚本做真实剖析（cProfile），其余仅测墙钟。
    # frozen 下 sys.executable 是 DocMind.exe 而非 python，无法 cProfile，
    # 直接返回明确错误，避免再启动一个应用实例（弹浏览器/挂起 120s）。
    is_python_cmd = cmd and (cmd.lower().endswith(".py") or cmd.lower().startswith("python "))
    if is_python_cmd and getattr(sys, "frozen", False):
        return {"ok": False, "metric": "cProfile(top15 by cumulative)",
                "error": "分发版（DocMind.exe）未内置 Python 解释器，cProfile 剖析仅源码环境可用；可改为在源码环境运行。"}
    if is_python_cmd:
        prof = f'"{sys.executable}" -m cProfile -s cumulative {cmd}'
        started = time.time()
        try:
            p = subprocess.run(prof, shell=True, cwd=root_abs, capture_output=True, text=True, timeout=120)
            out = (p.stdout + p.stderr)
            top = "\n".join(out.splitlines()[-15:])
            return {"ok": p.returncode == 0, "metric": "cProfile(top15 by cumulative)",
                    "duration": round(time.time() - started, 2), "output": top[-4000:]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    r = playtest(root, cmd, 30)
    r["metric"] = "wall_time_seconds"
    return r

# ---------------------------------------------------------------------------
# 审批门禁（approval gate）
# 把「审批」从“只记一条日志”升级为真正的服务端门禁：敏感操作（提交/回滚/应用分区方案）
# 执行前必须存在一条「在有效期内、action+target 匹配」的 approved 记录，否则被拦截。
# 这样既约束 API 端点，也约束 Agent 的 dev_* 工具（它们最终都走 regions.py 底层函数）。
# ---------------------------------------------------------------------------
APPROVAL_TTL_SECONDS = 1800  # 审批有效期 30 分钟


def approval_ledger_path(root):
    return _file(root, ".docmind_approvals.jsonl")


def approval(root, action, user, approved=False, target=""):
    """记录一条审批（追加到 .docmind_approvals.jsonl）。target 用于把审批绑定到具体对象。"""
    path = approval_ledger_path(root)
    row = {
        "action": action,
        "target": (target or "").strip(),
        "user": user,
        "approved": bool(approved),
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def is_approved(root, action, target, ttl=APPROVAL_TTL_SECONDS):
    """是否存在一条对 (action, target) 有效的 approved 记录（默认 30 分钟内）。"""
    path = approval_ledger_path(root)
    if not os.path.isfile(path):
        return False
    target = (target or "").strip()
    now = time.time()
    try:
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except Exception:  # noqa: BLE001
        return False
    for r in rows:
        if not r.get("approved"):
            continue
        if r.get("action") != action:
            continue
        if (r.get("target") or "").strip() != target:
            continue
        ts = r.get("time")
        if not ts:
            return True  # 无时间戳的旧记录视为永久有效（兼容历史数据）
        try:
            then = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return True  # 时间解析失败也放行，避免历史记录把操作永久卡死
        if now - then <= ttl:
            return True
    return False


def approval_status(root, action, target):
    """查询某操作当前是否已通过审批（供前端/Agent 判断是否需先审批）。"""
    return {
        "action": action,
        "target": (target or "").strip(),
        "approved": is_approved(root, action, target),
        "ttl_seconds": APPROVAL_TTL_SECONDS,
    }


def require_approval(root, action, target):
    """门禁检查：已审批返回 None；未审批返回阻断结构，由敏感操作原样向上返回。

    阻断结构含 blocked / approval_required / action / target / message，
    让 API 端点与 Agent 都能明确识别「需要先审批」。
    """
    if is_approved(root, action, target):
        return None
    return {
        "blocked": True,
        "approval_required": True,
        "action": action,
        "target": (target or "").strip(),
        "message": (
            f"操作 {action}(target={target or '*'}) 需要先审批。"
            f"请先调用审批（action={action}, target={target or '*'}, approved=true），"
            f"审批通过后 {APPROVAL_TTL_SECONDS // 60} 分钟内该操作放行。"
        ),
    }
