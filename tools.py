"""Agent 工具集：知识库检索 / 素材筛选 / 计算器 / 联网搜索 / 代码执行 / 视频提示词生成。

每个工具含 description（给 LLM 看的说明）与 func（实际执行函数）。
新增工具：在 TOOLS 字典里追加一项即可，Agent 会自动识别。
"""
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
import urllib.parse

from config import TOP_K, CODE_COLLECTION_NAME, CODE_ROOT, get_runtime, set_runtime, edit_confirm_enabled
from embeddings import EmbeddingClient
from vectorstore import query as vs_query, pretty_source
from ingest import _CODE_EXT, _SKIP_DIRS

_emb = None


def _get_emb():
    global _emb
    if _emb is None:
        _emb = EmbeddingClient()
    return _emb


def search_knowledge(query):
    """在已上传的知识库中检索与问题相关的文档片段。"""
    emb = _get_emb().embed([query])[0]
    res = vs_query(emb, k=TOP_K)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    if not docs:
        return "知识库中未找到相关内容。"
    out = []
    for d, m in zip(docs, metas):
        src = pretty_source(m.get("source", "")) if m else ""
        if not src:
            src = "?"
        # 截断单个 chunk 的长度：避免把大段原文塞给小模型逼它"逐条抄"
        text = d if len(d) <= 500 else d[:500].rstrip() + "…"
        out.append(f"[{src}] {text}")
    return "\n---\n".join(out)


def calculate(expression):
    """对数学表达式求值，仅允许数字与 + - * / % 和括号。"""
    expression = (expression or "").strip().strip("'\"").strip()
    if not expression:
        return "未提供表达式。"
    allowed = set("0123456789+-*/().% ")
    if not all(c in allowed for c in expression):
        return "表达式包含非法字符，仅支持数字与 + - * / % 和括号。"
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:  # noqa: BLE001
        return f"计算失败: {e}"


def web_search(query):
    """联网搜索（DuckDuckGo HTML，无需 API Key）。

    返回前 5 条结果的标题/摘要/链接；网络不可达时优雅降级并说明原因。
    注意：依赖运行环境能访问外网；若所在网络屏蔽 DuckDuckGo，可后续替换为
    带 Key 的搜索引擎（SerpAPI / Bing 等），只需改本函数。
    """
    q = (query or "").strip().strip("'\"")
    if not q:
        return "未提供搜索关键词。"
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; DocMind/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return f"搜索失败: {type(e).__name__}: {e}（请确认运行环境能访问外网，或换用带 Key 的搜索引擎）"

    def _clean(s):
        s = re.sub(r"<[^>]+>", "", s or "")
        return urllib.parse.unquote(s).strip()

    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)
    links = re.findall(r'class="result__a"[^>]*href="(.*?)"', page)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    if not titles:
        # 兜底：DDG 偶尔换结构，尝试更宽松的抓取
        titles = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>', page, re.S)
    if not titles:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"

    lines = []
    for i in range(min(5, len(titles))):
        t = _clean(titles[i])
        s = _clean(snippets[i]) if i < len(snippets) else ""
        link = links[i] if i < len(links) else ""
        # DDG 的跳转链接可能带 uddg= 编码，尽量还原
        if "uddg=" in link:
            try:
                link = urllib.parse.unquote(link.split("uddg=", 1)[1].split("&", 1)[0])
            except Exception:
                pass
        lines.append(f"· {t}\n  {s}\n  {link}")
    return "\n".join(lines)


def python_exec(code):
    """在受限子进程中执行 Python 代码，返回 stdout/stderr（截断到 1500 字）。

    用于数值计算、数据处理、文本变换等"让 agent 真正动手"的场景。
    超时 12 秒；这是本地开发工具，以当前用户权限运行，请勿用于不可信代码。
    """
    code = (code or "").strip()
    # 清理模型可能包裹的代码围栏 / 语言提示词，避免把 "```python" 当代码执行
    code = re.sub(r"^```(?:python|py)?\s*", "", code)
    code = re.sub(r"\s*```$", "", code)
    code = re.sub(r"^\s*(?:python|py)\s*$", "", code, flags=re.I | re.M)  # 去掉独立的提示行
    code = code.strip().strip("`").strip()
    if not code:
        return "未提供有效代码。"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=12,
            cwd=os.path.dirname(__file__),
        )
    except subprocess.TimeoutExpired:
        return "代码执行超时（>12s），可能被死循环阻塞。"
    except Exception as e:  # noqa: BLE001
        return f"执行失败: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        return "（代码已执行，无输出）"
    return out[:1500] + ("…" if len(out) > 1500 else "")


# ---------------------------------------------------------------------------
# 视频提示词生成工具：按 MiniMax H3 的"三段结构"把创意描述转为可直接粘贴进
# ComfyUI 节点 (MiniMaxH3ImageToVideo) 的结构化提示词。
# 设计说明：三段骨架（integrated_multimodal_description / overall_soundscape /
# non_diegetic_music）由本工具确定性拼装，保证永远符合 H3 格式；LLM 仅用于润色
# Shot 1 的英文描述，失败则回退模板句。这样无论模型强弱都能产出可用提示词。
# ---------------------------------------------------------------------------
def _enrich_shot1(spec):
    """用 LLM 把中文创意扩写成一句英文分镜描述；失败时回退到确定性模板句。"""
    provider = get_runtime("llm_provider") or ""
    if provider and provider != "mock":
        try:
            from llm import LLMClient

            sys_p = (
                "把下面中文创意扩写成一句英文分镜描述（含主体动作、镜头运动、物理力），"
                "只输出这一句英文，不要解释，不超过 40 词。"
            )
            client = LLMClient()
            out = client.chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": spec}],
                stream=False,
            )
            out = (out or "").strip().strip("`").strip()
            # 防御：若模型没按要求只给一句英文，回退模板
            if out and "integrated_multimodal" not in out and len(out) < 400:
                return out
        except Exception:  # noqa: BLE001
            pass
    return (
        f"{spec}。镜头缓慢推近，机位与主体视线平齐，"
        "注意点名物理力（重力/风/液体/布料张力）让画面更真实。"
    )


def gen_video_prompt(spec):
    """按 MiniMax H3 的三段结构，把一段创意描述生成结构化视频提示词。

    结构（integrated_multimodal_description / overall_soundscape / non_diegetic_music）
    由本工具确定性拼装，保证永远符合 H3 格式；LLM 仅用于润色 Shot 1 的英文描述。
    输出可直接粘贴进 ComfyUI 的 MiniMaxH3ImageToVideo 节点的 prompt 字段。
    """
    spec = (spec or "").strip().strip("'\"")
    if not spec:
        return "未提供创意描述，请描述你想生成的画面（主体/场景/动作/氛围）。"

    shot1 = _enrich_shot1(spec)

    # 关键词驱动的声音设计（diegetic），让画内音贴合场景
    amb = []
    if "雨" in spec:
        amb.append("steady rain")
    if "夜" in spec or "晚" in spec:
        amb.append("distant night traffic")
    if "风" in spec:
        amb.append("wind moving through the scene")
    if "海" in spec or "浪" in spec:
        amb.append("waves on the shore")
    if "城" in spec or "市" in spec:
        amb.append("low city hum")
    if "森" in spec or "林" in spec:
        amb.append("rustling leaves")
    if "雪" in spec:
        amb.append("soft snowfall")
    if not amb:
        amb.append("ambient room tone")
    sound = "画内音逐一点名：" + ", ".join(amb) + "。"

    music = (
        "配乐：pulsing synth arpeggio at a steady mid tempo over sustained low bass, "
        "with a filtered swell that builds then drops."
    )

    return (
        "integrated_multimodal_description:\n"
        f"[Shot 1] Live-action, cinematic. {shot1}\n"
        "[Shot 2] At 00:03.000，承接上一镜，引用 Shot 1 的角色与位置，推进后续动作与状态变化。\n"
        "overall_soundscape:\n"
        f"{sound}\n"
        "non_diegetic_music:\n"
        f"{music}\n\n"
        "（约束：无负面提示词；FPS 固定 24；时长 4-15s；提示词 < 7000 字符且需 >= 100 词则继续扩写；"
        "不说话的角色写 lips stay closed。）"
    )


# ---------------------------------------------------------------------------
# 素材筛选工具：在本地精选素材目录（Kenney CC0 等）中按关键词 / 类型 / 许可筛选。
# 设计说明：
#   - 素材库（Kenney / OpenGameArt / itch）没有稳定可用的公开 API，且多被
#     Cloudflare 保护，直接爬取易失败、也不礼貌。业界做法（如 Arcane Assets
#     MCP）同样是维护一份本地 / 远程的素材清单 JSON，由 Agent 在其上做检索。
#   - 因此这里用一份人工精选 + 持续可扩充的 assets_catalog.json，配合本工具的
#     关键词展开与打分排序，实现「根据提问筛选素材」。
# ---------------------------------------------------------------------------
_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "assets_catalog.json")

# 中文关键词 -> 英文检索词 的同义展开表，使中文提问也能命中英文标签。
_CN_SYNONYMS = {
    "角色": "character sprite",
    "精灵": "sprite character",
    "人物": "character",
    "怪物": "monster creature",
    "动物": "animal",
    "龙": "dragon",
    "骑士": "knight",
    "巫师": "wizard",
    "战士": "knight warrior",
    "像素": "pixel",
    "地形": "tileset tile",
    "地块": "tileset tile",
    "贴图": "tile texture",
    "地图": "tile map",
    "等距": "isometric",
    "模型": "model 3d",
    "三维": "3d model",
    "界面": "ui interface",
    "图标": "icon ui",
    "按钮": "ui button",
    "音效": "sfx sound audio",
    "声音": "sound audio",
    "音乐": "music",
    "字体": "font",
    "特效": "particle vfx effect",
    "粒子": "particle",
    "动画": "animation",
    "战斗": "battle",
    "地牢": "dungeon",
    "城镇": "town",
    "小镇": "town",
    "城市": "city",
    "太空": "space",
    "宇宙": "space",
    "射击": "shooter",
    "坦克": "tank",
    "飞船": "ship",
    "平台": "platformer",
    "美术": "art asset",
    "资源": "asset",
    "素材": "asset",
}


def _expand_query(q):
    """把用户提问展开成一组检索 token（英文/数字原样保留，中文按同义词展开）。"""
    q = (q or "").lower()
    tokens = re.findall(r"[a-z0-9]+", q)  # 抽取英文 / 数字片段，中文串里也抓得到
    for cn, en in _CN_SYNONYMS.items():
        if cn in q:
            tokens.extend(en.split())
    # 去重保序
    seen, out = set(), []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def search_assets(query, asset_type=None, license=None):
    """在精选游戏素材目录（Kenney CC0 等）中按关键词/类型/许可筛选素材。

    返回命中条目的可读列表；按关键词命中数量打分排序，最多 8 条。
    asset_type 例：'2d-sprites' / 'tilesets' / 'ui' / 'audio' / 'fonts' / '3d'
    license    例：'CC0' / 'CC-BY' / 'CC-BY-SA-3.0'
    """
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        return "未找到相关素材。"

    tokens = _expand_query(query)
    type_filter = (asset_type or "").lower().strip()
    lic_filter = (license or "").strip()

    scored = []
    for item in catalog:
        cat = (item.get("category") or "").lower()
        lic = item.get("license") or ""
        if type_filter and cat != type_filter:
            continue
        if lic_filter and lic != lic_filter:
            continue
        hay = " ".join([
            item.get("name", ""),
            " ".join(item.get("tags", [])),
            item.get("desc", ""),
            item.get("category", ""),
            lic,
        ]).lower()
        if tokens:
            score = sum(1 for t in tokens if t in hay)
            if score == 0:
                continue
        else:
            score = 1
        scored.append((score, item))

    if not scored:
        return "未找到相关素材。"
    scored.sort(key=lambda x: -x[0])
    lines = []
    for _, it in scored[:8]:
        lines.append(
            f"· {it['name']} ［{it['category']}｜{it['license']}］ {it.get('desc', '')}  → {it.get('url', '')}"
        )
    return "\n".join(lines)


def set_embedding_provider(provider):
    """切换检索用的 embedding provider（如 local <-> qwen），并清空缓存。"""
    set_runtime("embedding_provider", provider)
    global _emb
    _emb = None


# ---------------------------------------------------------------------------
# 代码工具：让 Agent 能检索 / 阅读 / 搜索已索引的代码库（代码问答模式）。
# 所有路径操作都限定在 code_root 之内，避免越界读取本机其它文件。
# ---------------------------------------------------------------------------
def _get_code_root():
    return (get_runtime("code_root") or CODE_ROOT or "").strip()


# 记录 Agent 本次会话内已用 read_file 读过的文件（绝对路径，normcase 归一），
# 用于 apply_edit 的"先读后写"安全护栏：未确认过内容的文件不允许整体重写。
_READ_FILES = set()


def _resolve_in_root(path):
    """把 path 解析为 code_root 内的绝对路径；越界返回 (None, root_abs)。"""
    root = _get_code_root()
    root_abs = os.path.normpath(root)
    target = os.path.normpath(path if os.path.isabs(path) else os.path.join(root_abs, path))
    if not (target == root_abs or target.startswith(root_abs + os.sep)):
        alt = os.path.normpath(os.path.join(root_abs, path.lstrip("./\\")))
        if os.path.isfile(alt) and (alt == root_abs or alt.startswith(root_abs + os.sep)):
            return alt, root_abs
        return None, root_abs
    return target, root_abs


def _clean_symbol(p):
    """从混入自然语言的输入里提取首个「代码标识符」token。

    弱模型常把工具输入写成「seekTo 进行进度跳转」这种「符号 + 中文描述」的形式，
    直接当正则/检索词会匹配失败。这里在检测到中文时只取第一个 ASCII 标识符
    （如 seekTo）；纯 ASCII 输入（可能是合法正则）原样保留。
    """
    p = (p or "").strip()
    if not p:
        return p
    if re.search(r"[\u4e00-\u9fff]", p):
        m = re.search(r"[A-Za-z_]\w*", p)
        if m:
            return m.group(0)
    return p


def search_code(query):
    """在已索引的源代码/配置中检索相关函数、类、配置片段。"""
    if not _get_code_root():
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录后再问代码相关问题。"
    query = _clean_symbol(query)
    emb = _get_emb().embed([query])[0]
    res = vs_query(emb, k=TOP_K, collection=CODE_COLLECTION_NAME)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    if not docs:
        return "代码库未找到相关内容，建议改用 grep 搜索关键词或 read_file 查看具体文件。"
    out = []
    for d, m in zip(docs, metas):
        src = m.get("source", "?")
        sym = m.get("symbol", "")
        lang = m.get("lang", "")
        label = f"{src} › {sym}" if sym else src
        if lang:
            label += f" ({lang})"
        text = d if len(d) <= 600 else d[:600].rstrip() + "…"
        out.append(f"[{label}]\n{text}")
    return "\n---\n".join(out)


def read_file(path):
    """读取代码库中的文件内容（path 为相对 code_root 的路径或文件名）。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    root_abs = os.path.normpath(root)
    target = os.path.normpath(path if os.path.isabs(path) else os.path.join(root_abs, path))
    # 路径越界防护：只允许读取 code_root 目录内的文件
    if not (target == root_abs or target.startswith(root_abs + os.sep)):
        alt = os.path.normpath(os.path.join(root_abs, path.lstrip("./\\")))
        if os.path.isfile(alt) and (alt == root_abs or alt.startswith(root_abs + os.sep)):
            target = alt
        else:
            return f"拒绝访问：{path} 不在代码根目录内。"
    if not os.path.isfile(target):
        return f"文件不存在：{path}"
    try:
        with open(target, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:  # noqa: BLE001
        return f"读取失败: {e}"
    # 记录已读，供 apply_edit 的"先读后写"护栏使用
    _READ_FILES.add(os.path.normcase(target))
    if len(content) > 4000:
        content = content[:4000] + "\n…（已截断，仅显示前 4000 字）"
    rel = os.path.relpath(target, root_abs)
    return f"=== {rel} ===\n{content}"


def grep(pattern):
    """在代码库中按正则搜索文本/符号，返回匹配的文件路径与行号。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    pattern = _clean_symbol(pattern)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"正则错误: {e}"
    root_abs = os.path.normpath(root)
    hits = []
    for dp, dns, fns in os.walk(root_abs):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        for fn in fns:
            if os.path.splitext(fn)[1].lower() not in _CODE_EXT:
                continue
            fp = os.path.join(dp, fn)
            if os.path.getsize(fp) > 2_000_000:
                continue
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{os.path.relpath(fp, root_abs)}:{i}: {line.rstrip()}")
                            if len(hits) >= 40:
                                break
            except Exception:  # noqa: BLE001
                pass
            if len(hits) >= 40:
                break
        if len(hits) >= 40:
            break
    if not hits:
        return f"代码库中未匹配到：{pattern}"
    return "\n".join(hits[:40])


# ---------------------------------------------------------------------------
# 受控写工具 apply_edit：让 Agent 能按用户指令修改 code_root 内的已存在文件，
# 但绝不越界、绝不误删、绝不写坏 .py。护栏分四层：
#   1) 路径沙箱：复用 _resolve_in_root，越界直接拒（与 read_file 同逻辑）。
#   2) 先读后写：整体重写时必须已在本次会话 read_file 过该文件；
#      或提供 old_text 做"精确局部替换"（更安全，推荐）。
#   3) 体积上限：新文件 > 200KB 拒写。
#   4) .py 语法校验：写入前 py_compile 编译，失败回滚原文件并报告。
# ---------------------------------------------------------------------------
_APPLY_MAX_BYTES = 200 * 1024  # 单文件写入上限 200KB


def _parse_edit_input(inp):
    """解析 apply_edit 的 Action Input（多行 keyed 格式），返回 (path, old_text, new_text)。

    约定格式（字段顺序无所谓，old_text / new_text 的值可写在 key: 同行或下一行，且可多行）：
        path: <相对或绝对路径>
        old_text: <可选：要被替换的精确旧片段>
        new_text: <替换后的新内容（可多行）>
    解析策略：定位三个字段标记（行首 `key:`），每个字段的值 = 标记之后到下一个字段
    标记（或文本末尾）之间的内容。只去掉字段标记紧跟的那个换行分隔符；最后一个字段
    （通常是 new_text/文件内容）保留其尾部换行，不丢失文件内容。这样无论是同行值还是
    多行值都稳。
    """
    inp = (inp or "").lstrip("\n")
    keys = ["path", "old_text", "new_text"]
    spans = []
    for key in keys:
        for m in re.finditer(r"^\s*" + key + r"\s*:\s*", inp, re.M):
            spans.append((m.start(), m.end(), key))
    spans.sort()
    fields = {}
    for i, (s, e, key) in enumerate(spans):
        val_end = spans[i + 1][0] if i + 1 < len(spans) else len(inp)
        val = inp[e:val_end]
        # 去掉标记后紧跟的那个换行分隔符（key: 与值换行的情形）
        if val.startswith("\n"):
            val = val[1:]
        # 非最后一个字段：其尾部换行是字段间分隔符，应剥掉（避免 old_text 多带空行匹配失败）
        # 最后一个字段（通常是 new_text/文件内容）：保留尾部换行，不丢失文件内容
        if val_end < len(inp):
            val = val.rstrip("\n")
        fields[key] = val
    return fields.get("path"), fields.get("old_text"), fields.get("new_text")


def _diff_summary(a, b):
    """生成简短 unified-diff 摘要（最多 30 行变更），用于回显给 Agent 与用户。"""
    if a == b:
        return "（内容无变化）"
    import difflib

    a_lines = a.splitlines()
    b_lines = b.splitlines()
    diff = list(difflib.unified_diff(a_lines, b_lines, lineterm="", n=1))
    body = [d for d in diff if not d.startswith(("---", "+++"))]
    if len(body) > 30:
        body = body[:30] + [f"...（共 {len(body)} 行变更，已截断显示前 30 行）"]
    return "Diff:\n" + "\n".join(body)


# ---------------------------------------------------------------------------
# 待确认修改暂存：当「人工确认」模式开启时，apply_edit / create_file 不直接写盘，
# 而是校验通过后暂存到内存，返回 pending id + diff，由人工在界面上确认/拒绝。
# ---------------------------------------------------------------------------
_PENDING_EDITS = {}       # id -> {kind, target, rel, old_content, new_content, diff}
_PENDING_LOCK = threading.Lock()
_pending_seq = [0]


def _edit_confirm_on():
    return edit_confirm_enabled()


def stage_edit(kind, target, rel, old_content, new_content, diff):
    """暂存一次已校验但未落盘的修改，返回 pending id。"""
    with _PENDING_LOCK:
        _pending_seq[0] += 1
        pid = str(_pending_seq[0])
        _PENDING_EDITS[pid] = {
            "kind": kind,            # "apply_edit" | "create_file"
            "target": target,        # 绝对路径
            "rel": rel,
            "old_content": old_content,
            "new_content": new_content,
            "diff": diff,
        }
        return pid


def list_pending_edits():
    with _PENDING_LOCK:
        return [
            {
                "id": pid,
                "kind": e["kind"],
                "path": e["rel"],
                "diff": e["diff"],
                "size": len(e["new_content"].encode("utf-8", "ignore")),
            }
            for pid, e in _PENDING_EDITS.items()
        ]


def confirm_edit(pid):
    """落盘一次已暂存的修改；成功返回 (True, 摘要)，失败返回 (False, 原因)。

    若失败原因是「暂存内容已失效」（文件被改动/删除/已被创建），会同时丢弃该待确认项，
    避免界面残留一个永远无法确认的条目；仅「写入 I/O 错误」会保留以便重试。
    """
    with _PENDING_LOCK:
        e = _PENDING_EDITS.get(pid)
    if not e:
        return False, f"未找到待确认修改 #{pid}（可能已确认/拒绝或重启后失效）。"
    target, kind = e["target"], e["kind"]

    def _discard(reason):
        with _PENDING_LOCK:
            _PENDING_EDITS.pop(pid, None)
        return False, reason

    try:
        if kind == "create_file":
            if os.path.exists(target):
                return _discard(f"目标文件已存在：{e['rel']}（暂存后文件被创建，为避免覆盖已取消）。")
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
        else:  # apply_edit
            if not os.path.isfile(target):
                return _discard(f"目标文件已不存在：{e['rel']}")
            # 防「暂存后被改动」：内容不一致则拒绝，避免覆盖他人在暂存期间的修改
            try:
                with open(target, encoding="utf-8", errors="ignore") as f:
                    cur = f.read()
            except Exception as ex:  # noqa: BLE001
                return False, f"读取目标失败: {ex}"
            if cur != e["old_content"]:
                return _discard("目标文件在暂存后被修改，内容已变化；请重新 read_file 后再改。")
        with open(target, "w", encoding="utf-8") as f:
            f.write(e["new_content"])
        _READ_FILES.add(os.path.normcase(target))
    except Exception as ex:  # noqa: BLE001
        return False, f"写入失败: {ex}"
    with _PENDING_LOCK:
        _PENDING_EDITS.pop(pid, None)
    nbytes = len(e["new_content"].encode("utf-8", "ignore"))
    return True, f"已应用修改 {e['rel']}（{nbytes} 字节）。"


def reject_edit(pid):
    with _PENDING_LOCK:
        return bool(_PENDING_EDITS.pop(pid, None))


def clear_read_files():
    """清空「已读文件」记录（重置代码库时调用，避免旧项目的读记录跨项目残留）。"""
    _READ_FILES.clear()


def apply_edit(arg):
    """受控修改代码库中的【已存在】文件。详见 _parse_edit_input 的输入格式。

    两种用法：
      · 局部安全替换：提供 path + old_text（要匹配的精确旧片段）+ new_text；
        工具在文件中唯一匹配处替换，匹配 0 处或 >1 处都会拒绝以避免歧义/误改。
      · 整体重写：只提供 path + new_text（不带 old_text），但前提是你已用
        read_file 读取过该文件（确认过当前内容）。
    护栏：只能改 code_root 内已存在文件，不能新建、不能越界；写入后 .py 会做语法
    校验，失败自动回滚。返回：已写入 / 失败原因 / diff 摘要。
    """
    path, old_text, new_text = _parse_edit_input(arg)
    if not path:
        return "参数缺失：请提供 path: <文件路径> 与 new_text: <新内容>。"
    if new_text is None:
        return "参数缺失：请提供 new_text: <新内容>（若做局部替换，还需 old_text: <旧片段>）。"

    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"

    target, root_abs = _resolve_in_root(path)
    if target is None:
        return f"拒绝写入：{path} 不在代码根目录内（禁止越界写）。"
    if not os.path.isfile(target):
        return f"文件不存在：{path}（apply_edit 只修改已存在文件，不会新建文件）。"

    try:
        with open(target, encoding="utf-8", errors="ignore") as f:
            old_content = f.read()
    except Exception as e:  # noqa: BLE001
        return f"读取原文件失败: {e}"

    # 决定新内容 + 局部/整体模式
    if old_text is not None:
        if old_text == "":
            return "old_text 为空，无法确定替换范围（整体重写请省略 old_text；局部替换请提供精确片段）。"
        cnt = old_content.count(old_text)
        if cnt == 0:
            return "安全限制：未找到 old_text 的匹配，可能内容已变化。请重新 read_file 确认当前内容后再改。"
        if cnt > 1:
            return f"安全限制：old_text 在文件中匹配到 {cnt} 处，存在歧义。请提供更精确的 old_text（含前后上下文）以唯一定位。"
        new_content = old_content.replace(old_text, new_text, 1)
    else:
        # 整体重写：必须先 read_file 确认过当前内容（防 Agent 凭空改写）
        if os.path.normcase(target) not in _READ_FILES:
            return ("安全限制：整体重写前请先调用 read_file 读取该文件确认当前内容，"
                    "或改用 old_text 参数做局部安全替换。")
        new_content = new_text

    # 护栏：单文件体积上限
    if len(new_content.encode("utf-8", "ignore")) > _APPLY_MAX_BYTES:
        return f"拒绝写入：新文件大小超过上限（{_APPLY_MAX_BYTES // 1024}KB）。"

    # 护栏：.py 语法校验（失败回滚，原文件不动）
    ext = os.path.splitext(target)[1].lower()
    if ext == ".py":
        import tempfile
        import py_compile

        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(new_content)
                tmp = tf.name
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            return f"语法校验失败，已取消写入（原文件未改动）：\n{e.msg}"
        except Exception as e:  # noqa: BLE001
            return f"语法校验异常，已取消写入（原文件未改动）：{e}"
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

    rel = os.path.relpath(target, root_abs)
    nbytes = len(new_content.encode("utf-8", "ignore"))
    summary = _diff_summary(old_content, new_content)

    # 「人工确认」模式：只暂存，不落盘，返回 diff 等人工批准
    if _edit_confirm_on():
        pid = stage_edit("apply_edit", target, rel, old_content, new_content, summary)
        return f"待人工确认 #{pid}（未写入）。确认后才会真正修改文件。\n{summary}"

    # 写回
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:  # noqa: BLE001
        return f"写入失败: {e}"

    return f"已写入 {rel}（{nbytes} 字节，路径沙箱校验通过）。\n{summary}"


# ---------------------------------------------------------------------------
# 受控"新建文件"工具 create_file：让 Agent 能在 code_root 内新增模块/分区文件，
# 但依然受沙箱、体积、语法护栏约束，且绝不覆盖已有文件（覆盖请用 apply_edit）。
# 设计意图：配合 search_code/grep，让"加新功能"走"先确认无重复 → 再新建"的
# 受控路径，而不是凭空生成导致堆叠。
# ---------------------------------------------------------------------------
def _resolve_create_path(path):
    """解析新建文件的绝对路径；越界返回 (None, root_abs)。不要求文件已存在。"""
    root = _get_code_root()
    root_abs = os.path.normpath(root)
    target = os.path.normpath(path if os.path.isabs(path) else os.path.join(root_abs, path))
    if not (target == root_abs or target.startswith(root_abs + os.sep)):
        return None, root_abs
    return target, root_abs


def create_file(arg):
    """在代码库内【新建】一个文件（不能覆盖已有文件）。详见 _parse_edit_input 输入格式。

    输入：path: <相对或绝对路径>，new_text: <文件内容（可多行）>。
    护栏：① 路径沙箱（必须落在 code_root 内）；② 不覆盖已有文件（改已有用 apply_edit）；
    ③ 父目录不存在时允许自动创建一层（仍在 code_root 内，便于新建分区）；
    ④ 单文件 200KB 上限；⑤ .py 写入前 py_compile 语法校验，失败取消创建。
    返回：已创建 / 失败原因。
    """
    path, _, content = _parse_edit_input(arg)
    if not path:
        return "参数缺失：请提供 path: <文件路径> 与 new_text: <文件内容>。"
    if content is None:
        return "参数缺失：请提供 new_text: <文件内容（可多行）>。"
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"

    target, root_abs = _resolve_create_path(path)
    if target is None:
        return f"拒绝写入：{path} 不在代码根目录内（禁止越界写）。"
    if os.path.exists(target):
        return f"文件已存在：{path}（create_file 不覆盖已有文件；要修改请用 apply_edit）。"

    if len(content.encode("utf-8", "ignore")) > _APPLY_MAX_BYTES:
        return f"拒绝写入：新文件大小超过上限（{_APPLY_MAX_BYTES // 1024}KB）。"

    ext = os.path.splitext(target)[1].lower()
    if ext == ".py":
        import tempfile
        import py_compile

        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(content)
                tmp = tf.name
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            return f"语法校验失败，已取消创建：\n{e.msg}"
        except Exception as e:  # noqa: BLE001
            return f"语法校验异常，已取消创建：{e}"
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

    rel = os.path.relpath(target, root_abs)
    nbytes = len(content.encode("utf-8", "ignore"))
    preview = content if len(content) <= 2000 else content[:2000] + "\n…（已截断，完整内容 %d 字节）" % nbytes

    # 「人工确认」模式：只暂存，不落盘
    if _edit_confirm_on():
        pid = stage_edit("create_file", target, rel, None, content, "新建文件：\n" + preview)
        return f"待人工确认 #{pid}（未写入）。确认后才会真正创建文件。\n新建文件：\n{preview}"

    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            return f"创建目录失败: {e}"

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return f"写入失败: {e}"

    # 自己刚创建的文件，视为已"确认内容"，允许后续整体重写而无需再 read_file
    _READ_FILES.add(os.path.normcase(target))
    return f"已创建 {rel}（{nbytes} 字节，路径沙箱校验通过）。"


# ---------------------------------------------------------------------------
# 命令执行工具 run_command：在 code_root 内跑构建/测试/项目命令，返回输出。
# 护栏：cwd 锁 code_root；危险命令黑名单拦截；超时 12s（同 python_exec）；
# stdout+stderr 合并截断 1500 字。
# ---------------------------------------------------------------------------
# 命令护栏：结构化黑名单（按可执行词）+ 可选白名单（RUN_COMMAND_ALLOW）。
# 仅为启发式防护，非 OS 级沙箱；真正的隔离需外部容器/沙箱。
_BLOCKED_CMDS = {
    # 破坏性文件/系统命令
    "rm", "del", "erase", "rd", "rmdir", "format", "shutdown", "restart", "reboot",
    "mkfs", "fdisk", "diskpart", "dd", "chkdsk", "chown", "chmod", "cacls", "icacls",
    "shred", "wipe", "cipher",
    # 网络外联 / 下载（防数据外带）
    "curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm",
    "nc", "netcat", "ncat", "telnet", "ssh", "scp", "sftp", "ftp", "certutil",
    # 脚本解释器 / 子 shell / 系统操控（可执行任意命令，绕过黑名单）
    "powershell", "pwsh", "bash", "sh", "zsh", "cmd", "reg", "wmic", "mshta",
    "rundll32", "schtasks", "wsl", "taskkill", "net",
}
# 危险子串/参数模式（命令+参数级，比单一可执行词更细）
_DANGEROUS_PATTERNS = (
    "rm -rf", "rm -r ", "rm -fr", "del /s", "del /q", "rd /s", "rmdir /s",
    "format ", "shutdown", "mkfs", "dd if=", "> /dev/sd", "> /dev/null",
    "git clean", "git reset --hard", "git push -f", "git push --force",
    "chmod -r", "chmod 777", "chmod 666", ":(){", "fork bomb",
    "python -c", "py -c", "python3 -c", "node -e", "node --eval",
    "npm install -g", "npm i -g", "pip install", "rmdir /q",
)


def _first_command_token(low):
    """提取命令首个「可执行词」（去引号/前缀/环境变量），取 basename 用于黑/白名单匹配。"""
    s = low.strip()
    s = re.sub(r"^(?:cmd(?:\.exe)?\s+/[ck]\s+)", "", s)    # cmd /c ...
    s = re.sub(r"^(?:[a-z_][a-z0-9_]*=[^ ]*\s+)+", "", s)   # VAR=value 前缀
    s = re.sub(r"^call\s+", "", s)
    m = re.match(r'"?([^"\s]+)"?', s)
    if not m:
        return ""
    tok = os.path.basename(m.group(1)).lower()
    return tok[:-4] if tok.endswith(".exe") else tok


def _cmd_is_blocked(cmd):
    """返回 (True, 原因) 表示应拦截；否则 (False, '')。"""
    low = re.sub(r"\s+", " ", cmd.lower()).strip()
    for pat in _DANGEROUS_PATTERNS:
        if pat in low:
            return True, pat
    first = _first_command_token(low)
    if first in _BLOCKED_CMDS:
        return True, first
    allow = (get_runtime("run_command_allow") or os.getenv("RUN_COMMAND_ALLOW", "")).strip()
    if allow:
        allowed = {a.strip().lower() for a in allow.split(",") if a.strip()}
        if first not in allowed:
            return True, f"{first}（不在白名单内）"
    return False, ""


def run_command(cmd):
    """在代码库根目录内执行 shell 命令（如 pytest / npm run build），返回合并输出（截断 1500 字，超时 12s）。

    用于跑构建、跑测试、执行项目内命令来验证改动或查看结果。命令在 code_root 内执行。
    护栏：结构化黑名单（破坏性命令 / 网络外联 / 脚本解释器）+ 可选白名单（RUN_COMMAND_ALLOW）。
    说明：这是启发式防护，非 OS 级沙箱；真正的隔离需外部容器/沙箱。
    """
    cmd = (cmd or "").strip().strip("'\"")
    if not cmd:
        return "未提供命令。"
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    blocked, why = _cmd_is_blocked(cmd)
    if blocked:
        return f"拒绝执行：命令被安全策略拦截（命中「{why}」）。"
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=os.path.normpath(root),
            timeout=12, capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return "命令执行超时（>12s），可能被死循环或长构建阻塞；如需更长超时请分步执行。"
    except Exception as e:  # noqa: BLE001
        return f"执行失败: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    head = f"[exit code {proc.returncode}]\n"
    if not out.strip():
        return head + "（命令已执行，无输出）"
    combined = head + out
    return combined[:1500] + ("…" if len(combined) > 1500 else "")


TOOLS = {
    "search_knowledge": {
        "description": "在已上传的知识库中检索相关文档片段。输入应为检索关键词或问题。",
        "func": search_knowledge,
    },
    "search_assets": {
        "description": "在精选游戏素材目录（Kenney CC0 等）中按关键词/类型/许可筛选素材，适合回答'找素材/美术资源/角色精灵/tileset/UI/音效'类问题。",
        "func": search_assets,
    },
    "calculate": {
        "description": "对数学表达式求值，例如 '23*45+12'。仅支持 + - * / % 和括号。",
        "func": calculate,
    },
    "web_search": {
        "description": "当知识库不足或需要时效性/外部信息时，联网搜索（DuckDuckGo，无需 Key）。输入为搜索关键词。返回前 5 条结果的标题/摘要/链接。",
        "func": web_search,
    },
    "python_exec": {
        "description": "在受限子进程中执行 Python 代码并返回输出（超时 12s）。适合数值计算、数据处理、文本变换、小规模绘图数据生成等'让 agent 真正动手'的任务。输入为完整 Python 代码。",
        "func": python_exec,
    },
    "gen_video_prompt": {
        "description": "按 MiniMax H3 的三段结构（integrated_multimodal_description / overall_soundscape / non_diegetic_music）把一段创意描述生成为结构化视频提示词，可直接粘贴进 ComfyUI 的 MiniMaxH3ImageToVideo 节点。输入为自然语言创意（主体/场景/动作/氛围）。",
        "func": gen_video_prompt,
    },
    "search_code": {
        "description": "在已索引的源代码/配置中检索相关函数、类、配置片段。回答'某功能在哪实现/某函数做什么/某配置怎么写'等关于代码库的问题。输入为搜索关键词。",
        "func": search_code,
    },
    "read_file": {
        "description": "读取代码库中的某个文件内容（path 为相对代码根目录的路径或文件名）。需要看完整文件、或某文件细节时用。返回文件内容（截断到 4000 字）。",
        "func": read_file,
    },
    "grep": {
        "description": "在代码库中按正则表达式搜索文本/符号，返回匹配的文件路径与行号。定位某段代码、某变量、某错误出现位置时用。输入为正则表达式。",
        "func": grep,
    },
    "apply_edit": {
        "description": "受控修改代码库中【已存在】的文件（不能新建、不能越界写）。两种用法：① 局部安全替换——提供 path、old_text（要被替换的【精确】旧片段）、new_text（替换后内容），工具在文件中唯一匹配处替换；② 整体重写——只提供 path 与 new_text（省略 old_text），但前提是你已用 read_file 读取过该文件。修改前请先 read_file 确认当前内容；.py 写入后会做语法校验，不通过自动回滚。Action Input 按多行格式写：第一行 path: <路径>，可选 old_text: <精确旧片段>，最后 new_text: <新内容（可多行）>。",
        "func": apply_edit,
    },
    "create_file": {
        "description": "在代码库内【新建】一个文件（不能覆盖已有文件，修改已有文件请用 apply_edit）。用于新增模块/分区（如新建 combat/crit.py）。受路径沙箱、单文件 200KB 上限、.py 语法校验约束；父目录不存在会自动创建（仍在 code_root 内）。新建前建议先用 search_code/grep 确认不会与已有实现重复（防堆叠）。Action Input 格式：第一行 path: <相对或绝对路径>，最后 new_text: <文件内容（可多行）>。",
        "func": create_file,
    },
    "run_command": {
        "description": "在代码库根目录内执行 shell 命令（如 pytest / npm run build / gradle test），返回合并后的标准输出与错误（截断 1500 字，超时 12s）。用于跑构建、跑测试、执行项目内命令来验证改动或查看结果。命令在 code_root 内执行，危险操作（rm -rf /、format、shutdown 等）会被拦截。输入为完整命令字符串。",
        "func": run_command,
    },
}
