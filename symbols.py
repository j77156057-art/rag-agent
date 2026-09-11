"""代码符号提取：文件大纲、全局符号语义地图、符号级切片共用一份行级符号表。

零第三方依赖：
  - Python 走标准库 ast，行号/签名/文档串精确；
  - GDScript 是缩进语法、定义形式规整，用行级状态解析即可可靠提取
    （func/class_name/extends/inner class/signal/enum/const/var/@export/@export_group）；
  - Godot 文本场景/资源（.tscn/.tres）抽 node/resource 段；
  - 其它语言用正则 + 括号配平兜底。

符号统一为 dict：
  name, kind, start, end（均 1 基含端点）, parent, signature, doc, detail
文件信封：lang, class_name, extends, doc, symbols（按行号排序）。

文件结果按 (mtime_ns, size) 缓存，长驻进程里反复打开/画地图不重复解析。
"""
import ast
import os
import re

# ---------------------------------------------------------------- 常量

# 结构化符号种类（前端按此上色/分组）
CLASS = "class"
FUNCTION = "function"
SIGNAL = "signal"
ENUM = "enum"
CONST = "const"
VAR = "var"
NODE = "node"
RESOURCE = "resource"
SECTION = "section"
GROUP = "group"

# 走「符号级精确切片」的扩展名；其余（.json/.tscn 等数据文件）仍用整文件切分
STRUCTURED_CODE_EXT = {".gd", ".py"}

# ---------------------------------------------------------------- 工具

def _indent_of(line):
    s = line.lstrip(" \t")
    return len(line) - len(s)


def _strip_hash_comment(line):
    """去掉 GDScript/Python 风格行内 # 注释，字符串内的 # 保留（状态机感知引号）。"""
    out = []
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in ('"', "'"):
            quote = c
            triple = line[i:i + 3] == quote * 3
            if triple:
                out.append(line[i:i + 3])
                i += 3
                while i < n and line[i:i + 3] != quote * 3:
                    out.append(line[i])
                    i += 1
                if i < n:
                    out.append(line[i:i + 3])
                    i += 3
                continue
            out.append(c)
            i += 1
            while i < n and line[i] != quote:
                if line[i] == "\\":
                    out.append(line[i])
                    i += 1
                    if i < n:
                        out.append(line[i])
                        i += 1
                    continue
                out.append(line[i])
                i += 1
            if i < n:
                out.append(line[i])
                i += 1
        elif c == "#":
            break
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _clean_comment(line):
    """`## 文档` / `# 注` -> 纯文本，保留缩进层级。"""
    s = line.strip()
    if s.startswith("##"):
        s = s[2:]
    elif s.startswith("#"):
        s = s[1:]
    return s.strip()


def _truncate(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _mk(name, kind, start, end, signature="", doc="", detail="", parent=""):
    return {
        "name": name,
        "kind": kind,
        "start": start,
        "end": max(end, start),
        "parent": parent,
        "signature": _truncate(signature, 300),
        "doc": _truncate(doc, 400),
        "detail": _truncate(detail, 200),
    }


# ---------------------------------------------------------------- GDScript

_GD_CLASS_NAME = re.compile(r"^class_name\s+(\w+)\s*(?:extends\s+(.+?))?\s*$")
_GD_EXTENDS = re.compile(r"^extends\s+(.+?)\s*$")
_GD_FUNC = re.compile(
    r"^(?:static\s+|remote\s+|master\s+|puppet\s+|remotesync\s+|mastersync\s+|puppetsync\s+)*"
    r"func\s+(\w+)\s*\((.*?)\)\s*(?:->\s*([^:]+?))?\s*:\s*(.*)$"
)
_GD_INNER_CLASS = re.compile(r"^class\s+(\w+)\s*(?:extends\s+([\w.]+))?\s*:\s*$")
_GD_SIGNAL = re.compile(r"^signal\s+(\w+)\s*(?:\((.*)\))?\s*$")
_GD_ENUM_START = re.compile(r"^enum\s+(\w+)?\s*(?:\{(.*))?$")
_GD_CONST = re.compile(r"^const\s+(\w+)\s*(?::\s*([^=]+))?\s*=\s*(.*)$")
_GD_VAR = re.compile(
    r"^(?:static\s+)?var\s+(\w+)\s*(?::\s*([^=]+?))?\s*(?:=(.*))?$"
)
_GD_ANNOTATION = re.compile(r"^@([\w.]+)(?:\((.*)\))?\s*$")
_GD_EXPORT_GROUP = re.compile(r"""^@export_(?:sub)?group\(\s*['"]([^'"]+)['"]""")

_GD_LEAF_KINDS = {CONST, VAR, SIGNAL, ENUM, GROUP}


def _strip_inline_annotations(code):
    """去掉行首内联注解（@export / @onready 等），返回剩余声明代码。"""
    prev = None
    while prev != code:
        prev = code
        code = re.sub(r"^@[\w.]+\s*(?:\([^)]*\))?\s*", "", code)
    return code


def _gd_doc_above(raw_lines, idx, indent, consumed):
    """收集声明上方连续注释（同缩进），## 文档注释优先；consumed 中的行不重复用。"""
    docs = []
    j = idx - 1
    while j >= 0 and j not in consumed:
        s = raw_lines[j].strip()
        if not s.startswith("#"):
            break
        if _indent_of(raw_lines[j]) not in (indent, indent + 1):
            break
        docs.append(_clean_comment(raw_lines[j]))
        j -= 1
    docs.reverse()
    text = "\n".join(d for d in docs if d)
    return _truncate(text, 400)


def _gd_block_end(raw_lines, code_lines, i, indent):
    """缩进块（func/inner class）结束行：返回 1 基含端点行号（裁掉尾随空行）。"""
    j = i + 1
    n = len(raw_lines)
    while j < n:
        s = code_lines[j].strip()
        if s and not s.startswith("#") and _indent_of(raw_lines[j]) <= indent:
            break
        j += 1
    # 裁掉块尾的空行（split("\n") 给文件尾 \n 造的空元素也算）
    while j > i + 1 and not code_lines[j - 1].strip():
        j -= 1
    return j  # j 是块外第一行（0 基），恰好等于块内最后一行的 1 基行号


def _gd_continue_end(code_lines, i):
    """单行声明因括号未闭合/反斜杠续行时，返回结束行（0 基，含）。"""
    depth = 0
    j = i
    n = len(code_lines)
    while j < n:
        for ch in code_lines[j]:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        cont = code_lines[j].rstrip().endswith("\\") or depth > 0
        if not cont:
            return j
        j += 1
    return n - 1


def _extract_gdscript(text):
    raw = text.split("\n")
    code = [_strip_hash_comment(ln) for ln in raw]
    n = len(raw)

    class_name, extends_ref, header_doc = "", "", ""
    symbols, consumed = [], set()

    # 头部信息：class_name / extends / 文件头 ## 文档
    first_decl = n
    for i in range(n):
        if _indent_of(raw[i]) != 0:
            continue
        s = code[i].strip()
        if not s:
            continue
        if s.startswith("@"):
            # 纯注解行（@tool/@icon）属头部；@export var 这类内联注解是首条声明
            if _GD_ANNOTATION.match(s):
                continue
            first_decl = i
            break
        m = _GD_CLASS_NAME.match(s)
        if m:
            class_name = m.group(1)
            if m.group(2):
                extends_ref = m.group(2).strip()
            continue
        m = _GD_EXTENDS.match(s)
        if m:
            extends_ref = m.group(1).strip()
            continue
        if s.startswith("#"):
            continue
        first_decl = i
        break

    # 头部文档归属（Godot 约定：## 紧贴声明上一行=该声明的文档）：
    # first_decl 正上方连续无空行的注释块归首条声明；头部区其余注释归文件/类。
    member_doc_lines = set()
    j = first_decl - 1
    while j >= 0 and raw[j].strip().startswith("#"):
        member_doc_lines.add(j)
        j -= 1

    header_docs = []
    for i in range(0, first_decl):
        if raw[i].strip().startswith("#") and i not in member_doc_lines:
            header_docs.append(_clean_comment(raw[i]))
            consumed.add(i)
    if header_docs:
        header_doc = _truncate("\n".join(d for d in header_docs if d), 400)

    def parse_region(lo, hi, parent, base_indent):
        i = lo
        while i < hi:
            line_raw, line_code = raw[i], code[i]
            ind = _indent_of(line_raw)
            s_code = line_code.strip()
            if ind != base_indent or not s_code or s_code.startswith("#"):
                i += 1
                continue

            # 独立注解行（@tool / @icon / @rpc / @export_group ...）
            ann = _GD_ANNOTATION.match(s_code)
            ann_start = i
            while ann and i < hi and _GD_ANNOTATION.match(code[i].strip()):
                gm = _GD_EXPORT_GROUP.match(code[i].strip())
                if gm:
                    symbols.append(_mk(gm.group(1), GROUP, i + 1, i + 1,
                                       signature=code[i].strip(), parent=parent))
                    consumed.add(i)
                i += 1
                consumed.add(i - 1)
                if i < hi:
                    ann = _GD_ANNOTATION.match(code[i].strip()) if _indent_of(raw[i]) == base_indent else None
                if i >= hi:
                    break
            if i >= hi:
                break
            if i != ann_start and (
                _indent_of(raw[i]) != base_indent or not code[i].strip()
            ):
                # 独立注解后没有跟声明（文件尾/空行/缩进出错），安全跳过
                i += 1
                continue
            line_raw, line_code = raw[i], code[i]
            ind = _indent_of(line_raw)
            if ind != base_indent:
                i += 1
                continue
            s_code = line_code.strip()
            if not s_code or s_code.startswith("#"):
                i += 1
                continue

            doc = _gd_doc_above(raw, ann_start, base_indent, consumed)
            start_line = ann_start + 1

            # inner class
            m = _GD_INNER_CLASS.match(s_code)
            if m:
                end = _gd_block_end(raw, code, i, ind)
                symbols.append(_mk(m.group(1), CLASS, start_line, end,
                                   signature=s_code, doc=doc, parent=parent,
                                   detail=m.group(2) or ""))
                body_indent = None
                for k in range(i + 1, end):
                    st = code[k].strip()
                    if st and not st.startswith("#"):
                        body_indent = _indent_of(raw[k])
                        break
                if body_indent is not None:
                    parse_region(i + 1, end, m.group(1), body_indent)
                i = end
                continue

            # func
            m = _GD_FUNC.match(s_code)
            if m:
                end = _gd_block_end(raw, code, i, ind)
                args = m.group(2).strip()
                ret = (m.group(3) or "").strip()
                detail = ("-> " + ret) if ret else ""
                symbols.append(_mk(m.group(1), FUNCTION, start_line, end,
                                   signature=s_code, doc=doc, detail=detail,
                                   parent=parent))
                i = end
                continue

            # signal
            m = _GD_SIGNAL.match(s_code)
            if m:
                symbols.append(_mk(m.group(1), SIGNAL, start_line, i + 1,
                                   signature=s_code, doc=doc,
                                   detail=(m.group(2) or "").strip(), parent=parent))
                i += 1
                continue

            # enum（可能跨多行）
            m = _GD_ENUM_START.match(s_code)
            if m:
                end_i = i
                if "{" not in s_code or s_code.count("{") > s_code.count("}"):
                    depth = s_code.count("{") - s_code.count("}")
                    j = i + 1
                    while j < hi and depth > 0:
                        depth += code[j].count("{") - code[j].count("}")
                        end_i = j
                        j += 1
                body = "\n".join(code[k] for k in range(i, end_i + 1))
                mb = re.search(r"\{(.*)\}", body, re.S)
                members = []
                if mb:
                    for part in mb.group(1).split(","):
                        nm = part.strip().split("=")[0].strip()
                        if nm:
                            members.append(nm)
                symbols.append(_mk(m.group(1) or "(anonymous)", ENUM, start_line,
                                   end_i + 1, signature=s_code, doc=doc,
                                   detail=", ".join(members), parent=parent))
                i = end_i + 1
                continue

            # const
            m = _GD_CONST.match(s_code)
            if m:
                end_i = _gd_continue_end(code, i)
                detail_parts = [p for p in ((m.group(2) or "").strip(), (m.group(3) or "").strip()) if p]
                symbols.append(_mk(m.group(1), CONST, start_line, end_i + 1,
                                   signature=s_code, doc=doc,
                                   detail=" = ".join(detail_parts)[:200], parent=parent))
                i = end_i + 1
                continue

            # var（去掉行首 @export/@onready 等内联注解再匹配）
            # := 推断写法归一化成占位类型，便于同一正则处理（var x := v → var x : ␣ = v）
            stripped_decl = _strip_inline_annotations(s_code).replace(":=", ": ␣ =", 1)
            m = _GD_VAR.match(stripped_decl)
            if m:
                end_i = _gd_continue_end(code, i)
                detail = (m.group(2) or "").strip()
                if detail == "␣":
                    detail = ""
                symbols.append(_mk(m.group(1), VAR, start_line, end_i + 1,
                                   signature=s_code, doc=doc, detail=detail,
                                   parent=parent))
                i = end_i + 1
                continue

            i += 1

    parse_region(0, n, "", 0)
    symbols.sort(key=lambda s: (s["start"], 0 if s["parent"] == "" else 1))

    return {
        "lang": "gdscript",
        "class_name": class_name,
        "extends": extends_ref,
        "doc": header_doc,
        "symbols": symbols,
    }


# ---------------------------------------------------------------- Python

def _py_signature(node):
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    head = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    ret = ""
    if node.returns:
        try:
            ret = " -> " + ast.unparse(node.returns)
        except Exception:
            pass
    return _truncate(f"{head}{node.name}({args}){ret}:", 300)


def _extract_python(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    symbols = []

    def doc_of(node):
        d = ast.get_docstring(node)
        return _truncate(d or "", 400)

    def start_of(node):
        if node.decorator_list:
            return min(d.lineno for d in node.decorator_list)
        return node.lineno

    def emit_func(node, parent=""):
        symbols.append(_mk(node.name, FUNCTION, start_of(node),
                           node.end_lineno or node.lineno,
                           signature=_py_signature(node), doc=doc_of(node),
                           parent=parent))

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    pass
            symbols.append(_mk(node.name, CLASS, start_of(node),
                               node.end_lineno or node.lineno,
                               signature=f"class {node.name}:", doc=doc_of(node),
                               detail=", ".join(bases)))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    emit_func(child, node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            emit_func(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    kind = CONST if t.id.isupper() else VAR
                    symbols.append(_mk(t.id, kind, node.lineno,
                                       node.end_lineno or node.lineno,
                                       parent=""))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            kind = CONST if node.target.id.isupper() else VAR
            symbols.append(_mk(node.target.id, kind, node.lineno,
                               node.end_lineno or node.lineno, parent=""))

    symbols.sort(key=lambda s: s["start"])
    return {
        "lang": "python",
        "class_name": "",
        "extends": "",
        "doc": _truncate(ast.get_docstring(tree) or "", 400),
        "symbols": symbols,
    }


# ---------------------------------------------------------------- Godot 场景/资源

_TSCN_NODE = re.compile(r'^\[node\s+name="([^"]*)"(?:\s+type="([^"]*)")?(?:\s+parent="([^"]*)")?')
_TSCN_EXT = re.compile(r'^\[ext_resource\s+(.*?)\]\s*$')
_TSCN_SUB = re.compile(r'^\[sub_resource\s+(.*?)\]\s*$')
_ATTR_TYPE = re.compile(r'type="([^"]*)"')
_ATTR_PATH = re.compile(r'path="([^"]*)"')
_ATTR_ID = re.compile(r'id="([^"]*)"')
_INI_SECTION = re.compile(r"^\[([^\]]+)\]$")


def _extract_godot_scene(text, lang):
    """抽取 .tscn 的 node 树与 .tres/.godot 的 resource/section 段。"""
    lines = text.split("\n")
    symbols = []
    section_start = None
    section_kind = None

    def flush(end_line):
        if section_start is not None:
            symbols[-1]["end"] = end_line

    for i, ln in enumerate(lines):
        s = ln.strip()
        m = _TSCN_NODE.match(s)
        if m:
            if symbols:
                symbols[-1]["end"] = i
            parent = m.group(3) or "."
            symbols.append(_mk(m.group(1), NODE, i + 1, i + 1,
                               signature=s, detail=(m.group(2) or ""),
                               parent="" if parent == "." else parent))
            continue
        m = _TSCN_EXT.match(s)
        if m:
            if symbols:
                symbols[-1]["end"] = i
            attrs = m.group(1)
            mt, mp = _ATTR_TYPE.search(attrs), _ATTR_PATH.search(attrs)
            typ = mt.group(1) if mt else ""
            path = mp.group(1) if mp else ""
            symbols.append(_mk(os.path.basename(path) or "(external)", RESOURCE,
                               i + 1, i + 1, signature=s, detail=typ))
            continue
        m = _TSCN_SUB.match(s)
        if m:
            if symbols:
                symbols[-1]["end"] = i
            attrs = m.group(1)
            mt, mi = _ATTR_TYPE.search(attrs), _ATTR_ID.search(attrs)
            typ = mt.group(1) if mt else ""
            sid = mi.group(1) if mi else "(sub)"
            symbols.append(_mk(sid, RESOURCE, i + 1, i + 1, signature=s, detail=typ))
            continue
        m = _INI_SECTION.match(s)
        if m and lang != "godot-scene":
            if symbols:
                symbols[-1]["end"] = i
            symbols.append(_mk(m.group(1), SECTION, i + 1, i + 1, signature=s))
    if symbols:
        symbols[-1]["end"] = len(lines)
    return {
        "lang": lang,
        "class_name": "",
        "extends": "",
        "doc": "",
        "symbols": symbols,
    }


# ---------------------------------------------------------------- 通用兜底

_GENERIC_PATTERNS = [
    (re.compile(r"\b(?:function|func(?:tion)?|fn|def)\s+(\w+(?:[.:]\w+)*)"), FUNCTION),
    (re.compile(r"\b(?:class|struct|interface|trait|impl|module|namespace)\s+([A-Za-z_]\w*)"), CLASS),
    (re.compile(r"\benum\s+(\w+)"), ENUM),
    (re.compile(r"\b(?:pub\s+)?const\s+(\w+)"), CONST),
    (re.compile(r"\b(?:var|let|val|uniform|varying)\s+(\w+)"), VAR),
]


def _extract_generic(text, lang):
    lines = text.split("\n")
    symbols = []
    in_block_comment = False
    for i, ln in enumerate(lines):
        code = ln
        if "/*" in code:
            code = re.sub(r"/\*.*?\*/", "", code)
            if "/*" in code and "*/" not in code.split("/*", 1)[1]:
                in_block_comment = True
                code = code.split("/*")[0]
        elif in_block_comment:
            if "*/" in code:
                in_block_comment = False
                code = code.split("*/", 1)[1]
            else:
                continue
        code = re.sub(r"//.*$", "", code)
        s = code.strip()
        if not s:
            continue
        for pat, kind in _GENERIC_PATTERNS:
            m = pat.search(s)
            if m:
                # Lua 形态 function M.foo() / A:bar() 取末段符号名
                rawname = re.split(r"[.:]", m.group(1))[-1]
                end = i + 1
                # 大括号语言：从声明行配平到闭合括号（单行声明立即闭合）
                if "{" in code and kind in (FUNCTION, CLASS, ENUM):
                    depth = 0
                    for j in range(i, min(len(lines), i + 2000)):
                        depth += lines[j].count("{") - lines[j].count("}")
                        if depth <= 0:
                            end = j + 1
                            break
                elif lang == "lua":
                    # Lua: function ... end
                    for j in range(i, min(len(lines), i + 400)):
                        if re.search(r"(^|\s)end(\s|$|;)", lines[j]):
                            end = j + 1
                            break
                symbols.append(_mk(rawname, kind, i + 1, end,
                                   signature=s[:300]))
                break
    symbols.sort(key=lambda s: s["start"])
    return {"lang": lang, "class_name": "", "extends": "", "doc": "", "symbols": symbols}


# ---------------------------------------------------------------- 分发 + 缓存

_LANG_BY_EXT = {
    ".gd": "gdscript", ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".cs": "csharp", ".java": "java",
    ".cpp": "cpp", ".cc": "cpp", ".c": "c", ".h": "c", ".hpp": "cpp",
    ".go": "go", ".rs": "rust", ".lua": "lua", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala", ".sh": "shell",
    ".gdshader": "gdshader", ".tscn": "godot-scene",
    ".tres": "godot-resource", ".godot": "ini",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
}

# 不做行级大纲的数据文件（地图视图仍展示文件名，不展开符号）
_DATA_EXT = {".json", ".yaml", ".yml", ".toml", ".godot"}

_cache = {}


def extract(text, path):
    """按扩展名解析文本，返回符号信封；解析器异常时退回空信封，绝不阻断调用方。"""
    ext = os.path.splitext(path)[1].lower()
    lang = _LANG_BY_EXT.get(ext, "text")
    empty = {"lang": lang, "class_name": "", "extends": "", "doc": "", "symbols": []}
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        if ext == ".gd":
            return _extract_gdscript(text)
        if ext == ".py":
            return _extract_python(text) or empty
        if ext == ".tscn":
            return _extract_godot_scene(text, "godot-scene")
        if ext == ".tres":
            return _extract_godot_scene(text, "godot-resource")
        if ext in _DATA_EXT:
            return empty
        return _extract_generic(text, lang)
    except Exception as e:  # 解析器 bug 不应影响大纲/索引主流程
        print(f"[symbols] 解析失败 {path}: {e}")
        return empty


def file_symbols(abs_path):
    """读盘 + (mtime_ns, size) 缓存。"""
    try:
        st = os.stat(abs_path)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(abs_path)), st.st_mtime_ns, st.st_size)
    cached = _cache.get(key[0])
    if cached and cached[0] == key:
        return cached[1]
    try:
        # utf-8-sig：Godot 编辑器默认写 UTF-8 BOM，不带 BOM 的文件同样可读
        with open(abs_path, encoding="utf-8-sig", errors="ignore") as f:
            text = f.read()
    except OSError:
        return None
    if text.startswith("\ufeff"):
        text = text[1:]
    env = extract(text, abs_path)
    _cache[key[0]] = (key, env)
    return env
