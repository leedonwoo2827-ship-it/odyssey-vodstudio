@echo off
REM ============================================================
REM 영상공방 (Odyssey VOD Studio) — Windows 설치 스크립트
REM venv 생성 + 의존성 설치 (notebooklm-mcp-cli, PyMuPDF 포함)
REM ============================================================
cd /d %~dp0

echo [1/3] 가상환경(venv) 생성...
if not exist venv (
  python -m venv venv
)

echo [2/3] 의존성 설치...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [3/5] .env 준비...
if not exist .env (
  copy .env.example .env >nul
  echo   .env 파일을 생성했습니다. Google OAuth 키 등을 채워주세요.
  echo   ^(knowledge\google-oauth-setup.md 참고^)
)

echo [4/5] mp4maker 체크아웃...
if not exist mp4maker\mp4maker (
  git clone --depth 1 https://github.com/leedonwoo2827-ship-it/mp4maker.git mp4maker
)

echo [5/5] mp4maker 환경 점검 ^(ffmpeg/폰트/패키지^)...
pushd mp4maker
python -m mp4maker --probe
popd
if errorlevel 1 (
  echo   ^[주의^] ffmpeg/ffprobe 가 PATH에 없을 수 있습니다.
  echo   https://ffmpeg.org 에서 설치 후 PATH에 추가하세요. ^(winget install Gyan.FFmpeg^)
)

echo.
echo 설치 완료!
echo  - NotebookLM 로그인:  venv\Scripts\nlm login
echo  - 서버 실행:          run.bat  ^(http://127.0.0.1:7000/vodstudio^)
pause
