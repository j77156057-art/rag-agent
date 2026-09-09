"""DocMind 配置中心：从 .env 读取，集中管理模型 / 路径 / 参数。"""
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
# 可选: qwen(通义千问) / deepseek / ollama / mock(离线演示，无需任何 key)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")

# 各 provider 的 OpenAI 兼容接入点（base_url 统一为 OpenAI 协议，与 Android 端 AIApiClient 思路一致）
PROVIDERS = {
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-plus",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
        "default_model": "qwen2.5:7b",
    },
    "mock": {
        "base_url": "",
        "api_key_env": "",
        "default_model": "mock",
    },
}

LLM_MODEL = os.getenv("LLM_MODEL", "")  # 为空则用 provider 默认
LLM_API_KEY = os.getenv("LLM_API_KEY", "")  # 直接指定可覆盖环境变量

# ---- Embedding ----
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")  # qwen / local
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
LOCAL_EMBED_DIM = 256  # 本地兜底向量维度（仅离线演示用，非语义向量）

# ---- 路径 / 参数 ----
if getattr(sys, "frozen", False):
    # 打包后强制使用随 exe 的资源目录里的 .chroma（含预索引数据），
    # 不受外部 .env 的相对路径覆盖影响。BASE_DIR 已按 onedir/onefile 正确定位。
    CHROMA_DIR = os.path.join(BASE_DIR, ".chroma")
else:
    CHROMA_DIR = os.getenv("CHROMA_DIR", os.path.join(BASE_DIR, ".chroma"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "docmind")
CODE_COLLECTION_NAME = os.getenv("CODE_COLLECTION_NAME", "docmind_code")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80"))
TOP_K = int(os.getenv("TOP_K", "4"))
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "5"))
CODE_ROOT = os.getenv("CODE_ROOT", "")  # 代码问答模式的代码库根目录；为空表示未配置
CODE_CHUNK = int(os.getenv("CODE_CHUNK", "1200"))  # 单个代码切片的最大字符数
EDIT_CONFIRM = os.getenv("EDIT_CONFIRM", "0") == "1"  # 写工具是否需要人工确认（改前出 diff）

# ---- 运行时覆盖（由前端 /api/config 动态设置，优先级高于 .env）----
# 仅存于内存，进程重启后恢复 .env 默认值。用于页面内"免重启切换模型"。
_RUNTIME = {}


def set_runtime(key, value):
    _RUNTIME[key] = value


def get_runtime(key, default=None):
    return _RUNTIME.get(key, default)


def edit_confirm_enabled():
    """写工具是否处于「人工确认」模式：运行时覆盖优先，其次 .env EDIT_CONFIRM。"""
    v = get_runtime("edit_confirm")
    if v is not None:
        return bool(v)
    return EDIT_CONFIRM
