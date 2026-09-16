"""DocMind 配置中心：从 .env 读取，集中管理模型 / 路径 / 参数。"""
import json
import os
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv()

# 资源根目录：未打包时取项目目录；PyInstaller 单文件(onefile)解包后取 _MEIPASS；
# 目录(onedir)模式（本项目实际打包方式，PyInstaller>=5.13）把收集的资源放在 exe
# 同级的 _internal/ 下。onedir 不会设置 sys._MEIPASS，必须按实际落点定位，否则会
# 回退去读源码目录的 .chroma/web（一旦把 dist 文件夹挪走就全盘失效）。
if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(sys.executable)
    if hasattr(sys, "_MEIPASS"):
        BASE_DIR = sys._MEIPASS
    elif os.path.isdir(os.path.join(EXE_DIR, "_internal", "web")):
        BASE_DIR = os.path.join(EXE_DIR, "_internal")
    else:
        BASE_DIR = EXE_DIR
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 项目根目录与前端目录（供 api.py 等模块复用）
PROJECT_DIR = BASE_DIR
PROJECT_WEB_DIR = os.path.join(PROJECT_DIR, "web")

# 运行时状态根：.chroma / .docmind* / 会话 / trace / 预算等派生自此。
# 显式 DOCMIND_STATE_ROOT 优先；测试进程默认隔离到临时目录（不污染仓库）。
STATE_ROOT = os.getenv("DOCMIND_STATE_ROOT") or BASE_DIR


def _running_under_unittest():
    """当前进程是否由 `python -m unittest ...` 驱动（严格判据）。

    过去判据是 `"unittest" in sys.modules`（外加 argv[0] 猜测），会把「任何 import
    了 unittest 的进程」（如 `python -c "import unittest, config"`）也误判为测试
    进程，把状态根搬到临时目录，让用户状态「看起来丢了」。这里改用 runpy 的精确
    特征：`-m unittest` 时 `sys.modules["__main__"].__spec__.name == "unittest.__main__"`
    （unittest/__main__.py 以 __main__ 身份执行，spec 名保留）；普通 `import unittest`
    或 `python script.py` 的 __main__ 无此 spec，故只在真正的 `-m unittest` 进程命中。
    """
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    return getattr(spec, "name", None) == "unittest.__main__"


# 最小、可解释的测试隔离：仅 `python -m unittest` 进程生效。打包 exe / dev 服务不
# 满足该判据，行为不变；显式设置 DOCMIND_STATE_ROOT 优先于本默认；
# DOCMIND_NO_TEST_ISOLATION=1 可关闭（例如想跑真实状态根时）。
_TEST_STATE_ISOLATED = bool(
    not os.getenv("DOCMIND_STATE_ROOT")
    and _running_under_unittest()
    and os.getenv("DOCMIND_NO_TEST_ISOLATION") != "1"
)
if _TEST_STATE_ISOLATED:
    STATE_ROOT = tempfile.mkdtemp(prefix="docmind_test_state_")


def state_path(env_name, default):
    """取运行时状态路径：显式 env 优先；测试隔离生效时相对路径以 STATE_ROOT 为基准。

    - env 未设置（空串视为未设置）→ 返回 default（通常已派生自 STATE_ROOT）；
    - env 是绝对路径 → 原样返回；
    - env 是相对路径 且 测试隔离生效 → 挂到 STATE_ROOT 下（否则会相对 cwd 写进仓库根）；
    - env 是相对路径 且 未隔离 → 原样返回（保持「显式 env 优先」语义）。
    """
    raw = os.getenv(env_name)
    if not raw:
        return default
    if _TEST_STATE_ISOLATED and not os.path.isabs(raw):
        return os.path.join(STATE_ROOT, raw)
    return raw

# ---- LLM Provider ----
# 可选: qwen(通义千问) / deepseek / kimi(月之暗面) / zhipu(智谱) /
#       siliconflow(硅基流动) / openai / ollama / llamacpp /
#       custom(任意 OpenAI 兼容端点，base_url 运行时填写) / mock(离线演示)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")

# 各 provider 的 OpenAI 兼容接入点（base_url 统一为 OpenAI 协议，与 Android 端 AIApiClient 思路一致）
# cloud=True 表示外部云端服务（请求出网、按 token 计费）；本地服务与 mock 为 False。
PROVIDERS = {
    "qwen": {
        "label": "通义千问（阿里云百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-plus",
        "cloud": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "cloud": True,
    },
    "kimi": {
        "label": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "default_model": "kimi-k2-0905-preview",
        "cloud": True,
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "default_model": "glm-4.6",
        "cloud": True,
    },
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_env": "SILICONFLOW_API_KEY",
        "default_model": "Qwen/Qwen3-8B",
        "cloud": True,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "cloud": True,
    },
    "ollama": {
        "label": "本地 Ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
        "default_model": "qwen2.5:7b",
        "cloud": False,
    },
    "llamacpp": {
        "label": "本地 llama.cpp",
        "base_url": "http://localhost:8080/v1",
        "api_key_env": "",
        "default_model": "qwen3.6-35b-a3b",
        "cloud": False,
    },
    # 任意 OpenAI 兼容服务：base_url / model / key 全部运行时填写
    "custom": {
        "label": "自定义 OpenAI 兼容服务",
        "base_url": "",
        "api_key_env": "",
        "default_model": "",
        "cloud": True,
    },
    "mock": {
        "label": "离线演示（mock）",
        "base_url": "",
        "api_key_env": "",
        "default_model": "mock",
        "cloud": False,
    },
}

LLM_MODEL = os.getenv("LLM_MODEL", "")  # 为空则用 provider 默认
LLM_API_KEY = os.getenv("LLM_API_KEY", "")  # 直接指定可覆盖环境变量

# ---- Embedding ----
# 可选: qwen(通义千问 text-embedding-v3, 需 key) / ollama(本机 Ollama nomic-embed-text, 零 Key) / local(本地哈希占位, 仅离线演示)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "bge-m3" if EMBEDDING_PROVIDER == "ollama" else "text-embedding-v3",
)
LOCAL_EMBED_DIM = 256  # 本地兜底向量维度（仅离线演示用，非语义向量）

# ---- 路径 / 参数 ----
# .chroma 与其余状态文件同源于 STATE_ROOT（打包后 STATE_ROOT 默认 = 资源目录）。
# 该目录**不随包分发**（见 docmind.spec，否则会泄漏开发者的本地代码索引并额外增加约
# 377MB 体积），由 chromadb 首次启动时自动创建空目录，用户通过 /api/ingest_code 索引
# 自己的代码库。CHROMA_DIR 显式 env 仍优先。
CHROMA_DIR = state_path("CHROMA_DIR", os.path.join(STATE_ROOT, ".chroma"))


def ensure_dirs():
    """幂等创建运行时目录（惰性）。

    严禁在模块导入期调用——导入不应产生任何磁盘副作用。由 api.py 的 lifespan 与
    vectorstore 建立 chroma 客户端前显式调用。
    """
    try:
        os.makedirs(CHROMA_DIR, exist_ok=True)
    except OSError:
        pass


COLLECTION_NAME = os.getenv("COLLECTION_NAME", "docmind")
CODE_COLLECTION_NAME = os.getenv("CODE_COLLECTION_NAME", "docmind_code")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80"))
TOP_K = int(os.getenv("TOP_K", "4"))
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "8"))
# ---- Agent 上下文预算（防止长系统提示 + 多轮观察 + 思考模型 reasoning 撑爆 n_ctx）----
# 多轮记忆回放的问答对【硬上限】：实际回放多少轮先按 token 窗口动态决定
# （Agent._history_window，占 prompt 预算 COMPACT_KEEP_RATIO），此值只兜底防失控。
AGENT_HISTORY_TURNS = int(os.getenv("AGENT_HISTORY_TURNS", "40"))
OBS_MAX_CHARS = int(os.getenv("OBS_MAX_CHARS", "1200"))  # 单条工具观察回填给模型前的截断长度
HISTORY_ANSWER_CHARS = int(os.getenv("HISTORY_ANSWER_CHARS", "700"))  # 回放历史回答时的单条截断长度
TRAIL_ASSISTANT_CHARS = int(os.getenv("TRAIL_ASSISTANT_CHARS", "1000"))  # trail 中保留的模型单轮决策上限
# 每轮送模型前，整段 prompt 的 token 预算（llamacpp 走 /tokenize 精算）。
# 仅作为 env 显式覆盖值与无画像客户端的兜底；实际默认值按模型真实窗口缩放
# （见 prompt_token_budget）：16k 本地模型约 12k，131k 云端模型可放到 ~98k，
# 避免「小窗口塞不下、大窗口浪费 90%」的一刀切。
PROMPT_TOKEN_BUDGET = int(os.getenv("PROMPT_TOKEN_BUDGET", "11000"))
# 历史回放/压缩保留占 prompt 预算的比例：留出其余空间给系统提示、当前问题与
# 本轮工具往返（observation）。触发压缩的历史占用比例（80%：大窗口模型尽量
# 少丢早期原文，到额度八成再压缩；保留 35% 给最近轮次）。
COMPACT_KEEP_RATIO = float(os.getenv("DOCMIND_COMPACT_KEEP_RATIO", "0.35"))
COMPACT_TRIGGER_RATIO = float(os.getenv("DOCMIND_COMPACT_TRIGGER_RATIO", "0.8"))
# 单次补全上限（含思考型模型的 reasoning）：防止模型不按格式收尾时无限生成，
# 到顶后 finish_reason=length，Agent 会自动 nudge 要求直接给简短 Final Answer。
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "3072"))
# 是否允许思考型模型把预算花在 reasoning_content 上。ReAct 工具路由不需要长思考，
# 实测关思考后同类请求 55 token/4s 给出 Action（开思考时偶发烧满 3072 token 不行动）。
# 仅对本地 OpenAI 兼容服务（llamacpp/ollama）透传 chat_template_kwargs。
LLM_ENABLE_THINKING = os.getenv("LLM_ENABLE_THINKING", "0") == "1"

# ---- 模型能力画像 ---------------------------------------------------------
# 不同模型家族差异很大，不能一套参数打天下：
# · 本地模型需要按自身上下文窗口扩 num_ctx（窗口小的模型不能盲目给 14k prompt）；
# · 思考分两类：native（reasoner 类模型天生输出 reasoning_content，无需开关）
#   与 toggle（qwen3 家族靠 enable_thinking / think 参数切换）；
# · 其余模型不支持思考，传了参数反而可能 400，必须跳过。
# 画像按 (provider, 模型名子串) 匹配，未命中走 provider 默认值。

# (provider, 模型名小写子串) -> 上下文窗口 token 数
_MODEL_CONTEXT_OVERRIDES = {
    ("deepseek", "reasoner"): 65536,
    ("openai", "gpt-4o"): 128000,
    ("openai", "gpt-4-"): 65536,
    ("openai", "gpt-3.5"): 16385,
    ("openai", "o1"): 200000,
    ("openai", "o3"): 200000,
    ("ollama", "qwen2.5:7b"): 32768,
}

# provider 级默认上下文窗口（模型未命中精确画像时使用）
_PROVIDER_CONTEXT_DEFAULT = {
    "qwen": 131072,
    "deepseek": 131072,
    "kimi": 131072,
    "zhipu": 131072,
    "siliconflow": 65536,
    "openai": 128000,
    "ollama": 16384,
    "llamacpp": 16384,
    "custom": 32768,
    "mock": 32768,
}

# 模型名命中任一子串即视为"原生思考模型"（推理始终开启，思考走 reasoning_content）
_NATIVE_THINK_HINTS = (
    "reasoner", "qwq", "-r1", "_r1", "/r1", "think", "o1-", "o3-", "o4-",
    "gpt-5", "deepseek-r1",
)

# 支持 enable_thinking 开关的家族：(provider, 模型名前缀/子串)
_TOGGLE_THINK_RULES = (
    ("qwen", "qwen3"),       #  DashScope 兼容模式 qwen3 系列支持 extra_body 开关
    ("ollama", "qwen3"),
    ("llamacpp", "qwen3"),
    ("custom", "qwen3"),
    ("siliconflow", "qwen3"),
)


def model_context_window(provider: str, model: str, live_window: int = None) -> int:
    """该模型可用的上下文窗口（token）。

    优先级：用户自定义覆盖（模型设置里手填，跨重启持久化）> 实时探测值
    （如 Ollama /api/show 返回的 context_length）> 内置模型画像 > 厂商默认。
    """
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    custom = get_context_window_override(p, m)
    if custom:
        return custom
    if live_window:
        return int(live_window)
    for (pp, hint), win in _MODEL_CONTEXT_OVERRIDES.items():
        if p == pp and hint in m:
            return win
    return _PROVIDER_CONTEXT_DEFAULT.get(p, 32768)


def prompt_token_budget(provider: str, model: str, context_window: int = None) -> int:
    """单次请求 prompt 可用的 token 预算，按模型真实窗口缩放。

    - env PROMPT_TOKEN_BUDGET 显式设置时强制采用（排障/压测用）；
    - context_window 可传入实时探测/自定义的窗口值（缺省走 model_context_window）；
    - 默认取「窗口的 75%」与「窗口 - 输出上限 - 512 余量」的较小值，
      并保 6144 下限（但受窗口可用量约束）。16k 本地模型 ≈ 12k，131k 云端 ≈ 98k，
      1M 模型 ≈ 786k；窗口过小时退化为窗口的一半（至少 256），绝不越过窗口。
    """
    env = os.getenv("PROMPT_TOKEN_BUDGET", "").strip()
    if env:
        try:
            return max(1024, int(env))
        except ValueError:
            pass
    win = int(context_window) if context_window else model_context_window(provider, model)
    reserve = LLM_MAX_TOKENS + 512
    usable = win - reserve
    if usable <= 0:
        # 窗口连输出预留都放不下：退化为窗口的一半（至少 256）
        budget = max(256, int(win * 0.5))
    else:
        scaled = int(win * 0.75)
        budget = min(usable, scaled)
        budget = max(min(6144, usable), budget)   # 保 6144 下限，但不得越过 usable
        budget = max(256, budget)
    return budget


def model_thinking_mode(provider: str, model: str) -> str:
    """思考能力画像：'native'（天生推理）/ 'toggle'（可开关）/ 'none'（不支持）。"""
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    if any(h in m for h in _NATIVE_THINK_HINTS):
        return "native"
    for pp, hint in _TOGGLE_THINK_RULES:
        if p == pp and hint in m:
            return "toggle"
    return "none"


def model_capability(provider: str, model: str, context_window: int = None) -> dict:
    """汇总模型能力：{context_window, thinking, cloud}，供前后端共同决策。

    context_window 可传入实时探测值（如 Ollama /api/show）；用户自定义覆盖优先。
    """
    cfg = PROVIDERS.get(provider or "", {})
    return {
        "context_window": model_context_window(provider, model, live_window=context_window),
        "thinking": model_thinking_mode(provider, model),
        "cloud": bool(cfg.get("cloud")),
    }

CODE_ROOT = os.getenv("CODE_ROOT", "")  # 代码问答模式的代码库根目录；为空表示未配置
CODE_CHUNK = int(os.getenv("CODE_CHUNK", "1200"))  # 单个代码切片的最大字符数
EDIT_CONFIRM = os.getenv("EDIT_CONFIRM", "0") == "1"  # 写工具是否需要人工确认（改前出 diff）
API_TOKEN = os.getenv("DOCMIND_API_TOKEN", "").strip()  # 为空保持本机免鉴权

# ---- 外部接入 DocMind API 的 CORS ----
# 逗号分隔的允许来源（如 https://app.example.com,http://localhost:3000）；
# 设为 * 表示允许任意来源（默认）。仅在「外部网页/前端要调用本服务」时需要。
DOCMIND_CORS_ORIGINS = [
    o.strip() for o in os.getenv("DOCMIND_CORS_ORIGINS", "*").split(",") if o.strip()
] or ["*"]

# ---- Agent 调用外部业务 API 的白名单 ----
# 逗号分隔，支持三种写法：api.example.com（精确匹配且含其子域）、
# *.example.com（仅子域，不含裸 example.com）、*（任意 host，协议仍限 http/https）。
# 命中其一才允许发起请求，防止 Agent 被诱导对内网/元数据地址做 SSRF
# （30x 重定向的每一跳也会重新过此白名单）。为空则禁止使用 dev_http_request。
EXTERNAL_API_ALLOWLIST = [
    h.strip().lower()
    for h in os.getenv("EXTERNAL_API_ALLOWLIST", "").split(",")
    if h.strip()
]

# ---- 聊天图片输入（视觉模型，如 qwen3.6 系列）----
CHAT_IMAGE_MAX_FILES = int(os.getenv("CHAT_IMAGE_MAX_FILES", "4"))  # 单条消息最多图片数
CHAT_IMAGE_MAX_BYTES = int(os.getenv("CHAT_IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))  # 单图大小上限（压缩前）
CHAT_IMAGE_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

# ---- 运行时覆盖（由前端 /api/config 动态设置，优先级高于 .env）----
# 模型类切换仅存于内存（重启恢复 .env）；code_root 例外，经 STATE_FILE 跨重启恢复。
_RUNTIME = {}


def set_runtime(key, value):
    _RUNTIME[key] = value


def get_runtime(key, default=None):
    return _RUNTIME.get(key, default)


# ---- 跨重启持久化的少量本地状态（当前仅 code_root）----
# 与 .chroma 同目录（开发=源码根；冻结=_internal），只存路径类非敏感数据，
# 避免重启后必须重新选择代码库。写入失败一律静默回落内存态，不影响主流程。
STATE_FILE = os.path.join(STATE_ROOT, ".docmind_state.json")


def save_state(key, value):
    try:
        data = {}
        if os.path.isfile(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (ValueError, OSError):
                data = {}
        data[key] = value
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_state(key, default=None):
    """读取跨重启持久化状态中的单个键；缺失/损坏返回 default。"""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return default
    return data.get(key, default)


# ---- 用户在模型设置里手填的上下文窗口覆盖：{"provider/model": tokens} ----
# 内置画像不可能覆盖所有模型（尤其自定义 OpenAI 端点），允许用户显式指定；
# 跨重启持久化在 STATE_FILE，优先级高于实时探测与内置画像。
_CONTEXT_WINDOW_OVERRIDES = {}


def _ctx_override_key(provider: str, model: str) -> str:
    return f"{(provider or '').strip().lower()}/{(model or '').strip().lower()}"


def get_context_window_override(provider: str, model: str):
    """返回用户自定义窗口（int）；未设置返回 None。"""
    v = _CONTEXT_WINDOW_OVERRIDES.get(_ctx_override_key(provider, model))
    return int(v) if isinstance(v, int) and v > 0 else None


def set_context_window_override(provider: str, model: str, tokens):
    """设置/更新自定义窗口（tokens 为正整数）；0 或非法值等同清除。"""
    key = _ctx_override_key(provider, model)
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        tokens = 0
    if tokens > 0:
        _CONTEXT_WINDOW_OVERRIDES[key] = tokens
    else:
        _CONTEXT_WINDOW_OVERRIDES.pop(key, None)
    save_state("context_window_overrides", dict(_CONTEXT_WINDOW_OVERRIDES))


def clear_context_window_override(provider: str, model: str):
    _CONTEXT_WINDOW_OVERRIDES.pop(_ctx_override_key(provider, model), None)
    save_state("context_window_overrides", dict(_CONTEXT_WINDOW_OVERRIDES))


def _apply_persisted_state():
    """恢复上次持久化的本地选择（code_root / GPU 偏好 / 自定义窗口）。

    过去在 import config 时调用（导入期磁盘读）——现改由服务启动期显式调用
    （api.py 的 lifespan，紧接 ensure_dirs() 之后、gpu.init() 之前），
    使导入 config 不再触碰磁盘。函数本身与调用点解耦：路径失效自动忽略。
    """
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    root = data.get("code_root")
    # 顺序约束：调用方（如 verify_scene_canvas.py / verify_engine_embed.py / verify_regions.py
    # 的 serve()）若已在启动前 set_runtime('code_root', …) 显式指定代码库，则不得被持久化
    # 状态覆盖——显式设置优先于持久化恢复。这正是把本函数从 import 期移到 lifespan
    # （ensure_dirs() 之后）后必须补上的约束：脚本「先设 code_root、再启动应用」，其
    # set_runtime 早于 lifespan 执行，若无此判断会被这里的无条件赋值冲掉。
    # 其余键（GPU 偏好 / 自定义窗口）调用方不会预设，行为保持不变。
    if isinstance(root, str) and root and os.path.isdir(root) and "code_root" not in _RUNTIME:
        _RUNTIME["code_root"] = root
    # GPU 空闲卸载/采样间隔为用户在 GPU 面板设置的本机偏好，跟随状态文件恢复
    for key in ("gpu_idle_unload_seconds", "gpu_poll_interval"):
        val = data.get(key)
        if isinstance(val, (int, float)) and val >= 0:
            _RUNTIME[key] = float(val)
    # 用户自定义的模型上下文窗口（键 "provider/model" -> 正整数）
    ov = data.get("context_window_overrides")
    if isinstance(ov, dict):
        for k, v in ov.items():
            if isinstance(k, str) and "/" in k and isinstance(v, int) and v > 0:
                _CONTEXT_WINDOW_OVERRIDES[k] = v


# 注：过去此处 import 期直接调用 _apply_persisted_state()（导入即磁盘读）。
# 现改为由服务启动期显式调用（api.py lifespan，见 _app_lifespan），
# 使 `import config` 不产生任何磁盘 I/O；仅读取的持久化状态在进程真正
# 对外提供服务前恢复，行为对外等价。


def edit_confirm_enabled():
    """写工具是否处于「人工确认」模式：运行时覆盖优先，其次 .env EDIT_CONFIRM。"""
    v = get_runtime("edit_confirm")
    if v is not None:
        return bool(v)
    return EDIT_CONFIRM
