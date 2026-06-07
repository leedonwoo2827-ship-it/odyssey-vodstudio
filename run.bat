@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM Port for this instance. Change here if it conflicts with another app.
set "PORT=7000"

if not exist venv\Scripts\python.exe goto NOVENV

REM Self-heal: agy needs pywinpty to capture its console output (else "agy 호출 실패(exit=0)").
venv\Scripts\python -c "import winpty" 2>nul || (echo pywinpty 설치 중... & venv\Scripts\python -m pip install pywinpty)

REM Make agy/codex visible if installed to common locations (PATH may be stale).
if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%PATH%;%ProgramFiles%\nodejs"
if exist "%LOCALAPPDATA%\Antigravity\agy.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Antigravity"

REM Local single-user: do not force login at start (Google login optional in the gear menu).
if "%AUTH_ENABLED%"=="" set AUTH_ENABLED=false

echo ============================================================
echo   영상공방 (VOD Studio) 시작
echo   URL: http://127.0.0.1:%PORT%/vodstudio
echo   (이 창을 닫으면 종료됩니다)
echo ============================================================
echo.

start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:%PORT%/vodstudio"

venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port %PORT%

echo.
echo 서버가 종료되었습니다.
pause
exit /b 0

:NOVENV
echo.
echo [오류] venv 가 없습니다. 먼저 setup.bat 을 실행하세요.
echo.
pause
exit /b 1
