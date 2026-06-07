@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "LOG=%~dp0setup_log.txt"
echo setup start > "%LOG%"

echo ============================================================
echo   영상공방 (VOD Studio) - 설치
echo ============================================================
echo.

REM 1) Python (py -3 우선, 없으면 python)
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto NOPY
echo [OK] Python: %PY%

REM 2) 가상환경
if not exist "venv\Scripts\python.exe" (
  echo [1/6] venv 생성...
  %PY% -m venv venv
) else (
  echo [1/6] venv 이미 있음 - 건너뜀
)
set "VPY=%~dp0venv\Scripts\python.exe"
if not exist "%VPY%" goto VENVFAIL

REM 3) 라이브러리 (로컬 TTS·임베딩·pywinpty 포함)
echo [2/6] 라이브러리 설치... (처음엔 수 분)
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto PIPFAIL
echo step:pip-ok >> "%LOG%"

REM 4) Antigravity CLI (agy) - Gemini
echo [3/6] agy (Gemini/Google) 확인...
where agy >nul 2>nul || call :INSTALL_AGY
where agy >nul 2>nul && (echo       [OK] agy 설치됨) || (echo       [참고] agy 미설치 - 나중에 화면 [로그인] 버튼에서 설치/로그인 가능)
echo step:agy-done >> "%LOG%"

REM 5) OpenAI Codex CLI (codex) - ChatGPT (Node 필요)
echo [4/6] codex (OpenAI/ChatGPT) 확인...
where codex >nul 2>nul || call :INSTALL_CODEX
where codex >nul 2>nul && (echo       [OK] codex 설치됨) || (echo       [참고] codex 미설치 - Node 새로 깔렸으면 setup.bat 한 번 더 실행)
echo step:codex-done >> "%LOG%"

REM 5b) 로컬 TTS 모델 다운로드 (HuggingFace, ~380MB) — 없을 때만
echo [5b] TTS 모델 확인 (assets\onnx)...
if exist "assets\onnx\vocoder.onnx" (
  echo       [OK] TTS 모델 있음
) else (
  echo       모델 없음 - HuggingFace에서 다운로드 ^(~380MB, 시간 걸림^)...
  powershell -ExecutionPolicy Bypass -File "scripts\setup_assets.ps1"
)
echo step:models-done >> "%LOG%"

REM 6) mp4maker 체크아웃 + 점검, .env 준비
echo [5/6] mp4maker 체크아웃 + 점검...
if not exist "mp4maker\mp4maker" git clone --depth 1 https://github.com/leedonwoo2827-ship-it/mp4maker.git mp4maker
if not exist .env copy .env.example .env >nul
echo [6/6] 환경 점검...
pushd mp4maker
"%VPY%" -m mp4maker --probe
popd
echo step:mp4maker-done >> "%LOG%"

echo.
echo ============================================================
echo   설치 완료!  다음: run.bat 더블클릭
echo   화면에서 공급자(OpenAI/Gemini) 고르고 [로그인(터미널)]:
echo     - OpenAI : codex login   (ChatGPT 로그인)
echo     - Gemini : agy           (Google 로그인)
echo   API 키 불필요 (계정 할당량 사용).
echo ============================================================
echo setup end >> "%LOG%"
echo.
pause
exit /b 0

:INSTALL_AGY
echo       agy 설치 시도 (인터넷 필요)...
curl -fsSL https://antigravity.google/cli/install.cmd -o "%TEMP%\agy_install.cmd"
if exist "%TEMP%\agy_install.cmd" cmd /c "%TEMP%\agy_install.cmd"
del "%TEMP%\agy_install.cmd" >nul 2>nul
goto :eof

:INSTALL_CODEX
where npm >nul 2>nul || call :INSTALL_NODE
where npm >nul 2>nul && (echo       codex 설치 중 (npm i -g @openai/codex)... & cmd /c npm i -g @openai/codex)
goto :eof

:INSTALL_NODE
echo       Node.js 없음 - winget 설치 시도 (UAC 창이 뜰 수 있음)...
where winget >nul 2>nul && winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%PATH%;%ProgramFiles%\nodejs"
goto :eof

:NOPY
echo [오류] Python 3.11+ 가 필요합니다. https://www.python.org/downloads/
echo        설치 시 "Add Python to PATH" 체크 후 다시 실행하세요.
echo python-missing >> "%LOG%"
pause
exit /b 1

:VENVFAIL
echo [오류] venv 생성 실패. Python 재설치 후 다시 시도하세요.
pause
exit /b 1

:PIPFAIL
echo [오류] pip 설치 실패. 위의 빨간 메시지를 확인하세요.
pause
exit /b 1
