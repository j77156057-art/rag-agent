@echo off
REM DocMind 桌面端一键启动：激活 venv 并运行 desktop.py
REM 路径含单引号用户名时务必用双引号包裹（%dp0 已自带结尾反斜杠）
cd /d "%~dp0"
.venv\Scripts\python.exe desktop.py
pause
