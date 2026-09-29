"""tree-sitter symbol lookup for TypeScript / JavaScript / Vue single files.

Why this exists: `code_intel` had one AST-precise path (Python) and everything
else fell back to a word-boundary regex, so for the most-edited files in this
repo (`.vue`, `.ts`) a "reference" could just as well be a comment, a log
string or an unrelated object key. tree-sitter gives us node kinds, which is
the same precision Python already had.

Everything degrades: if the grammar is missing, callers get ``None`` and fall
back to the regex path instead of losing results entirely.
"""
from __future__ import annotations

import re
from typing import Any

TS_EXTS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue"})

#: declarations whose `name` field is the symbol being defined
_DEF_PARENTS = frozenset({
    "function_declaration", "generator_function_declaration", "function_signature",
    "class_declaration", "abstract_class_declaration", "interface_declaration",
    "enum_declaration", "type_alias_declaration", "module", "internal_module",
    "method_definition", "variable_declarator", "field_definition",
    "property_signature", "public_field_definition", "abstract_method_signature",
})
#: name-fields that still count as a *use* of the symbol, not its definition
_BINDING_PARENTS = frozenset({"import_specifier", "export_specifier"})

_SCRIPT_OPEN = re.compile(r"<script([^>]*)>", re.I)
_SCRIPT_CLOSE = re.compile(r"</script>", re.I)
_TEMPLATE_OPEN = re.compile(r"<template[^>]*>", re.I)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

_PARSERS: dict[str, Any] = {}
_IMPORT_ERROR = ""


def available() -> bool:
    """Whether tree-sitter plus the TS/JS grammars are importable."""
    try:
        _load("typescript")
    except Exception as exc:  # noqa: BLE001
        global _IMPORT_ERROR
        _IMPORT_ERROR = str(exc)
        return False
    return True


def _grammar_entry(module, *candidates):
    """Grammar 包的导出名不一致：tree-sitter-typescript 用 language_typescript()，
    tree-sitter-javascript 0.25 只导出 language()。按候选名找第一个可调用项。"""
    for name in candidates:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn()
    raise ImportError(f"{module.__name__} 缺少语法入口，候选名：{', '.join(candidates)}")


def _load(flavor: str):
    """Build (and cache) a parser for one grammar flavor."""
    if flavor in _PARSERS:
        return _PARSERS[flavor]
    from tree_sitter import Language, Parser  # noqa: WPS433 (lazy by design)
    if flavor in ("typescript", "tsx"):
        import tree_sitter_typescript as grammar  # noqa: WPS433
        raw = _grammar_entry(grammar, "language_tsx" if flavor == "tsx" else "language_typescript",
                             "language")
    elif flavor == "javascript":
        import tree_sitter_javascript as grammar  # noqa: WPS433
        raw = _grammar_entry(grammar, "language_javascript", "language")
    else:
        raise ValueError(f"未知 grammar flavor: {flavor}")
    parser = Parser(Language(raw))
    _PARSERS[flavor] = parser
    return parser


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _script_ranges(source: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
    """Vue SFC 的脚本区间：[(首行代码行号, 末行代码行号, flavor)]（都是 1-based）。

    返回的区间已经排开 `<script>` / `</script>` 两行标签本身，所以调用方
    `lines[first-1:last]` 取到的就是可以交给语法解析的代码，行号也能直接映射回文件。
    """
    blocks: list[tuple[int, int, str]] = []
    covered: list[tuple[int, int]] = []
    lines = source.splitlines()
    opened: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _SCRIPT_OPEN.search(line) if not opened else None
        if match is not None:
            attrs = match.group(1) or ""
            flavor = "javascript" if re.search(r'lang\s*=\s*["\']?js\b', attrs, re.I) else "typescript"
            opened.append((index, flavor))
            continue
        if opened and _SCRIPT_CLOSE.search(line):
            start, flavor = opened.pop()
            if index - 1 >= start + 1:  # 空 script 块跳过
                blocks.append((start + 2, index, flavor))
                covered.append((start + 2, index))
    for start, flavor in opened:  # 未闭合的块：一直取到文件末尾
        if len(lines) >= start + 2:
            blocks.append((start + 2, len(lines), flavor))
            covered.append((start + 2, len(lines)))
    return blocks, covered


def _node_defs_refs(source: bytes, tree, lines: list[str], symbol: str,
                    offset: int, rel: str) -> tuple[list[dict], list[dict]]:
    defs: list[dict] = []
    refs: list[dict] = []
    for node in _walk(tree.root_node):
        kind = node.type
        if kind in ("comment", "html_comment", "error", "MISSING"):
            continue
        line = node.start_point[0] + 1 + offset
        code = lines[node.start_point[0]].strip() if node.start_point[0] < len(lines) else ""
        if kind == "identifier" or kind.endswith("property_identifier"):
            parent = node.parent
            name_field = parent.child_by_field_name("name") if parent is not None else None
            is_decl_name = (name_field is not None and name_field.id == node.id
                            and parent.type in _DEF_PARENTS
                            and parent.type not in _BINDING_PARENTS)
            if _text(source, node) != symbol or is_decl_name:
                continue
            is_key = (parent is not None and parent.type == "pair"
                      and parent.child_by_field_name("key") is not None
                      and parent.child_by_field_name("key").id == node.id)
            if is_key:
                # 对象字面量的键是「写这个名字」而不是「读它」：放进低置信桶，
                # 重命名时要同步改，但不能混进真实引用里。
                refs.append({"file": rel, "line": line, "code": code, "kind": "str"})
                continue
            is_attr = kind == "property_identifier"
            refs.append({"file": rel, "line": line, "code": code,
                         "kind": "attr" if is_attr else "ref"})
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None or kind not in _DEF_PARENTS:
            continue
        if _text(source, name_node) != symbol:
            continue
        if kind == "variable_declarator" and node.parent is not None \
                and node.parent.type in ("assignment_pattern", "object_pattern"):
            continue  # 解构赋值里的 foo 是绑定目标而不是新定义，交给 ref
        defs.append({"file": rel, "line": line, "code": code, "kind": "def"})
    return defs, refs


def _template_refs(pairs: list[tuple[int, str]], symbol: str, rel: str) -> list[dict]:
    """`<template>` 里的插值与绑定表达式 tree-sitter 看不见，用词边界正则补齐。

    HTML 注释可以跨行，所以先把整段非 script 文本拼起来剔掉注释，再按行号映射回去，
    这样 `<!-- foo -->` 里的符号不会被当成模板引用。
    """
    if not pairs:
        return []
    joined = "\n".join(text for _, text in pairs)
    joined = _HTML_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), joined)
    rx = re.compile(r"(?<![\w$])" + re.escape(symbol) + r"(?![\w$])")
    hits: list[dict] = []
    for (line_no, _), row in zip(pairs, joined.splitlines()):
        if rx.search(row):
            hits.append({"file": rel, "line": line_no, "code": row.strip(), "kind": "ref"})
    return hits


def _vue_matches(rel: str, source: str, lines: list[str], symbol: str):
    blocks, covered = _script_ranges(source)
    defs: list[dict] = []
    refs: list[dict] = []
    notes: list[str] = []
    if blocks:
        for first, last, flavor in blocks:
            parser = _load(flavor)
            chunk_lines = lines[first - 1:last]
            chunk = "\n".join(chunk_lines)
            chunk_bytes = chunk.encode("utf-8", "replace")
            tree = parser.parse(chunk_bytes)
            # 语法解析的是截取出来的 script 片段，字节偏移必须用同一段 payload 还原，
            # 用整个文件的字节会读错位置（行号才需要加 offset）
            d, r = _node_defs_refs(chunk_bytes, tree, chunk_lines, symbol, first - 1, rel)
            defs.extend(d)
            refs.extend(r)
    else:
        notes.append("这个 .vue 里没有 <script> 块，只看模板。")

    def _in_script(line_no: int) -> bool:
        return any(lo <= line_no <= hi for lo, hi in covered)

    pairs = [(index, text) for index, text in enumerate(lines, 1) if not _in_script(index)]
    refs.extend(_template_refs(pairs, symbol, rel))
    if defs or refs:
        notes.append("script 块走 tree-sitter 精确匹配；template 里的同名文本由词边界正则补充"
                     "（模板注释已剔除，但纯文本插值仍可能有噪声）。")
    return defs, refs, _dedupe_notes(notes)


def ts_matches(rel: str, source: str, symbol: str) -> tuple[list[dict], list[dict], list[str]] | None:
    """精确找出 `symbol` 的定义与引用；不适用或语法不可用时返回 None（调用方回退正则）。

    返回 `(defs, refs, notes)`：注释、字符串字面量与对象键里的同名文本【不会】被当成引用，
    这与原正则路径的全部区别。Vue 的 `<template>` 走补充正则并如实记入 notes。
    """
    ext = ("." + rel.rsplit(".", 1)[-1].lower()) if "." in rel else ""
    if ext not in TS_EXTS or not symbol:
        return None
    lines = source.splitlines()
    try:
        if ext == ".vue":
            return _vue_matches(rel, source, lines, symbol)
        flavor = "tsx" if ext == ".tsx" else ("javascript" if ext in (".js", ".jsx", ".cjs", ".mjs")
                                              else "typescript")
        parser = _load(flavor)
    except Exception as exc:  # noqa: BLE001
        global _IMPORT_ERROR
        _IMPORT_ERROR = str(exc)
        return None
    tree = parser.parse(source.encode("utf-8", "replace"))
    defs, refs = _node_defs_refs(source.encode("utf-8", "replace"), tree, lines, symbol, 0, rel)
    return defs, refs, []


def _dedupe_notes(notes: list[str]) -> list[str]:
    out: list[str] = []
    for note in notes:
        if note and note not in out:
            out.append(note)
    return out


def import_error() -> str:
    return _IMPORT_ERROR
