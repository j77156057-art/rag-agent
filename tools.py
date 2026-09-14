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
import mcp_client

from config import TOP_K, CODE_COLLECTION_NAME, CODE_ROOT, get_runtime, set_runtime, edit_confirm_enabled, EXTERNAL_API_ALLOWLIST
from embeddings import EmbeddingClient
from vectorstore import query as vs_query, pretty_source
from ingest import _CODE_EXT, _SKIP_DIRS

_emb = None


def _get_emb():
    global _emb
    if _emb is None:
        _emb = EmbeddingClient()
    return _emb

def dev_mcp_call(arg):
    """Agent 受控调用已启用 MCP 连接器。输入 key/name/arguments(JSON)。"""
    root = get_runtime('code_root') or CODE_ROOT
    if not root: return 'MCP 调用失败：未配置代码库。'
    lines = str(arg or '').splitlines(); data={}; body=[]
    for line in lines:
        if ':' in line and not body:
            k,v=line.split(':',1); data[k.strip()]=v.strip()
        else: body.append(line)
    key=data.get('key',''); name=data.get('name',''); task_id=data.get('task_id','')
    if not key or not name: return 'MCP 调用失败：需要 key 和 name。'
    try:
        cfg=mcp_client.get_server_config(root,key)
        if not cfg.get('enabled'): return 'MCP 调用失败：连接器未启用，请先在工作台启用并审批。'
        if task_id:
            from game_workbench import list_tasks
            task=next((t for t in list_tasks(root) if str(t.get('id'))==str(task_id)),None)
            if not task:
                return 'MCP 调用失败：task_id 不存在。'
            raw_args=data.get('arguments','{}'); check_args=raw_args
            if isinstance(raw_args,str):
                try: check_args=json.loads(raw_args) if raw_args else {}
                except Exception: check_args={}
            paths=[]
            if isinstance(check_args,dict):
                for k,v in check_args.items():
                    if k.lower() in ('path','file','scene','asset','script') and isinstance(v,str): paths.append(v.replace('res://','').lstrip('/'))
            if paths:
                from game_workbench import validate_task_scope
                scope=validate_task_scope(root,{**task,'files':paths})
                if not scope.get('ok'): return 'MCP 调用失败：参数路径超出任务分区。'
        args=data.get('arguments','{}')
        if isinstance(args,str): args=json.loads(args) if args else {}
        return json.dumps(mcp_client.call_tool(root,key,name,args), ensure_ascii=False)[:6000]
    except Exception as e: return f'MCP 调用失败：{e}'


def _dedup_docs(docs, metas):
    """向量检索偶发把同一 chunk 返回多次（同一 source+文本出现 N 遍，曾出现 4 次同片）。

    用 (source, 规范化文本) 作去重键只保留首次出现，避免重复片段浪费 token、
    并防止 Agent 误以为"信息很多"而空转。返回过滤后的 (docs, metas)。"""
    seen = set()
    out_d, out_m = [], []
    for d, m in zip(docs, metas):
        key = (m.get("source", "") if m else "", (d or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out_d.append(d)
        out_m.append(m)
    return out_d, out_m


def search_knowledge(query):
    """在已上传的知识库中检索与问题相关的文档片段。"""
    emb = _get_emb().embed([query])[0]
    res = vs_query(emb, k=TOP_K)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    docs, metas = _dedup_docs(docs, metas)
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

def web_fetch(url):
    """读取公开网页正文的简化研究工具，返回标题、来源和清理后的文本。"""
    ok, _ = _url_scheme_ok(url)
    if not ok: return "网页读取失败：只允许 http/https。"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (DocMind research)"})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(1_000_000).decode('utf-8','replace')
            final_url = r.geturl() or url
            content_type = r.headers.get('Content-Type', '')
        if 'html' not in content_type.lower() and '<html' not in raw[:500].lower():
            return f"来源：{final_url}\n内容类型：{content_type or '未知'}\n网页正文读取器仅支持 HTML 页面。"
        title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.I|re.S)
        text = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', raw, flags=re.I|re.S)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        clean_title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else '未知'
        clipped = len(text) > 8000
        return f"来源：{final_url}\n标题：{clean_title}\n正文：{text[:8000]}" + ("\n[正文已截断]" if clipped else "")
    except Exception as e: return f"网页读取失败：{type(e).__name__}: {e}"


_ALLOWED_URL_SCHEMES = ("http", "https")


def _url_scheme_ok(url):
    """只允许 http/https：白名单配 * 时也要阻止 file:// 读本地文件、gopher/ftp 等协议。"""
    try:
        scheme = (urllib.parse.urlparse(url).scheme or "").lower()
    except Exception:
        return False, ""
    return scheme in _ALLOWED_URL_SCHEMES, scheme


def _host_allowed(url):
    """按 EXTERNAL_API_ALLOWLIST 校验 url 的 host（防 SSRF）。空白名单一律拒绝。

    支持三种写法（与文档一致）：
      *                          放行任意 host（scheme 仍限 http/https）
      api.example.com            精确匹配，同时匹配其子域
      *.example.com              仅匹配子域（含多级，如 a.b.example.com），不含裸 example.com
    """
    if not EXTERNAL_API_ALLOWLIST:
        return False, "未配置 EXTERNAL_API_ALLOWLIST，dev_http_request 已禁用（请在启动环境设置白名单，如 EXTERNAL_API_ALLOWLIST=api.example.com,*.example.com）。"
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False, f"无法解析 URL：{url}"
    host = host.lower()
    for raw in EXTERNAL_API_ALLOWLIST:
        pat = (raw or "").strip().lower()
        if not pat:
            continue
        if pat == "*":
            return True, ""
        if pat.startswith("*."):
            suffix = pat[1:]  # ".example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True, ""
            continue
        if host == pat or host.endswith("." + pat):
            return True, ""
    return False, f"目标 host「{host}」不在 EXTERNAL_API_ALLOWLIST 白名单内，已拒绝（当前白名单：{', '.join(EXTERNAL_API_ALLOWLIST)}）。"


class _GuardRedirectHandler(urllib.request.HTTPRedirectHandler):
    """30x 跳转时对每一跳重新做 scheme + host 白名单校验。

    urllib 默认自动跟随重定向且不重新校验目标，白名单域若存在开放重定向，
    一跳即可访问 127.0.0.1 / 169.254.169.254 / 内网甚至跳到 file://，
    把响应内容回显给 Agent，等于绕过 SSRF 白名单。
    """

    # 301/303/307 在基类里都是 http_error_302 的别名；子类里重新绑定，确保全部走守卫
    def http_error_302(self, req, fp, code, msg, hdrs):
        # 注意：Python 3.13 起基类在调用 redirect_request 之前就会自行拒绝非 http(s)
        # 跳转（抛 HTTPError），这里提前校验是为了拿到统一、可读的拦截原因，
        # 并保证所有 3xx 状态码都先过本方法。
        loc = hdrs.get("location") or hdrs.get("uri") or ""
        newurl = urllib.parse.urljoin(req.full_url, loc)
        scheme_ok, _ = _url_scheme_ok(newurl)
        if not scheme_ok:
            raise urllib.error.URLError(f"重定向目标协议不允许（仅 http/https）：{newurl}")
        allowed, why = _host_allowed(newurl)
        if not allowed:
            raise urllib.error.URLError(f"重定向目标未通过白名单：{why}")
        return super().http_error_302(req, fp, code, msg, hdrs)

    http_error_301 = http_error_303 = http_error_307 = http_error_302

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        # 纵深防御：即便未来 Python 版本改变 302 处理链，这里仍逐跳复核
        scheme_ok, _ = _url_scheme_ok(newurl)
        if not scheme_ok:
            raise urllib.error.URLError(f"重定向目标协议不允许（仅 http/https）：{newurl}")
        allowed, why = _host_allowed(newurl)
        if not allowed:
            raise urllib.error.URLError(f"重定向目标未通过白名单：{why}")
        return super().redirect_request(req, fp, code, msg, hdrs, newurl)


def dev_http_request(arg):
    """让 Agent 调用你自己的外部业务 API（REST/JSON）。

    受 EXTERNAL_API_ALLOWLIST 域名白名单约束（防止对内网/元数据地址做 SSRF），
    未配置白名单时本工具拒绝任何请求。输入（多行 key: value）：
        url: <必填，完整 URL>
        method: <GET|POST|PUT|PATCH|DELETE，默认 GET>
        timeout: <秒，默认 15>
        headers: <可选，单行 JSON 对象，如 {"Authorization":"Bearer x"}>
        body: <可选，请求体；与 method 配合，POST/PUT/PATCH 常用；可多行>
    返回：HTTP 状态码 + 响应头(部分) + 截断后的响应体（前 4000 字）。网络/解析错误会说明原因。
    """
    arg = (arg or "").lstrip("\n")
    keys = ["url", "method", "timeout", "headers", "body"]
    spans = []
    for key in keys:
        for m in re.finditer(r"^\s*" + key + r"\s*:\s*", arg, re.M):
            spans.append((m.start(), m.end(), key))
    spans.sort()
    fields = {}
    for i, (s, e, key) in enumerate(spans):
        val_end = spans[i + 1][0] if i + 1 < len(spans) else len(arg)
        val = arg[e:val_end]
        if val.startswith("\n"):
            val = val[1:]
        if val_end < len(arg):
            val = val.rstrip("\n")
        fields[key] = val

    url = (fields.get("url") or "").strip()
    if not url:
        return "参数缺失：请提供 url: <完整 URL>。"
    scheme_ok, scheme = _url_scheme_ok(url)
    if not scheme_ok:
        return f"安全拦截：仅允许 http/https 协议，拒绝「{scheme or '未知'}」。"
    ok, why = _host_allowed(url)
    if not ok:
        return f"安全拦截：{why}"

    method = (fields.get("method") or "GET").strip().upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return f"不支持的 HTTP 方法：{method}。"
    try:
        timeout = float((fields.get("timeout") or "15").strip())
    except ValueError:
        return "timeout 参数不是合法数字（秒）。"
    timeout = max(1.0, min(timeout, 60.0))

    headers = {}
    raw_h = (fields.get("headers") or "").strip()
    if raw_h:
        try:
            headers = json.loads(raw_h)
            if not isinstance(headers, dict):
                return "headers 必须是 JSON 对象（如 {\"Authorization\":\"Bearer x\"}）。"
        except Exception as e:  # noqa: BLE001
            return f"headers 解析失败（需单行 JSON 对象）：{e}"

    body = fields.get("body")
    data = None
    if body is not None and body != "" and method in ("POST", "PUT", "PATCH"):
        data = body.encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    try:
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        # 使用带跳转守卫的 opener：302 每一跳都重过 scheme + host 白名单
        opener = urllib.request.build_opener(_GuardRedirectHandler)
        with opener.open(req, timeout=timeout) as r:
            status = r.status
            resp_body = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        try:
            resp_body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            resp_body = ""
        return f"HTTP {e.code} {e.reason}\n{resp_body[:4000]}"
    except Exception as e:  # noqa: BLE001
        return f"请求失败: {type(e).__name__}: {e}"

    preview = resp_body[:4000]
    tail = "" if len(resp_body) <= 4000 else f"\n…（截断，共 {len(resp_body)} 字）"
    return f"HTTP {status}  content-type: {ctype}\n{preview}{tail}"


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
    # 打包版 sys.executable 是 DocMind.exe（onedir 内无 python.exe），
    # 不能拿它当解释器跑代码（只会再启动一个应用实例）。
    if getattr(sys, "frozen", False):
        return "分发版（DocMind.exe）未内置 Python 解释器，python_exec 仅在源码/venv 环境可用。"
    # 弱模型常把多行代码写成单行、换行用字面量 \n/\t（Action Input 是单行字段）；
    # 首次编译失败且代码里没有真实换行时，反转义一次再执行（正常代码不受影响）。
    try:
        compile(code, "<python_exec>", "exec")
    except SyntaxError:
        if "\\n" in code and "\n" not in code:
            code = code.replace("\\n", "\n").replace("\\t", "\t")
    # 已索引代码库时在代码根目录内执行：脚本里的相对路径（如 open("app/src/...")）
    # 才能按项目语义解析；未配置代码库时维持旧行为（服务端目录）。
    cwd = _get_code_root() or os.path.dirname(__file__)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=12,
            cwd=cwd,
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


def _region_write_allowed(target):
    """When regions are configured, ordinary write tools cannot bypass region tools."""
    if get_runtime("region_edit_context"):
        return True
    root = _get_code_root()
    if not root or not os.path.isfile(os.path.join(root, "regions.json")):
        return True
    try:
        from regions import get_region_map
        target = os.path.realpath(target)
        for meta in get_region_map(root).values():
            base = os.path.realpath(os.path.join(root, meta["dir"]))
            if target == base or target.startswith(base + os.sep):
                return False
    except Exception:
        return False
    return True


# 记录 Agent 本次会话内已用 read_file 读过的文件（绝对路径，normcase 归一），
# 用于 apply_edit 的"先读后写"安全护栏：未确认过内容的文件不允许整体重写。
_READ_FILES = set()
_REGION_LOCKS = {}
_REGION_LOCKS_GUARD = threading.Lock()


def _region_lock(key):
    with _REGION_LOCKS_GUARD:
        return _REGION_LOCKS.setdefault(key, threading.RLock())


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


def _clean_search_query(q):
    """语义检索 query 只"剥壳"、不抽符号：保留多词自然语言/中英混合的完整语义。

    _clean_symbol 会把「collision 碰撞检测逻辑」缩成单个标识符，那是给 grep 正则用的；
    语义向量检索需要完整 query（如「score combo high score save」是合法英文短句）。
    这里仅去掉弱模型常误带的 query:/path:/关键词: 前缀与成对围栏、引号。
    """
    q = (q or "").strip()
    q = re.sub(
        r"^(?:query|q|path|关键词|检索词|搜索词)\s*[:：]\s*",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    if len(q) >= 2 and q[0] == q[-1] and q[0] in "`'\"":
        q = q[1:-1].strip()
    return q


# 符号种类 -> 检索结果标签动词（function 按语言再区分 def/func）
_SYMBOL_KW = {
    "class": "class", "signal": "signal", "enum": "enum",
    "const": "const", "var": "var",
}


def search_code(query):
    """在已索引的源代码/配置中检索相关函数、类、配置片段。"""
    if not _get_code_root():
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录后再问代码相关问题。"
    query = _clean_search_query(query)
    emb = _get_emb().embed([query])[0]
    res = vs_query(emb, k=TOP_K, collection=CODE_COLLECTION_NAME)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    docs, metas = _dedup_docs(docs, metas)
    if not docs:
        return "代码库未找到相关内容，建议改用 grep 搜索关键词或 read_file 查看具体文件。"
    out = []
    for d, m in zip(docs, metas):
        src = m.get("source", "?")
        sym = m.get("symbol", "")
        lang = m.get("lang", "")
        kind = m.get("kind", "code")
        doc = m.get("doc", "")
        sl, el = m.get("start_line"), m.get("end_line")
        loc = f"{src}:L{sl}" + (f"-L{el}" if el and el != sl else "") if sl else src
        kw = _SYMBOL_KW.get(kind)
        verb = ("def" if lang == "python" else "func") if kind == "function" else kw
        if sym and verb:
            label = f"{loc} › {verb} {sym}"
        elif sym:
            label = f"{loc} › {sym}"
        else:
            label = loc
        if lang:
            label += f" ({lang})"
        shown = d if len(d) <= 600 else d[:600].rstrip() + "\n…（下略）"
        # 给片段逐行标行号（锚定 start_line），模型可直接引用精确行
        if sl:
            numbered = []
            for i, ln in enumerate(shown.split("\n")):
                if ln == "…（下略）":
                    numbered.append(ln)
                else:
                    numbered.append(f"{sl + i:>5}| {ln}")
            shown = "\n".join(numbered)
        if doc:
            shown = f"# 文档: {doc.splitlines()[0]}\n" + shown
        out.append(f"[{label}]\n{shown}")
    return "\n---\n".join(out)


def read_file(path):
    """读取代码库中的文件内容（path 为相对 code_root 的路径或文件名）。

    大文件默认只给前 4000 字；可在输入里附 `start: <1基行号>` 与可选 `end: <行号>`
    只看某个区间（如枚举/方法所在行段），格式：路径换行后接 start:/end: 两行。
    """
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    root_abs = os.path.normpath(root)
    start_line = end_line = None
    ms = re.search(r"(?:^|\n)\s*start\s*[:：]\s*(\d+)", path)
    me = re.search(r"(?:^|\n)\s*end\s*[:：]\s*(\d+)", path)
    cuts = []
    if ms:
        start_line = max(1, int(ms.group(1)))
        cuts.append(ms.start())
    if me:
        end_line = max(1, int(me.group(1)))
        cuts.append(me.start())
    if cuts:
        path = path[:min(cuts)]
    path = path.strip()
    if start_line and end_line and end_line < start_line:
        end_line = start_line + 200
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
    rel = os.path.relpath(target, root_abs)
    if start_line:
        all_lines = content.splitlines(keepends=True)
        if not end_line:
            end_line = start_line + 199  # 区间默认 200 行
        end_line = min(end_line, len(all_lines))
        picked = all_lines[start_line - 1:end_line]
        shown = "".join(f"{i}: {ln}" for i, ln in enumerate(picked, start=start_line))
        return f"=== {rel}（第 {start_line}-{end_line} 行，共 {len(all_lines)} 行）===\n{shown}"
    if len(content) > 4000:
        content = content[:4000] + "\n…（已截断，仅显示前 4000 字；需要后续段落请用 start:/end: 指定行号）"
    return f"=== {rel} ===\n{content}"


_RE_GREP_SCOPE_LINE = re.compile(r"^\s*path\s*[:：]\s*(.+?)\s*$", re.I)
_RE_GREP_SCOPE_INLINE = re.compile(r"\s*[,，]\s*path\s*[:：]\s*(.+?)\s*$", re.I)
_RE_GREP_PATTERN_KEY = re.compile(r"^\s*(?:pattern|regex|p)\s*[:：]\s*(.+)$", re.I)


def _strip_arg_quotes(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        return text[1:-1].strip()
    return text


def grep(pattern):
    """在代码库中按正则搜索文本/符号，返回匹配的文件路径与行号。

    可在输入末尾用 path: 限定搜索范围（相对 code_root 的文件或目录），避免全仓扫描：
      某正则
      path: app/src/main/java/.../A.java
    或一行内：某正则, path: app/src/main/java/...（目录）
    """
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    # 解析可选 path: 作用域（独立行优先），剩余文本才是正则
    scope_rel = None
    kept = []
    for i, ln in enumerate((pattern or "").splitlines()):
        ms = _RE_GREP_SCOPE_LINE.match(ln)
        if ms and i >= 1:
            scope_rel = _strip_arg_quotes(ms.group(1))
        else:
            kept.append(ln)
    pattern = "\n".join(kept).strip()
    mi = _RE_GREP_SCOPE_INLINE.search(pattern)
    if mi:
        scope_rel = _strip_arg_quotes(mi.group(1))
        pattern = pattern[:mi.start()].strip()
    mp = _RE_GREP_PATTERN_KEY.match(pattern)
    if mp and "\n" not in pattern:
        pattern = _strip_arg_quotes(mp.group(1))
    pattern = _clean_symbol(pattern)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"正则错误: {e}"
    root_abs = os.path.normpath(root)

    def _resolve_scope(rel):
        target = os.path.normpath(os.path.join(root_abs, rel))
        if not (target == root_abs or target.startswith(root_abs + os.sep)):
            alt = os.path.normpath(os.path.join(root_abs, rel.lstrip("./\\")))
            if os.path.exists(alt) and (alt == root_abs or alt.startswith(root_abs + os.sep)):
                target = alt
            else:
                return None, f"拒绝访问：{rel} 不在代码根目录内。"
        if not os.path.exists(target):
            return None, f"路径不存在：{rel}"
        return target, None

    scope_abs = None
    if scope_rel:
        scope_abs, err = _resolve_scope(scope_rel)
        if err:
            return err

    def _iter_candidate_files():
        if scope_abs and os.path.isfile(scope_abs):
            if os.path.splitext(scope_abs)[1].lower() in _CODE_EXT:
                yield scope_abs
            return
        for dp, dns, fns in os.walk(scope_abs or root_abs):
            dns[:] = [d for d in dns if d not in _SKIP_DIRS]
            for fn in fns:
                if os.path.splitext(fn)[1].lower() not in _CODE_EXT:
                    continue
                yield os.path.join(dp, fn)

    hits = []
    for fp in _iter_candidate_files():
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
    if not hits:
        scope_note = f"（范围：{scope_rel}）" if scope_rel else ""
        return f"代码库中未匹配到：{pattern}{scope_note}"
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
    if not _region_write_allowed(target):
        return "拒绝写入：该路径属于已配置分区，请使用 dev_region_edit 以确保分区边界和先读后写护栏。"
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
    if not _region_write_allowed(target):
        return "拒绝写入：该路径属于已配置分区，请使用 dev_region_edit 以确保分区边界和先读后写护栏。"
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


# git 写操作子命令：必须走分区审批门禁（dev_commit / dev_commit_all / dev_rollback），
# 不能经 run_command 直接执行——否则 Agent 可绕过审批、变更集台账与回滚链，
# push 还会把代码外带，与黑名单"防数据外带"的初衷矛盾。只读子命令（status/log/diff/show 等）放行。
_GIT_OPTS_WITH_VALUE = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix", "--list-submodules",
}
_GIT_WRITE_SUBCMDS = {
    "commit", "push", "merge", "rebase", "revert", "reset", "clean",
    "checkout", "switch", "apply", "cherry-pick", "stash", "am",
    "update-ref", "worktree",
}


def _git_subcommand(low):
    """从 `git ... <sub>` 命令中解析真正的子命令，跳过 -C <path> 等带值选项。

    识别不了（如自定义 alias git co）时返回 ''，由调用方按默认策略处理。
    """
    toks = re.sub(r"\s+", " ", low).strip().split(" ")
    if not toks or _first_command_token(low) != "git":
        return ""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in _GIT_OPTS_WITH_VALUE:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t
    return ""


def _cmd_is_blocked(cmd):
    """返回 (True, 原因) 表示应拦截；否则 (False, '')。"""
    low = re.sub(r"\s+", " ", cmd.lower()).strip()
    for pat in _DANGEROUS_PATTERNS:
        if pat in low:
            return True, pat
    first = _first_command_token(low)
    # git 写操作门禁优先于可选白名单：即使用户把 git 加进 RUN_COMMAND_ALLOW 也不放行
    if first == "git":
        sub = _git_subcommand(low)
        if sub in _GIT_WRITE_SUBCMDS:
            return True, (f"git {sub}（git 写操作受分区审批门禁保护，"
                          "请改用 dev_commit / dev_commit_all / dev_rollback）")
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


def init_regions_tool(arg):
    """初始化「分区开发」结构：在代码库根目录建 assets/values/bugs/behaviors 四个独立子目录，
    各 git init 独立仓库，并生成 DEV_INDEX.md 与 DOCMIND_RULES.md（分区契约，会被注入 Agent 系统提示）。
    用于游戏等分工开发，防止代码堆叠。输入留空即可。"""
    from regions import init_regions
    ok, msg = init_regions()
    return msg


# ---------------------------------------------------------------------------
# 分区开发工具层（2.0）：分区内受控读写 / 分区校验 / 跨区安全搬移 / 提交与变更集回滚。
# 所有路径都限定在对应分区子目录内，越区写被拦截（强化"防堆叠/防混乱"）。
# 写操作复用 apply_edit / create_file 的全部护栏（先读后写、.py 语法校验、200KB 上限、人工确认）。
# ---------------------------------------------------------------------------
def _parse_keyed(arg, keys):
    """通用 keyed 解析：从多行文本里提取指定字段（region/path/old_text/new_text/...）。
    字段值可同行或换行续写、可多行；用于分区工具输入解析，逻辑与 _parse_edit_input 一致。"""
    arg = (arg or "").lstrip("\n")
    spans = []
    for key in keys:
        for m in re.finditer(r"^\s*" + re.escape(key) + r"\s*:\s*", arg, re.M):
            spans.append((m.start(), m.end(), key))
    spans.sort()
    fields = {}
    for i, (s, e, key) in enumerate(spans):
        val_end = spans[i + 1][0] if i + 1 < len(spans) else len(arg)
        val = arg[e:val_end]
        if val.startswith("\n"):
            val = val[1:]
        if val_end < len(arg):
            val = val.rstrip("\n")
        fields[key] = val
    return fields


def _require_regions():
    """返回 (root, rmap) 或 (None, error_msg)。rmap = {key: region_meta}。"""
    root = _get_code_root()
    if not root:
        return None, "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    try:
        from regions import get_region_map
        rmap = get_region_map(root)
    except Exception as e:  # noqa: BLE001
        return None, f"读取分区配置失败: {e}"
    if not rmap:
        return None, "尚未初始化分区（regions.json 为空）。请先调用 init_regions 初始化分区开发结构。"
    return (root, rmap), None


def _resolve_region_path(root, rmap, region_key, path):
    """把 path 解析到某分区目录内的绝对路径；越界返回 (None, reason)。
    允许 path 以分区目录开头（如 values/balance.json）或纯文件名（如 balance.json）。"""
    if region_key not in rmap:
        return None, f"未知分区：{region_key}（可选分区：{', '.join(rmap.keys())}）"
    region_abs = os.path.normpath(os.path.join(root, rmap[region_key]["dir"]))
    p = (path or "").strip().strip("'\"")
    if os.path.isabs(p):
        rel = os.path.relpath(os.path.normpath(p), root)
    else:
        rel = p
    rel = rel.replace("\\", "/")
    rdir = rmap[region_key]["dir"]
    if rel == rdir:
        rel = ""
    elif rel.startswith(rdir + "/"):
        rel = rel[len(rdir) + 1:]
    target = os.path.normpath(os.path.join(region_abs, rel)) if rel else region_abs
    if target != region_abs and not target.startswith(region_abs + os.sep):
        return None, f"拒绝写入：{path} 不在分区 {rdir}/ 内（禁止越区写）。"
    return target, None


def _emit_edit_arg(abs_path, old_text, new_text):
    """拼装给 apply_edit / create_file 的多行输入（复用其护栏：先读后写/.py 校验/人工确认/越区防护）。"""
    parts = ["path: " + abs_path]
    if old_text is not None:
        parts.append("old_text: " + old_text)
    parts.append("new_text: " + new_text)
    return "\n".join(parts)


def dev_list_regions(arg):
    """列出已配置分区的 key/名称/目录/依赖/导出/脏状态，供 Agent 选用正确分区。输入留空即可。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    from regions import list_regions
    rows = list_regions(root)
    lines = [f"已配置 {len(rows)} 个分区（code_root={root}）："]
    for r in rows:
        deps = ", ".join(r["depends_on"]) or "无"
        exports = ", ".join(r["exports"]) or "无"
        dirty = "（有未提交改动）" if r["dirty"] else ""
        lines.append(f"- {r['key']}｜{r['name']}（{r['dir']}/）：{r['desc']}；依赖：{deps}；导出：{exports}{dirty}")
    return "\n".join(lines)


def dev_region_read(arg):
    """读取某分区内的文件（分区作用域，越区读被拒）。
    输入：region: <分区key> 换行 path: <分区内相对路径>。
    读取后该文件可被 dev_region_edit 整体重写（满足先读后写护栏）。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region", "path"])
    region, path = (f.get("region") or "").strip(), (f.get("path") or "").strip()
    if not region or not path:
        return "参数缺失：请提供 region: <分区key> 与 path: <分区内相对路径>。"
    target, reason = _resolve_region_path(root, rmap, region, path)
    if target is None:
        return reason
    return read_file(target)


def dev_region_edit(arg):
    """受控修改/新建某分区内的文件（分区作用域，越区写被拒；复用 apply_edit/create_file 的全部护栏）。
    两种用法：① 局部安全替换——提供 region、path、old_text（精确旧片段）、new_text；
    ② 整体重写——提供 region、path、new_text（省略 old_text），前提是你已用 dev_region_read 读过该文件。
    若路径文件已存在则按修改处理，不存在则按新建处理。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region", "path", "old_text", "new_text"])
    region = (f.get("region") or "").strip()
    path = (f.get("path") or "").strip()
    old_text = f.get("old_text")
    new_text = f.get("new_text")
    if not region or not path:
        return "参数缺失：请提供 region: <分区key> 与 path: <分区内相对路径>。"
    if new_text is None:
        return "参数缺失：请提供 new_text: <新内容>（局部替换还需 old_text: <精确旧片段>）。"
    target, reason = _resolve_region_path(root, rmap, region, path)
    if target is None:
        return reason
    lock = _region_lock(region)
    with lock:
        set_runtime("region_edit_context", True)
        try:
            if os.path.isfile(target):
                return apply_edit(_emit_edit_arg(target, old_text, new_text))
            return create_file(_emit_edit_arg(target, None, new_text))
        finally:
            set_runtime("region_edit_context", False)


def _run_region_cmd(region_dir_abs, cmd):
    """在分区目录内执行校验命令（受安全黑名单约束，超时 30s，输出截断 1500 字）。"""
    cmd = (cmd or "").strip().strip("'\"")
    if not cmd:
        return "未提供校验命令。"
    blocked, why = _cmd_is_blocked(cmd)
    if blocked:
        return f"拒绝执行：校验命令被安全策略拦截（命中「{why}」）。"
    try:
        proc = subprocess.run(cmd, shell=True, cwd=region_dir_abs, timeout=30,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return "校验命令执行超时（>30s）。"
    except Exception as e:  # noqa: BLE001
        return f"执行失败: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    head = f"[exit code {proc.returncode}]\n"
    return head + (out[:1500] + ("…" if len(out) > 1500 else ""))


def dev_region_verify(arg):
    """校验单个分区：若该分区配置了 verify 命令则在分区目录内执行；否则检查其导出接口文件是否齐全。
    输入：region: <分区key>。返回命令执行情况或契约自检结果。
    说明：verify 命令建议写为脚本文件（如 `pytest tests/`）而非 `python -c ...`（后者会被安全策略拦截）。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region"])
    region = (f.get("region") or "").strip()
    if not region:
        return "参数缺失：请提供 region: <分区key>。"
    if region not in rmap:
        return f"未知分区：{region}（可选分区：{', '.join(rmap.keys())}）"
    meta = rmap[region]
    region_abs = os.path.normpath(os.path.join(root, meta["dir"]))
    verify_cmd = (meta.get("verify") or "").strip()
    if verify_cmd:
        from regions import run_verify
        ok, output = run_verify(region_abs, verify_cmd)
        if ok is not None or output is not None:
            return f"运行 {meta['name']} 的内置校验（{verify_cmd}）：\n{output}"
        return f"运行 {meta['name']} 的 verify 命令：`{verify_cmd}`\n" + _run_region_cmd(region_abs, verify_cmd)
    exports = meta.get("exports") or []
    if not exports:
        return f"{meta['name']} 未配置 verify 命令，也无导出接口需校验，跳过（OK）。"
    miss = [ex for ex in exports if not os.path.isfile(os.path.join(region_abs, ex))]
    if miss:
        return f"{meta['name']} 导出接口缺失：{', '.join(miss)}（契约校验失败）。"
    return f"{meta['name']} 导出接口齐全（{', '.join(exports)}），契约自检通过（OK）。"


def dev_refactor(arg):
    """跨分区安全搬移：把某分区内的文件移动到另一分区（受依赖方向约束，不破坏 git 跟踪）。
    输入：
      src_region: <源分区key>
      src_path: <源文件在源分区内的相对路径>
      dst_region: <目标分区key>
      dst_path: <目标文件在目标分区内的相对路径>
    规则：目标文件不能已存在（不覆盖）；搬移后从源分区 git 仓库移除源文件（保留历史）。
    依赖方向：仅允许移动到 src 依赖的分区（dst ∈ src.depends_on）或前后无关的分区；
    禁止把代码挪进「已依赖 src」的分区（sr ∈ dst.depends_on），避免循环耦合 / 倒置分层。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["src_region", "src_path", "dst_region", "dst_path"])
    sr, sp = (f.get("src_region") or "").strip(), (f.get("src_path") or "").strip()
    dr, dp = (f.get("dst_region") or "").strip(), (f.get("dst_path") or "").strip()
    if not (sr and sp and dr and dp):
        return "参数缺失：请提供 src_region/src_path/dst_region/dst_path。"
    if sr not in rmap or dr not in rmap:
        return f"未知分区：src={sr} dst={dr}（可选：{', '.join(rmap.keys())}）"
    src_deps = set(rmap[sr].get("depends_on") or [])
    # 依赖方向校验：禁止把 src 的代码挪进「已依赖 src」的分区（倒置分层 → 潜在循环耦合）
    if dr != sr and dr not in src_deps and sr in set(rmap[dr].get("depends_on") or []):
        return (f"拒绝搬移：{sr} → {dr} 会形成反向依赖（{dr} 已依赖 {sr}），"
                f"把 {sr} 的代码挪进 {dr} 会破坏依赖方向、可能引发循环耦合。"
                f"请改放到 {sr} 依赖的分区之一（{', '.join(src_deps) or '无'}），或做解耦重组。")
    src_target, reason = _resolve_region_path(root, rmap, sr, sp)
    if src_target is None:
        return reason
    if not os.path.isfile(src_target):
        return f"源文件不存在：{sp}（在分区 {sr} 内）。"
    dst_target, reason = _resolve_region_path(root, rmap, dr, dp)
    if dst_target is None:
        return reason
    if os.path.exists(dst_target):
        return f"目标文件已存在：{dp}（在分区 {dr} 内），dev_refactor 不覆盖，请先处理目标。"
    try:
        with open(src_target, encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except Exception as e:  # noqa: BLE001
        return f"读取源文件失败: {e}"
    create_res = create_file(_emit_edit_arg(dst_target, None, content))
    # 「人工确认」模式：仅暂存、未落盘 → 不要动源文件，等用户确认后再搬
    if "待人工确认" in create_res:
        return (f"目标已暂存、待人工确认，源文件未改动。请在界面确认写入后重新调用本工具，"
                f"届时本工具会从 {sr} 移除源文件。\n{create_res}")
    if not create_res.startswith("已创建"):
        return f"目标创建失败，已中止搬移：{create_res}"
    # 真正创建成功 → 从源分区 git 移除源文件（保留历史）；未跟踪则直接删除
    src_region_abs = os.path.normpath(os.path.join(root, rmap[sr]["dir"]))
    if os.path.isdir(os.path.join(src_region_abs, ".git")):
        rel_src = os.path.relpath(src_target, src_region_abs)
        proc = subprocess.run(
            ["git", "rm", "-q", rel_src], cwd=src_region_abs,
            capture_output=True, text=True,
        )
        if proc.returncode != 0 and "not found" in (proc.stdout or proc.stderr or "") \
                and os.path.isfile(src_target):
            try:
                os.remove(src_target)
            except Exception:
                pass
    elif os.path.isfile(src_target):
        try:
            os.remove(src_target)
        except Exception as e:  # noqa: BLE001
            return f"目标已创建，但删除源文件失败：{e}（请手动清理 {src_target}）"
    return f"已安全搬移 {sr}/{sp} → {dr}/{dp}（目标已创建，源文件从 {sr} 移除）。\n{create_res}"


def dev_commit(arg):
    """提交单个分区的改动（该分区独立 git 仓库内 commit）。
    输入：region: <分区key> 换行 message: <提交说明>。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region", "message"])
    region = (f.get("region") or "").strip()
    message = (f.get("message") or "docmind: update").strip() or "docmind: update"
    if not region:
        return "参数缺失：请提供 region: <分区key>。"
    from regions import commit_region
    ok, out = commit_region(root, region, message)
    return ("已提交" if ok else "提交失败") + f" 分区 {region}：" + out


def dev_verify_contracts(arg):
    """校验全部分区的契约：依赖方向无环、被依赖分区导出文件存在、依赖目标存在。
    输入留空即可。建议在大幅改动分区前后调用。返回 ok/错误列表/依赖图。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    from regions import verify_contracts
    r = verify_contracts(root)
    if r["ok"]:
        return "契约校验通过（依赖方向无环、依赖目标与导出接口均存在）。\n依赖图：" + json.dumps(r["graph"], ensure_ascii=False)
    return "契约校验失败：\n- " + "\n- ".join(r["errors"]) + "\n依赖图：" + json.dumps(r["graph"], ensure_ascii=False)


def dev_rebuild_index(arg):
    """依据当前分区配置重算 DEV_INDEX.md（分区目录/文件数/依赖变动后调用）。输入留空即可。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    from regions import rebuild_dev_index
    ok, msg = rebuild_dev_index(root)
    return msg


def dev_commit_all(arg):
    """把所有分区的改动各提交一次，并记进 dev_changesets.jsonl 作为一次绑定变更集（可整体回滚）。
    输入：message: <本次功能改动说明>。返回变更集 id 与各分区提交哈希。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg, ["message"])
    message = (f.get("message") or "docmind: update").strip() or "docmind: update"
    from regions import commit_all
    ok, info = commit_all(root, message)
    if not ok:
        return "变更集提交失败：\n" + "\n".join(f"- {k}: {v}" for k, v in (info.get("errors") or {}).items())
    cs_id = info.get("id", "")
    commits = info.get("commits", {})
    if not commits:
        return info.get("message", "没有可提交的分区改动。")
    lines = [f"已创建变更集 {cs_id}（message={message}）："]
    for k, v in commits.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) + f"\n（回滚请调用 dev_rollback_changeset，changeset={cs_id}）"


def dev_list_changesets(arg):
    """列出已记录的跨区变更集（dev_changesets.jsonl）。输入留空即可。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    from regions import list_changesets
    cs = list_changesets(root)
    if not cs:
        return "暂无已记录的变更集。用过 dev_commit_all 后会在此列出。"
    lines = [f"共 {len(cs)} 个变更集："]
    for c in cs:
        lines.append(f"- id={c.get('id')} message={c.get('message','')} 分区={list((c.get('commits') or {}).keys())}")
    return "\n".join(lines)


def dev_rollback_changeset(arg):
    """整体回滚某变更集：对每个分区 revert 其记录的 commit（生成新提交撤销改动）。
    输入：changeset: <变更集id> 或 id: <变更集id>。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg, ["changeset", "id"])
    cs_id = (f.get("changeset") or f.get("id") or "").strip()
    if not cs_id:
        return "参数缺失：请提供 changeset: <变更集id>（先用 dev_list_changesets 查看）。"
    from regions import rollback_changeset
    ok, detail = rollback_changeset(root, cs_id)
    if not ok:
        return detail
    return f"已回滚变更集 {cs_id}：\n- " + "\n- ".join(detail)


# ---------------------------------------------------------------------------
# Agent 研判分区工具：勘察目录 → 获取基线建议 → 落地自定义方案 / 增补单分区。
# 默认 8 个分区仅作「初始建议」，真实分区由 Agent 依据代码库判断后应用。
# ---------------------------------------------------------------------------
def list_dir(arg):
    """浏览代码库内的目录结构（限定 code_root，供 Agent 研判代码库组织方式）。
    输入：可选 path: <相对 code_root 的目录，默认根目录>。返回该目录下子项（目录/文件）及大小/类型。
    这是 Agent 研判分区前的「勘察」工具：看清顶层有哪些模块/资源目录，再决定分区方案。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    f = _parse_keyed(arg or "", ["path"])
    rel = (f.get("path") or "").strip().strip("'\"")
    if not rel:
        # 弱模型常按 list_dir(behaviors/) 裸参数调用（内联括号参数剥引号后无 path: 前缀）。
        # 整段输入若不含 key: 字段标记，就当作相对目录路径，避免静默回退根目录导致空转。
        bare = (arg or "").strip().strip("'\"")
        if bare and not re.search(r"^\s*[A-Za-z_]\w*\s*:", bare, re.M):
            rel = bare
    target, root_abs = _resolve_in_root(rel if rel else root)
    if target is None:
        return f"拒绝访问：{rel} 不在代码根目录内。"
    if not os.path.isdir(target):
        return f"不是目录：{rel or '(根目录)'}（请用 list_dir 浏览目录，不要传文件路径）。"
    try:
        entries = sorted(os.listdir(target))
    except Exception as e:  # noqa: BLE001
        return f"读取目录失败: {e}"
    rows = []
    for name in entries:
        p = os.path.join(target, name)
        if os.path.isdir(p):
            try:
                n = sum(1 for _ in os.scandir(p))
            except Exception:
                n = 0
            rows.append(f"[DIR ] {name}/  ({n} 项)")
        else:
            try:
                sz = os.path.getsize(p)
            except Exception:
                sz = 0
            rows.append(f"[FILE] {name}  ({sz} 字节)")
    rel_disp = os.path.relpath(target, root_abs)
    rel_disp = "(代码库根)" if rel_disp in (".", "") else rel_disp
    header = f"目录 {rel_disp} 共 {len(rows)} 项："
    return header + "\n" + "\n".join(rows) if rows else header + "（空）"


def dev_propose_regions(arg):
    """依据真实代码库结构，由 Agent 研判分区方案（默认 8 区仅作初始建议）。输入留空即可。
    返回：结构分析 + 建议分区清单（每区带 detected 证据与 included 建议）+ 代码库特有的可独立模块 + 中文结论。
    用法：先用 list_dir 勘察 → 调用本工具获取基线方案 → 据 detected/included 增删分区 → 用 dev_apply_regions 落地。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    try:
        from regions import propose_regions
        res = propose_regions(root)
    except Exception as e:  # noqa: BLE001
        return f"研判分区失败: {e}"
    if not res.get("ok"):
        return res.get("error", "研判分区失败。")
    a = res["analysis"]
    lines = [res["summary"], ""]
    lines.append(f"结构分析：顶层目录 = {', '.join(a['top_level_dirs']) or '(无)'}")
    c = a["counts"]
    lines.append(f"资源计数：图像 {c['images']} / 音频 {c['audio']} / 模型 {c['models']} / 数据表 {c['data']} / 代码 {c['code']}")
    lines.append("")
    lines.append("建议分区（included=建议启用，detected=代码库检出信号）：")
    for r in res["proposed"]:
        flag = "✅启用" if r["included"] else "➖可选"
        det = "（已检出）" if r["detected"] else "（未检出）"
        ev = ("；证据：" + "、".join(r["evidence"])) if r["evidence"] else ""
        lines.append(f"- {r['key']}｜{r['name']}（{r['dir']}/） {flag}{det}：{r['reason']}{ev}")
    if res["custom_suggestions"]:
        lines.append("")
        lines.append("代码库特有的可独立分区（custom_suggestions，可据实际增删）：")
        for r in res["custom_suggestions"]:
            lines.append(f"- {r['key']}｜{r['name']}（{r['dir']}/）：{r['desc']}")
    lines.append("")
    lines.append("落地方式：把最终分区清单（JSON 数组，每项含 key/dir/name/depends_on 等）交给 dev_apply_regions 应用；"
                 "或仅追加一个分区用 dev_add_region。")
    return "\n".join(lines)


def dev_apply_regions(arg):
    """应用 Agent 研判后的自定义分区方案：把给定分区清单写入 regions.json 并初始化（建目录/每区 git/导出桩/规则）。
    输入：regions: <JSON 数组，或 {"regions":[...]} 对象>。每个分区至少含 key 与 dir；可选 name/desc/depends_on/exports/verify。
    这是把「Agent 判断的分区」落地的关键一步；应用后 Agent 写操作即被约束到这些分区内。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    f = _parse_keyed(arg or "", ["regions"])
    raw = (f.get("regions") or "").strip()
    if not raw:
        return "参数缺失：请提供 regions: <JSON 数组或 {\"regions\":[...]} 分区清单>。"
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return f"regions 不是合法 JSON：{e}。请传入 JSON 数组（可用 dev_propose_regions 的产出再裁减）。"
    if isinstance(data, dict) and isinstance(data.get("regions"), list):
        regions_list = data["regions"]
    elif isinstance(data, list):
        regions_list = data
    else:
        return "regions 格式应为 JSON 数组，或 {\"regions\":[...]} 对象。"
    from regions import init_regions
    ok, msg = init_regions(root, regions_list)
    if not ok:
        return "应用分区方案失败：" + msg
    # 重新注入分区契约，使 Agent 立即按新分区约束工作
    try:
        from ingest import load_project_rules
        rules = load_project_rules(os.path.abspath(root))
        set_runtime("project_rules", rules)
    except Exception:  # noqa: BLE001
        pass
    return "已应用 Agent 研判的分区方案。" + msg


def dev_add_region(arg):
    """向现有分区配置追加（或覆盖同名）一个分区，并立即初始化它（建目录/每区 git/规则）。
    输入：key: <分区key> 换行 dir: <目录> 换行 name: <中文名> 换行 [desc:] [access:] [depends_on:]（逗号分隔） [exports:]（逗号分隔）。
    用于 Agent 研判后按需增补单个分区，无需重传整个方案。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg or "", ["key", "dir", "name", "desc", "access", "depends_on", "exports"])
    key = (f.get("key") or "").strip()
    d = (f.get("dir") or "").strip()
    name = (f.get("name") or "").strip()
    if not key or not d:
        return "参数缺失：请提供 key: <分区key> 与 dir: <目录>（建议再给 name: <中文名>）。"
    from regions import load_region_config, init_regions
    cur = load_region_config(root)
    new_region = {
        "key": key,
        "dir": d.strip("/\\").replace("\\", "/"),
        "name": name or key,
        "desc": (f.get("desc") or "").strip(),
        "access": (f.get("access") or "").strip(),
        "depends_on": [x.strip() for x in (f.get("depends_on") or "").split(",") if x.strip()],
        "exports": [x.strip() for x in (f.get("exports") or "").split(",") if x.strip()],
        "verify": "",
    }
    cur = [r for r in cur if (r.get("key") or "").strip() != key]  # 覆盖同名
    cur.append(new_region)
    ok, msg = init_regions(root, cur)
    if not ok:
        return "新增/更新分区失败：" + msg
    try:
        from ingest import load_project_rules
        rules = load_project_rules(os.path.abspath(root))
        set_runtime("project_rules", rules)
    except Exception:  # noqa: BLE001
        pass
    return f"已新增/更新分区 {key}（{d}/）并写入 regions.json。" + msg


def dev_approve(arg):
    """审批敏感操作（提交/回滚/应用分区方案）前必须调用：记录一次审批，30 分钟内该操作放行。
    输入：action: <commit_region|commit_all|rollback_changeset|apply_regions> 换行 target: <对象>
    target 精确匹配、不是通配符：commit_region 传分区 key（逐区审批，不能用 *）、
    rollback_changeset 传变更集 id、commit_all / apply_regions 固定传 *。
    在调用 dev_commit / dev_commit_all / dev_rollback_changeset / dev_apply_regions / dev_add_region 之前先调用本工具完成审批。
    若这些工具返回 blocked / approval_required，先调用本工具再重试，不要绕过。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg or "", ["action", "target"])
    action = (f.get("action") or "").strip()
    target = (f.get("target") or "*").strip() or "*"
    if not action:
        return "参数缺失：请提供 action: <操作名>（如 commit_all / rollback_changeset / apply_regions）。"
    from game_workbench import approval
    approval(root, action, "agent", approved=True, target=target)
    return f"已审批 {action}(target={target})，30 分钟内该操作放行。现在可执行对应的 dev_* 工具。"


def dev_approval_status(arg):
    """查询某敏感操作当前是否已通过审批。输入：action: <操作名> 换行 target: <对象>(默认 *)
    返回 approved: true/false。用于决定是否需要先调用 dev_approve。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg or "", ["action", "target"])
    action = (f.get("action") or "").strip()
    target = (f.get("target") or "*").strip() or "*"
    if not action:
        return "参数缺失：请提供 action: <操作名>。"
    from game_workbench import approval_status
    st = approval_status(root, action, target)
    return f"操作 {action}(target={target}) 审批状态：{'已通过' if st['approved'] else '未通过（需先调用 dev_approve）'}（有效期 {st['ttl_seconds'] // 60} 分钟）。"


def _region_file(root, rmap, key, rel_path):
    """Resolve a file inside a configured region and reject symlink/path escapes."""
    if key not in rmap:
        return None, f"未知分区：{key}"
    base = os.path.realpath(os.path.join(root, rmap[key]["dir"]))
    rel = (rel_path or "").strip().strip("'\"").replace("\\", "/")
    if not rel or os.path.isabs(rel) or any(p in ("", ".", "..") for p in rel.split("/")):
        return None, "素材路径必须是分区内的相对路径，禁止绝对路径和 ..。"
    target = os.path.realpath(os.path.join(base, rel))
    if target != base and not target.startswith(base + os.sep):
        return None, "拒绝访问：路径不在目标分区内。"
    return target, None


def dev_asset_get(arg):
    """通过素材区接口取得素材引用；其它分区只能拿到素材区内的路径/元数据。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["asset_id", "path", "consumer_region"])
    asset_id = (f.get("asset_id") or f.get("path") or "").strip()
    consumer = (f.get("consumer_region") or "").strip()
    if not asset_id:
        return "参数缺失：请提供 asset_id 或 path。"
    if consumer and consumer not in rmap:
        return f"未知调用分区：{consumer}"
    assets_dir = os.path.realpath(os.path.join(root, rmap.get("assets", {}).get("dir", "assets")))
    manifest = os.path.join(assets_dir, "manifest.json")
    rel = asset_id.replace("\\", "/").strip("/")
    # 支持 manifest 中的 id -> path 映射，也支持直接传相对路径。
    if os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("assets", data) if isinstance(data, dict) else data
            if isinstance(entries, dict) and asset_id in entries:
                entry = entries[asset_id]
                rel = entry.get("path", "") if isinstance(entry, dict) else str(entry)
            elif isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("id") == asset_id:
                        rel = entry.get("path", "")
                        break
        except (OSError, ValueError):
            pass
    target, reason = _region_file(root, rmap, "assets", rel)
    if target is None:
        return reason
    if not os.path.isfile(target):
        return f"素材不存在：{rel}"
    return json.dumps({
        "ok": True, "asset_id": asset_id, "path": os.path.relpath(target, root).replace("\\", "/"),
        "consumer_region": consumer or None, "size": os.path.getsize(target),
        "extension": os.path.splitext(target)[1].lower(),
    }, ensure_ascii=False)


def dev_asset_register(arg):
    """注册素材到 assets/manifest.json；不复制文件，只建立稳定 ID。"""
    res, err = _require_regions()
    if res is None: return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["asset_id", "path", "type", "license", "tags"])
    aid, rel = (f.get("asset_id") or "").strip(), (f.get("path") or "").strip()
    if not aid or not rel: return "参数缺失：请提供 asset_id 和 path。"
    target, reason = _region_file(root, rmap, "assets", rel)
    if target is None: return reason
    if not os.path.isfile(target): return f"素材不存在：{rel}"
    manifest = os.path.join(root, rmap.get("assets", {}).get("dir", "assets"), "manifest.json")
    try:
        with open(manifest, encoding="utf-8") as fh: data = json.load(fh)
    except (OSError, ValueError): data = {}
    if not isinstance(data, dict): data = {}
    data.setdefault("assets", {})[aid] = {"path": rel.replace("\\", "/"), "type": f.get("type") or "unknown", "license": f.get("license") or "unknown", "tags": [x.strip() for x in (f.get("tags") or "").split(",") if x.strip()]}
    with open(manifest, "w", encoding="utf-8") as fh: json.dump(data, fh, ensure_ascii=False, indent=2)
    return json.dumps({"ok": True, "asset_id": aid, "path": rel}, ensure_ascii=False)


def dev_capture_bug(arg):
    """把异常堆栈/复现信息归档到 bugs 分区，返回可追踪的 Bug ID。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["error", "exception", "traceback", "source_region", "reproduction", "severity", "title"])
    message = (f.get("error") or f.get("exception") or "未知异常").strip()
    source = (f.get("source_region") or "").strip()
    if source and source not in rmap:
        return f"未知来源分区：{source}"
    bug_id = "BUG-" + __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + __import__("uuid").uuid4().hex[:6]
    bugs_dir = os.path.realpath(os.path.join(root, rmap.get("bugs", {}).get("dir", "bugs")))
    os.makedirs(bugs_dir, exist_ok=True)
    record = {
        "id": bug_id, "title": (f.get("title") or message[:120]).strip(),
        "severity": (f.get("severity") or "error").strip(), "source_region": source or None,
        "error": message, "traceback": f.get("traceback") or "",
        "reproduction": f.get("reproduction") or "", "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "status": "open",
    }
    path = os.path.join(bugs_dir, bug_id + ".json")
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return json.dumps({"ok": True, "bug_id": bug_id, "path": os.path.relpath(path, root).replace("\\", "/")}, ensure_ascii=False)


def dev_list_bugs(arg=""):
    """列出 bugs 分区中的结构化异常记录。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    base = os.path.join(root, rmap.get("bugs", {}).get("dir", "bugs"))
    rows = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base), reverse=True):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(base, name), encoding="utf-8") as fh:
                    rows.append(json.load(fh))
            except (OSError, ValueError):
                continue
    return json.dumps({"ok": True, "bugs": rows[:200]}, ensure_ascii=False)


def dev_update_bug(arg):
    """更新 Bug 状态（open/investigating/fixed/ignored）。"""
    res, err = _require_regions()
    if res is None: return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["bug_id", "status"])
    bid, status = (f.get("bug_id") or "").strip(), (f.get("status") or "").strip().lower()
    if not bid or status not in {"open", "investigating", "fixed", "ignored"}:
        return "参数错误：bug_id 必填，status 必须是 open/investigating/fixed/ignored。"
    path = os.path.join(root, rmap.get("bugs", {}).get("dir", "bugs"), bid + ".json")
    if not os.path.isfile(path): return f"未找到 Bug：{bid}"
    with open(path, encoding="utf-8") as fh: record = json.load(fh)
    record["status"] = status
    record["updated_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as fh: json.dump(record, fh, ensure_ascii=False, indent=2); fh.write("\n")
    return json.dumps({"ok": True, "bug": record}, ensure_ascii=False)


def game_validate_data(arg=""):
    root = _get_code_root()
    from game_workbench import validate_data
    return json.dumps(validate_data(root), ensure_ascii=False) if root else "尚未配置代码库。"


def game_release_check(arg=""):
    root = _get_code_root()
    from game_workbench import release_check
    return json.dumps(release_check(root), ensure_ascii=False) if root else "尚未配置代码库。"


def game_upsert_task(arg):
    root = _get_code_root()
    if not root: return "尚未配置代码库。"
    from game_workbench import upsert_task
    f = _parse_keyed(arg or "", ["id", "title", "description", "region", "priority", "status", "owner", "files"])
    f["files"] = [x.strip() for x in (f.get("files") or "").split(",") if x.strip()]
    return json.dumps(upsert_task(root, f), ensure_ascii=False)

def game_simulate(arg):
    from game_workbench import simulate_growth
    f=_parse_keyed(arg or "",["levels","base","growth"])
    return json.dumps(simulate_growth(int(f.get("levels") or 50),float(f.get("base") or 100),float(f.get("growth") or 1.08)),ensure_ascii=False)
def game_impact(arg):
    from game_workbench import impact_analysis
    f=_parse_keyed(arg or "",["query"]); return json.dumps(impact_analysis(_get_code_root(),f.get("query") or ""),ensure_ascii=False)
def game_playtest(arg):
    from game_workbench import playtest
    f=_parse_keyed(arg or "",["command","timeout"]); return json.dumps(playtest(_get_code_root(),f.get("command") or "",int(f.get("timeout") or 30)),ensure_ascii=False)


TOOLS = {
    "web_fetch": {"description": "读取公开网页正文并返回来源、标题和清理后的文本。输入完整 http/https URL。联网研究时先 web_search，再对关键来源调用。", "func": web_fetch},
    "dev_mcp_call": {"description": "调用已启用的 MCP 游戏引擎连接器。输入 key: 服务器key、name: 工具名、arguments: JSON。先用 MCP 工具清单确认可用工具；外部连接器需已启用并遵守审批。", "func": dev_mcp_call},
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
    "dev_http_request": {
        "description": "调用你自己的外部业务 API（REST/JSON）。受 EXTERNAL_API_ALLOWLIST 域名白名单约束（防止 SSRF），未配置白名单则拒绝。输入（多行 key: value）：url: <完整URL> 换行 method: <GET/POST/...默认GET> 换行 可选 timeout: <秒> 换行 可选 headers: <单行JSON对象> 换行 可选 body: <请求体，可多行>。返回 HTTP 状态码 + 响应头 + 截断响应体。",
        "func": dev_http_request,
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
    "dev_asset_get": {
        "description": "通过素材区接口取得素材引用。输入 asset_id 或 path，可选 consumer_region；只允许读取 assets 分区内的文件，不复制或内联素材。",
        "func": dev_asset_get,
    },
    "dev_asset_register": {
        "description": "将 assets 分区内已存在的文件注册到 manifest.json，输入 asset_id/path/type/license/tags。",
        "func": dev_asset_register,
    },
    "dev_capture_bug": {
        "description": "把异常、堆栈、来源分区和复现步骤写入 bugs 分区，生成唯一 Bug ID。输入 error/traceback/source_region/reproduction/title/severity。",
        "func": dev_capture_bug,
    },
    "dev_list_bugs": {
        "description": "列出 bugs 分区中的异常记录，返回 Bug ID、严重等级、来源和时间。",
        "func": dev_list_bugs,
    },
    "dev_update_bug": {
        "description": "更新 Bug 状态，输入 bug_id 和 status(open/investigating/fixed/ignored)。",
        "func": dev_update_bug,
    },
    "game_validate_data": {"description": "校验项目 JSON/YAML/TOML 配置格式。", "func": game_validate_data},
    "game_release_check": {"description": "执行发布前检查：配置、翻译和敏感 .env 文件。", "func": game_release_check},
    "game_upsert_task": {"description": "创建或更新游戏开发任务，输入 title/region/priority/status 等字段。", "func": game_upsert_task},
    "game_simulate": {"description": "模拟等级成长数值，输入 levels/base/growth。", "func": game_simulate},
    "game_impact": {"description": "按符号或关键词分析代码影响文件，输入 query。", "func": game_impact},
    "game_playtest": {"description": "在项目根目录运行 Playtest 命令，输入 command/timeout。", "func": game_playtest},
    "init_regions": {
        "description": "初始化「分区开发」结构：在代码库根目录建 assets/（素材区）、values/（数值区）、bugs/（bug 区）、behaviors/（角色行为区）等独立子目录（具体分区以 regions.json 为准），每个目录 git init 独立仓库，并生成 DEV_INDEX.md、DOCMIND_RULES.md（分区契约，会被注入 Agent 系统提示，强制越区写被拦截）。用于游戏等分工开发，防止代码堆叠与混乱。输入留空即可；需先配置代码库根目录（/api/ingest_code）。",
        "func": init_regions_tool,
    },
    "dev_list_regions": {
        "description": "列出已配置分区的 key/名称/目录/依赖/导出/脏状态，供你选用正确分区。输入留空即可。在调用 dev_region_read/edit/verify/commit/refactor 前先调用它确认分区 key。",
        "func": dev_list_regions,
    },
    "dev_region_read": {
        "description": "读取某分区内的文件（分区作用域，越区读被拒）。输入：第一行 region: <分区key>，第二行 path: <分区内相对路径>。读取后该文件可被 dev_region_edit 整体重写（满足先读后写护栏）。",
        "func": dev_region_read,
    },
    "dev_region_edit": {
        "description": "受控修改/新建某分区内的文件（分区作用域，越区写被拒；复用 apply_edit/create_file 的全部护栏：先读后写、.py 语法校验、200KB 上限、人工确认）。输入格式：第一行 region: <分区key>，第二行 path: <分区内相对路径>，可选 old_text: <精确旧片段>，最后 new_text: <新内容（可多行）>。提供 old_text 做局部安全替换；省略 old_text 且已 dev_region_read 过该文件则整体重写；文件不存在则按新建处理。",
        "func": dev_region_edit,
    },
    "dev_region_verify": {
        "description": "校验单个分区：若该分区配置了 verify 命令（regions.json 的 verify 字段）则在分区目录内执行；否则检查其导出接口文件是否齐全。输入：region: <分区key>。verify 命令建议写为脚本（如 `pytest tests/`），不要写 `python -c ...`（会被安全策略拦截）。",
        "func": dev_region_verify,
    },
    "dev_refactor": {
        "description": "跨分区安全搬移：把某分区内的文件移动到另一分区（受依赖方向约束，不破坏 git 跟踪）。输入：src_region: <源key>、src_path: <源相对路径>、dst_region: <目标key>、dst_path: <目标相对路径>。目标不能已存在（不覆盖）；搬移后从源分区 git 移除源文件。禁止把代码挪进「已依赖源分区」的分区（避免循环耦合/倒置分层）。",
        "func": dev_refactor,
    },
    "dev_commit": {
        "description": "提交单个分区的改动（该分区独立 git 仓库内 commit）。输入：region: <分区key> 换行 message: <提交说明>。",
        "func": dev_commit,
    },
    "dev_verify_contracts": {
        "description": "校验全部分区的契约：依赖方向无环、被依赖分区导出文件存在、依赖目标存在。输入留空即可。建议在大幅改动分区前后调用，确认架构约束未被破坏。",
        "func": dev_verify_contracts,
    },
    "dev_rebuild_index": {
        "description": "依据当前分区配置重算 DEV_INDEX.md（分区目录/文件数/依赖变动后调用）。输入留空即可。",
        "func": dev_rebuild_index,
    },
    "dev_commit_all": {
        "description": "把所有分区的改动各提交一次，并记进 dev_changesets.jsonl 作为一次绑定变更集（可整体回滚）。输入：message: <本次功能改动说明>。返回变更集 id 与各分区提交哈希。",
        "func": dev_commit_all,
    },
    "dev_list_changesets": {
        "description": "列出已记录的跨区变更集（dev_changesets.jsonl）。输入留空即可。返回各变更集 id/message/涉及分区，供 dev_rollback_changeset 选用。",
        "func": dev_list_changesets,
    },
    "dev_rollback_changeset": {
        "description": "整体回滚某变更集：对每个分区 revert 其记录的 commit（生成新提交撤销改动）。输入：changeset: <变更集id> 或 id: <变更集id>。先用 dev_list_changesets 查看 id。",
        "func": dev_rollback_changeset,
    },
    "list_dir": {
        "description": "浏览代码库内的目录结构（限定 code_root，供 Agent 研判代码库组织方式）。输入：目录相对路径，可直接写 behaviors/ 或 behaviors（也兼容 path: behaviors/）；留空列根目录。返回子项（目录/文件）及大小/类型。在研判分区方案前先用它勘察顶层有哪些模块/资源目录。",
        "func": list_dir,
    },
    "dev_propose_regions": {
        "description": "依据真实代码库结构，由你研判分区方案（默认 8 个分区仅作初始建议，实际分区由你判断）。输入留空即可。返回结构分析 + 建议分区清单（每区带 detected 证据与 included 启用建议）+ 代码库特有的可独立模块 + 中文结论。先用 list_dir 勘察，再调用它拿基线，据 detected/included 增删分区，最后用 dev_apply_regions 落地。",
        "func": dev_propose_regions,
    },
    "dev_apply_regions": {
        "description": "应用你研判后的自定义分区方案：把给定分区清单写入 regions.json 并初始化（建目录/每区 git/导出桩/规则），此后写操作被约束在这些分区内。输入：regions: <JSON 数组，或 {\"regions\":[...]} 对象>；每项至少含 key 与 dir，可选 name/desc/depends_on/exports/verify。可由 dev_propose_regions 的产出裁减得到。",
        "func": dev_apply_regions,
    },
    "dev_add_region": {
        "description": "向现有分区配置追加（或覆盖同名）一个分区并立即初始化（建目录/每区 git/规则）。输入：key: <分区key> 换行 dir: <目录> 换行 name: <中文名> 换行 [desc:] [access:] [depends_on:]（逗号分隔） [exports:]（逗号分隔）。用于研判后按需增补单个分区，无需重传整个方案。",
        "func": dev_add_region,
    },
    "dev_approve": {
        "description": "审批敏感操作前必须调用：记录一次审批，30 分钟内该操作放行。输入：action: <commit_region|commit_all|rollback_changeset|apply_regions> 换行 target: <对象>。target 精确匹配、不是通配符：commit_region 传分区 key（不能用 *）、rollback_changeset 传变更集 id、commit_all/apply_regions 固定传 *。在调用 dev_commit/dev_commit_all/dev_rollback_changeset/dev_apply_regions/dev_add_region 之前先调用本工具；若它们返回 blocked/approval_required，先调用本工具再重试，不要绕过。",
        "func": dev_approve,
    },
    "dev_approval_status": {
        "description": "查询某敏感操作当前是否已通过审批。输入：action: <操作名> 换行 target: <对象>(默认 *)。返回已通过/未通过，用于决定是否需要先调用 dev_approve。",
        "func": dev_approval_status,
    },
}
