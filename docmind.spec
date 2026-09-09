# -*- mode: python ; coding: utf-8 -*-
# DocMind 打包配置（PyInstaller，onedir 目录模式）
# 用法：.venv\Scripts\python.exe -m PyInstaller docmind.spec
# 产物：dist\DocMind\DocMind.exe + 依赖 DLL（文件夹分发；启动更快、索引可持久化）
import os
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
