@echo off
chcp 65001 >nul
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
curl --noproxy * -s -o nul -w "本地服务(api/config): HTTP %{http_code}\n" http://127.0.0.1:8000/api/config 2>nul
echo.
echo ---- 启动日志 docmind_desktop.log ----
if exist docmind_desktop.log (type docmind_desktop.log) else (echo 暂无日志)
echo ----------------------------------------
echo 若上面显示 HTTP 200，请在浏览器打开: http://127.0.0.1:8000/
echo 若仍是空白/无反应，请把以上全部内容发给我。
echo （注意：关闭本窗口不会停止 DocMind，停止请在任务管理器结束 DocMind.exe 进程）
pause
