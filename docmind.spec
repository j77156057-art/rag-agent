# -*- mode: python ; coding: utf-8 -*-
# DocMind 打包配置（PyInstaller，onedir 目录模式）
# 用法：.venv\Scripts\python.exe -m PyInstaller docmind.spec
# 产物：dist\DocMind\DocMind.exe + 依赖 DLL（文件夹分发；启动更快、索引可持久化）
import os
import shutil
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# chromadb / webview 含大量运行时动态导入的子模块，用 collect_all 一网打尽最稳妥
chromadb_datas, chromadb_binaries, chromadb_hiddenimports = collect_all("chromadb")
webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=chromadb_binaries + webview_binaries,
    datas=[
        (".chroma", ".chroma"),
        ("web", "web"),
    ] + chromadb_datas + webview_datas,
    hiddenimports=[
        "fastapi",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.server",
        "openai",
        "pypdf",
        "markdown",
        "python-multipart",
        "httpx",
        "dotenv",
        "starlette",
        "pywebview",
        "chromadb",
        "numpy",
        "onnxruntime",
        "tokenizers",
        "hnswlib",
        "google.protobuf",
        "opentelemetry",
        "opentelemetry.trace",
        "opentelemetry.sdk.trace",
    ] + chromadb_hiddenimports + webview_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["unittest", "pydoc", "doctest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DocMind",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon="docmind.ico",
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DocMind",
)

# ---------------------------------------------------------------------------
# 随包携带 MinGit（Git for Windows 官方便携版）：用户机器无需安装 git，
# 分区初始化/提交/回滚与工作台文件树脏标记即可用。desktop.py 在 frozen
# 启动时把 <exe目录>/MinGit/cmd 前置进 PATH。
# 源目录解析顺序：环境变量 MINGIT_DIR → vendor/MinGit → 用户级便携安装。
# 不放进 _internal（PyInstaller 数据目录），而与 exe 同级，便于核对与整体替换。
# ---------------------------------------------------------------------------
def _resolve_mingit():
    candidates = [
        os.environ.get("MINGIT_DIR", ""),
        os.path.join(SPECPATH, "vendor", "MinGit"),
        os.path.expanduser(r"~\.local\bin\MinGit"),
    ]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "cmd", "git.exe")):
            return c
    raise SystemExit(
        "未找到 MinGit（需要 cmd\\git.exe）。请从 "
        "https://registry.npmmirror.com/-/binary/git-for-windows/ 下载 "
        "MinGit-*-64-bit.zip 解压到 vendor\\MinGit，或设置环境变量 MINGIT_DIR。"
    )


_mingit_src = _resolve_mingit()
_mingit_dst = os.path.join(DISTPATH, "DocMind", "MinGit")
if os.path.isdir(_mingit_dst):
    shutil.rmtree(_mingit_dst)
shutil.copytree(_mingit_src, _mingit_dst)
print("[docmind.spec] MinGit bundled: %s -> %s" % (_mingit_src, _mingit_dst))
