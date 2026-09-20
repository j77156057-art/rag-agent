@echo off
chcp 936 >nul
REM ============================================================================
REM 编码要求：本文件必须保存为 ANSI/GBK(cp936)。原因同 run_desktop.bat：
REM 用 UTF-8 保存时 cmd 会按默认代码页(936)误读中文注释，乱码字节会吃掉行尾
REM CRLF 甚至下一行开头的字符，报出 'op.py' 不是内部或外部命令 这类怪错。
REM ============================================================================
setlocal
cd /d "%~dp0dist\DocMind"
if not exist DocMind.exe (
  echo [错误] 在 %~dp0dist\DocMind 找不到 DocMind.exe
  echo 请确认本批处理位于 rag-agent 目录，且已成功构建。
  pause
  exit /b 1
)
echo ============================================
echo   DocMind 启动器（诊断模式）
echo ============================================
echo 正在启动 DocMind.exe ...
start "" DocMind.exe
echo 已发起启动，等待 6 秒后检测本地服务...
timeout /t 6 >nul
echo 检测本地服务 api/config：
curl --noproxy "*" -s -o nul -w "HTTP %%{http_code}\n" http://127.0.0.1:8000/api/config 2>nul
echo.
echo ---- 启动日志 docmind_desktop.log ----
if exist docmind_desktop.log (type docmind_desktop.log) else (echo 暂无日志)
echo ----------------------------------------
echo 若上面显示 HTTP 200，请在浏览器打开: http://127.0.0.1:8000/
echo 若仍是空白/无反应，请把以上全部内容发给我。
echo （注意：关闭本窗口不会停止 DocMind，停止请在任务管理器结束 DocMind.exe 进程）
pause
