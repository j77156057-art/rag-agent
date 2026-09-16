"""DocMind 配置中心：从 .env 读取，集中管理模型 / 路径 / 参数。"""
import json
import os
import sys

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
if getattr(sys, "frozen", False):
    # 打包后使用随 exe 资源目录里的 .chroma。该目录**不随包分发**（见 docmind.spec，
    # 否则会泄漏开发者的本地代码索引并额外增加约 377MB 体积），由 chromadb 首次启动时
    # 自动创建空目录，用户通过 /api/ingest_code 索引自己的代码库。
    CHROMA_DIR = os.path.join(BASE_DIR, ".chroma")
else:
    CHROMA_DIR = os.getenv("CHROMA_DIR", os.path.join(BASE_DIR, ".chroma"))
# 确保索引目录存在（干净分发 / 首次启动时 chromadb 可能尚未建目录）
os.makedirs(CHROMA_DIR, exist_ok=True)
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
# 本轮工具往返（observation）。触发压缩的历史占用比例。
COMPACT_KEEP_RATIO = float(os.getenv("DOCMIND_COMPACT_KEEP_RATIO", "0.35"))
COMPACT_TRIGGER_RATIO = float(os.getenv("DOCMIND_COMPACT_TRIGGER_RATIO", "0.6"))
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


def model_context_window(provider: str, model: str) -> int:
    """该模型可用的上下文窗口（token）。保守取厂商公开值，未知模型取 provider 默认。"""
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    for (pp, hint), win in _MODEL_CONTEXT_OVERRIDES.items():
        if p == pp and hint in m:
            return win
    return _PROVIDER_CONTEXT_DEFAULT.get(p, 32768)


def prompt_token_budget(provider: str, model: str) -> int:
    """单次请求 prompt 可用的 token 预算，按模型真实窗口缩放。

    - env PROMPT_TOKEN_BUDGET 显式设置时强制采用（排障/压测用）；
    - 默认取「窗口的 75%」与「窗口 - 输出上限 - 512 余量」的较小值，
      并保 6144 下限。16k 本地模型 ≈ 12k，131k 云端 ≈ 98k，1M 模型 ≈ 786k。
    """
    env = os.getenv("PROMPT_TOKEN_BUDGET", "").strip()
    if env:
        try:
            return max(1024, int(env))
        except ValueError:
            pass
    win = model_context_window(provider, model)
    usable = max(2048, win - LLM_MAX_TOKENS - 512)
    scaled = int(win * 0.75)
    return max(6144, min(usable, scaled))


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


def model_capability(provider: str, model: str) -> dict:
    """汇总模型能力：{context_window, thinking, cloud}，供前后端共同决策。"""
    cfg = PROVIDERS.get(provider or "", {})
    return {
        "context_window": model_context_window(provider, model),
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
STATE_FILE = os.path.join(BASE_DIR, ".docmind_state.json")


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


def _apply_persisted_state():
    """进程启动（import config）时恢复上次的本地选择；路径失效自动忽略。"""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    root = data.get("code_root")
    if isinstance(root, str) and root and os.path.isdir(root):
        _RUNTIME["code_root"] = root
    # GPU 空闲卸载/采样间隔为用户在 GPU 面板设置的本机偏好，跟随状态文件恢复
    for key in ("gpu_idle_unload_seconds", "gpu_poll_interval"):
        val = data.get(key)
        if isinstance(val, (int, float)) and val >= 0:
            _RUNTIME[key] = float(val)


_apply_persisted_state()


def edit_confirm_enabled():
    """写工具是否处于「人工确认」模式：运行时覆盖优先，其次 .env EDIT_CONFIRM。"""
    v = get_runtime("edit_confirm")
    if v is not None:
        return bool(v)
    return EDIT_CONFIRM
