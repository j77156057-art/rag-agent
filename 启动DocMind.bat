@echo off
setlocal
cd /d "%~dp0"

REM Default to the current repository source so a stale packaged snapshot cannot win.
set "DOCMIND_PYTHON=.venv\Scripts\python.exe"
set "DOCMIND_ENTRY=desktop.py"
set "DOCMIND_PACKAGED=dist\DocMind\DocMind.exe"

if exist "%DOCMIND_PYTHON%" if exist "%DOCMIND_ENTRY%" goto run_source
if exist "%DOCMIND_PACKAGED%" goto run_packaged

echo ============================================
echo   DocMind launcher
echo ============================================
echo [ERROR] No runnable DocMind installation was found.
echo.
echo Preferred current source runtime:
echo   %~dp0%DOCMIND_PYTHON%
echo.
echo Create it with:
echo   python -m venv .venv
echo   .venv\Scripts\pip.exe install -r requirements.txt
echo.
echo Packaged fallback checked at:
echo   %~dp0%DOCMIND_PACKAGED%
pause
exit /b 1

:run_source
echo ============================================
echo   DocMind - current source version
echo ============================================
echo Stopping an older packaged DocMind instance if one is still running...
taskkill /IM DocMind.exe /F >nul 2>&1
echo Starting the current repository version...
"%DOCMIND_PYTHON%" "%DOCMIND_ENTRY%"
set "DOCMIND_EXIT=%ERRORLEVEL%"
echo.
echo DocMind exited with code %DOCMIND_EXIT%.
pause
exit /b %DOCMIND_EXIT%

:run_packaged
echo ============================================
echo   DocMind - packaged fallback
echo ============================================
echo [WARNING] The source runtime is unavailable. Starting the packaged snapshot.
echo This copy may be older than the repository source.
start "" "%DOCMIND_PACKAGED%"
exit /b 0
