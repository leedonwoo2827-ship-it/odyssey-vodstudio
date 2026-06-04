@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOVENV
call venv\Scripts\activate.bat

REM 로컬 단독 사용: 시작 시 로그인 강제하지 않음 (구글 로그인은 화면 ⚙ 에서 선택적으로).
REM 외부 노출 시 .env 에서 AUTH_ENABLED=true 로 설정하세요.
if "%AUTH_ENABLED%"=="" set AUTH_ENABLED=false

echo 서버 시작 중... 잠시 후 브라우저가 열립니다: http://127.0.0.1:7000/vodstudio
start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:7000/vodstudio"

python -m uvicorn app:app --host 127.0.0.1 --port 7000
pause
exit /b 0

:NOVENV
echo.
echo [오류] venv 가 없습니다. 먼저 setup.bat 을 실행하세요.
echo.
pause
exit /b 1
