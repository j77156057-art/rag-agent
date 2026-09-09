"""文档摄取：加载(PDF/Markdown/TXT) -> 切分 -> 向量化 -> 入库。

另含「代码模式」摄取：把源代码/配置文件按文件 + 函数/类切成带符号名的片段，
存入独立的代码集合，使 Agent 能检索/阅读/搜索你的工程代码。
"""
import ast
import os
import re
import uuid

from config import CHUNK_SIZE, CHUNK_OVERLAP, CODE_COLLECTION_NAME, CODE_CHUNK
from embeddings import EmbeddingClient
from vectorstore import add_documents


def load_text(path):
    """按扩展名加载为纯文本。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if ext in (".md", ".markdown", ".txt"):
        with open(path, encoding="utf-8") as f:
            return f.read()
    raise ValueError(f"不支持的格式: {ext}")


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """优先按段落切分，超长段落再按字符长度兜底切分。"""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) <= size:
            cur += p + "\n"
        else:
            if cur.strip():
                chunks.append(cur.strip())
            if len(p) > size:
                step = max(1, size - overlap)
                for i in range(0, len(p), step):
                    piece = p[i : i + size].strip()
                    if piece:
                        chunks.append(piece)
                cur = ""
            else:
                cur = p + "\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def ingest_file(path, emb=None, size=None, overlap=None):
    """摄取单个文件，返回切片数量。"""
    emb = emb or EmbeddingClient()
    size = size or CHUNK_SIZE
    overlap = overlap or CHUNK_OVERLAP

    text = load_text(path)
    chunks = chunk_text(text, size, overlap)
    embeddings = emb.embed(chunks)
    metas = [{"source": os.path.basename(path), "chunk": i} for i in range(len(chunks))]
    ids = [uuid.uuid4().hex for _ in chunks]
    add_documents(chunks, embeddings, metas, ids)
    return len(chunks)


def ingest_directory(directory):
    """批量摄取目录下的所有支持文件。"""
    total = 0
    for name in sorted(os.listdir(directory)):
        p = os.path.join(directory, name)
        if os.path.isfile(p) and name.lower().endswith((".pdf", ".md", ".markdown", ".txt")):
            try:
                total += ingest_file(p)
            except Exception as e:  # 单个文件失败不影响其他
                print(f"[ingest] 跳过 {name}: {e}")
    return total


# ---------------------------------------------------------------------------
# 代码模式摄取：把源代码/配置文件切成带「符号名」的片段，存入独立代码集合。
# 设计要点：
#   - 散文分块按段落即可，但代码必须按「函数/类」切，否则一个函数被腰斩、
#     两个不相关的函数被粘成一段，检索质量会崩。
#   - Python 用 ast 精确拿到每个 def/class（含方法）的起止行与名字；
#     其它语言用正则启发式识别定义行，按定义切分。
#   - 切片过大时再按行兜底切，保证单条不超 CODE_CHUNK。
# ---------------------------------------------------------------------------

# 纳入代码库索引的扩展名（含 .json/.yaml/.toml 等配置文件，它们是「数据驱动」的核心）
_CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".cs", ".java", ".cpp", ".cc", ".c",
    ".h", ".hpp", ".go", ".rs", ".lua", ".rb", ".php", ".swift", ".kt", ".scala",
    ".sh", ".json", ".yaml", ".yml", ".toml",
}
# 遍历时跳过的目录（依赖/构建产物/版本控制/虚拟环境）
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist",
    "bin", "obj", "Library", "Temp", ".idea", ".vscode", "target", "out",
    ".mypy_cache", ".ruff_cache",
}
# 单个代码文件超过此体积（字节）则跳过，避免把巨型生成文件/压缩包塞进索引
_MAX_CODE_FILE = 500_000

# 扩展名 -> 语言标记（用于每块 metadata 的 lang 字段，便于检索排序与展示）
_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".cs": "csharp", ".java": "java", ".cpp": "cpp",
    ".cc": "cpp", ".c": "c", ".h": "c", ".hpp": "cpp", ".go": "go", ".rs": "rust",
    ".lua": "lua", ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".scala": "scala", ".sh": "shell", ".json": "json", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml",
}

# 顶层/类型定义行的启发式前缀（用于非 Python 语言的切分）
_DEF_RE = re.compile(
    r"""^\s*(?:export\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+"""
    r"""|async\s+|final\s+|virtual\s+|override\s+|inline\s+|const\s+|def\s+"""
    r"""|func(?:tion)?\s+|fn\s+|function\s+|class\s+|struct\s+|interface\s+"""
    r"""|impl\s+|module\s+|type\s+)""",
    re.X,
)


def _symbol_name(line, ext):
    """从一行定义里尽量抽出符号名（函数/类/方法/常量）。"""
    m = re.search(r"(?:def|func(?:tion)?|fn|function)\s+([A-Za-z_]\w*)", line)
    if m:
        return m.group(1)
    m = re.search(r"(?:class|struct|interface|impl|module|type)\s+([A-Za-z_]\w*)", line)
    if m:
        return m.group(1)
    m = re.search(
        r"(?:public|private|protected|internal|static|virtual|override|final|async|inline)\s+"
        r"[\w<>\[\],\s\.\?]+\s+([A-Za-z_]\w*)\s*\(",
        line,
    )
    if m:
        return m.group(1)
    m = re.search(r"const\s+([A-Za-z_]\w*)\s*=", line)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z_]\w*)\s*:\s*(?:\(|async\s*\()", line)  # TS 方法简写
    if m:
        return m.group(1)
    return ""


def _extract_imports(text, ext):
    """抽取该文件 import 的顶层模块名列表（跨文件依赖追踪用，最多取前 20 个）。

    用于给每个切片附加 imports 元数据，回答"这个符号依赖哪些模块 / 从哪来"。
    Python 走 ast 精确提取；其它语言用通用正则兜底（import/from/using/require/include）。
    """
    mods = []
    if ext == ".py":
        try:
            tree = ast.parse(text)
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        mods.append(a.name.split(".")[0])
                elif isinstance(n, ast.ImportFrom):
                    if n.module:
                        mods.append(n.module.split(".")[0])
        except SyntaxError:
            pass
    else:
        # 通用正则：捕获 import/using/require 后的模块名（跳过关键字本身）
        pat = re.compile(r"(?:import|from|using|require)\s*['\"]?([\w./\-]+)", re.I)
        for m in pat.finditer(text):
            nm = m.group(1).split(".")[0].split("/")[-1].split("\\")[-1]
            if nm and nm.lower() not in ("from",):
                mods.append(nm)
    seen, out = set(), []
    for m in mods:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out[:20]


def _split_by_chars(text, size):
    """极长行（无空行无换行可切）按字符硬切，保证单块不超 size。"""
    return [text[i:i + size] for i in range(0, len(text), size)] or [text]


def _split_big(text, size):
    """把过长文本切成若干 ≤ size 的片段（递归再切，保持片段尽量内聚）。

    策略：优先在空行（自然边界）处断开；若一段仍超 size 则对这段递归；
    没有任何空行可借力时退化为按行切；单行超长再按字符硬切。
    注意：符号名由调用方在切分后逐片附加，因此无论怎么切都不会丢符号。
    """
    if len(text) <= size:
        return [text]
    # 1) 优先按空行（自然段落边界）切
    parts = [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
    if len(parts) > 1:
        chunks, buf = [], ""
        for p in parts:
            if len(buf) + len(p) + 2 <= size:
                buf = (buf + "\n\n" + p) if buf else p
            else:
                if buf:
                    chunks.extend(_split_big(buf, size))
                buf = p
        if buf:
            chunks.extend(_split_big(buf, size))
        return [c for c in chunks if c.strip()] or [text]
    # 2) 无空行：按行切；若某行单独超长则按字符硬切
    lines = text.split("\n")
    chunks, buf = [], ""
    for ln in lines:
        if len(buf) + len(ln) + 1 <= size:
            buf = (buf + "\n" + ln) if buf else ln
        else:
            if buf:
                chunks.append(buf)
            if len(ln) > size:
                chunks.extend(_split_by_chars(ln, size))
                buf = ""
            else:
                buf = ln
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()] or [text]


def _chunk_python(text):
    """用 ast 精确切分 Python 文件为（片段, 符号名）列表；失败返回 None。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    lines = text.split("\n")
    nodes = []
    for node in tree.body:
        nodes.append(node)
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nodes.append(child)
    nodes.sort(key=lambda n: n.lineno)
    chunks = []
    # 文件头（imports / 模块 docstring / 注释）作为一段 preamble
    if nodes:
        pre = "\n".join(lines[: nodes[0].lineno - 1]).strip()
        if pre:
            chunks.append((pre, ""))
    for n in nodes:
        start = n.lineno - 1
        end = getattr(n, "end_lineno", n.lineno)
        block = "\n".join(lines[start:end]).strip()
        sym = n.name if hasattr(n, "name") else ""
        for piece in _split_big(block, CODE_CHUNK):
            chunks.append((piece, sym))
    return chunks or None


def chunk_code(text, path):
    """把一段源代码切成（片段, 符号名）列表；优先用 ast（Python），否则正则启发式。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        py = _chunk_python(text)
        if py is not None:
            return py
    lines = text.split("\n")
    idxs = [i for i, ln in enumerate(lines) if _DEF_RE.match(ln)]
    chunks = []
    if idxs and idxs[0] > 0:
        pre = "\n".join(lines[: idxs[0]]).strip()
        if pre:
            chunks.append((pre, ""))
    for k, s in enumerate(idxs):
        e = idxs[k + 1] if k + 1 < len(idxs) else len(lines)
        block = "\n".join(lines[s:e]).strip()
        sym = _symbol_name(lines[s], ext)
        for piece in _split_big(block, CODE_CHUNK):
            chunks.append((piece, sym))
    if not chunks:
        for piece in _split_big(text, CODE_CHUNK):
            chunks.append((piece, ""))
    return chunks


def load_code_file(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def _code_file_records(path, root, emb):
    """构建单个代码文件的 (chunks, embeddings, metas, ids)；无内容返回 None。"""
    text = load_code_file(path)
    chunks = chunk_code(text, path)
    if not chunks:
        return None
    rel = os.path.relpath(path, root).replace("\\", "/")
    ext = os.path.splitext(path)[1].lower()
    lang = _LANG_BY_EXT.get(ext, "text")
    imports_str = ", ".join(_extract_imports(text, ext)) or "none"
    texts = [c for c, _ in chunks]
    embeddings = emb.embed(texts)
    # 每块带语言标记与 import 边，便于检索排序、展示与"符号从哪来"追踪
    metas = [
        {"source": rel, "symbol": sym, "kind": "code", "lang": lang, "imports": imports_str}
        for _, sym in chunks
    ]
    ids = [uuid.uuid4().hex for _ in chunks]
    return texts, embeddings, metas, ids


def ingest_code_file(path, root, emb=None, collection=CODE_COLLECTION_NAME):
    """摄取单个代码文件，返回切片数量。"""
    emb = emb or EmbeddingClient()
    rec = _code_file_records(path, root, emb)
    if not rec:
        return 0
    texts, embeddings, metas, ids = rec
    add_documents(texts, embeddings, metas, ids, collection=collection)
    return len(texts)


# 目录级索引的「单次写入」批次上限：把切片累积到一定量后一次性 add。
# chroma 1.x 每次 add 都会重写索引，若按「每文件一次 add」做大量增量写入会极慢甚至卡死；
# 累积成较大的批次再 add 可避免该问题，同时限制大工程的内存占用。
_CODE_ADD_BATCH = 2000


def ingest_code_directory(root, emb=None, collection=CODE_COLLECTION_NAME):
    """遍历代码根目录，索引所有受支持的文件；返回总切片数。

    所有切片先在内存中累积，按 _CODE_ADD_BATCH 批量写入向量库（而非每文件一次 add）。
    """
    emb = emb or EmbeddingClient()
    root = os.path.abspath(root)
    total = 0
    texts_buf, embs_buf, metas_buf, ids_buf = [], [], [], []

    def _flush():
        nonlocal texts_buf, embs_buf, metas_buf, ids_buf
        if texts_buf:
            add_documents(texts_buf, embs_buf, metas_buf, ids_buf, collection=collection)
        texts_buf, embs_buf, metas_buf, ids_buf = [], [], [], []

    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        for fn in sorted(fns):
            if os.path.splitext(fn)[1].lower() not in _CODE_EXT:
                continue
            fp = os.path.join(dp, fn)
            try:
                if os.path.getsize(fp) > _MAX_CODE_FILE:
                    continue
                rec = _code_file_records(fp, root, emb)
                if rec:
                    texts, embeddings, metas, ids = rec
                    texts_buf.extend(texts)
                    embs_buf.extend(embeddings)
                    metas_buf.extend(metas)
                    ids_buf.extend(ids)
                    total += len(texts)
                    if len(texts_buf) >= _CODE_ADD_BATCH:
                        _flush()
            except Exception as e:  # 单个文件失败不影响其他
                print(f"[ingest_code] 跳过 {fn}: {e}")
    _flush()
    return total


def load_project_rules(root):
    """读取代码根目录下的 DOCMIND_RULES.md 作为「项目级规则」；不存在返回空串。

    规则由 /api/ingest_code 索引时读入并注入 Agent 的系统消息，使不同项目的
    分区约定 / 修改约束能随项目自动生效，无需改代码。
    """
    path = os.path.join(root, "DOCMIND_RULES.md")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except Exception:
        return ""
