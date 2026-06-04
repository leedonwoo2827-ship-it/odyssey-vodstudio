@echo off
REM ============================================================
REM 영상공방 (Odyssey VOD Studio) — Windows 실행 스크립트
REM venv 활성화 후 웹 서버를 띄우고 /vodstudio 를 브라우저로 엽니다.
REM ============================================================
cd /d %~dp0
call venv\Scripts\activate.bat

REM 로컬 단독 사용: 시작 시 로그인 강제하지 않음 (구글 로그인은 화면 ⚙ 에서 선택적으로).
REM .env 에 AUTH_ENABLED 가 있으면 그 값이 우선합니다. 외부 노출 시 .env에서 true 로 설정하세요.
if "%AUTH_ENABLED%"=="" set AUTH_ENABLED=false

REM 브라우저 자동 오픈 (서버가 뜨는 데 잠깐 걸리므로 약간 지연)
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:7000/vodstudio"

python -m uvicorn app:app --host 127.0.0.1 --port 7000
