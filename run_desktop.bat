@echo off
chcp 936 >nul
REM ============================================================================
REM 编码要求：本文件必须保存为 ANSI/GBK(cp936)。
REM 若用 UTF-8 保存，cmd 会按默认代码页(936)误读中文注释，乱码字节会吃掉行尾 CRLF、
REM 甚至吃掉下一行开头的字符，表现就是这些"看不懂"的报错：
REM   'op.py' 不是内部或外部命令            <- desktop.py 被从中间切开
REM   '鍙屽紩鍙峰寘瑁癸紙~dp0"' 不是内部或外部命令   <- 中文注释乱码后 % 被吃掉
REM ============================================================================
REM DocMind 桌面端一键启动：用项目 venv 运行 desktop.py
REM 路径含单引号用户名时务必用双引号包裹（%~dp0 已自带结尾反斜杠）
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run

echo [错误] 找不到 .venv\Scripts\python.exe
echo.
echo 请先在本目录【依次】执行下面两行（cmd 与 PowerShell 都适用）：
echo     python -m venv .venv
echo     .venv\Scripts\pip install -r requirements.txt
echo.
echo 注意：不要写成一行用连接符串联（PowerShell 5.1 不支持）；也不要连同本行前面的冒号一起复制。
echo.
echo 或者直接用已打包的桌面版，无需 Python、无需联网：
echo     https://github.com/j77156057-art/rag-agent/releases
pause
exit /b 1

:run
".venv\Scripts\python.exe" desktop.py
echo.
echo [提示] desktop.py 已退出（退出码 %errorlevel%）。
pause
