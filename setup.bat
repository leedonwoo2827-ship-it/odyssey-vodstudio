@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  영상공방 (Odyssey VOD Studio) - 설치
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 goto NOPY

echo [1/6] 가상환경(venv) 생성...
if not exist venv\Scripts\python.exe python -m venv venv

echo [2/6] venv 활성화...
call venv\Scripts\activate.bat

echo [3/6] 의존성 설치... (수 분 소요)
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [4/6] .env 준비...
if not exist .env copy .env.example .env >nul

echo [5/6] mp4maker 체크아웃...
if not exist mp4maker\mp4maker git clone --depth 1 https://github.com/leedonwoo2827-ship-it/mp4maker.git mp4maker

echo [6/6] mp4maker 환경 점검 (ffmpeg/폰트/패키지)...
pushd mp4maker
python -m mp4maker --probe
popd

echo.
echo ============================================================
echo  설치 완료!
echo   - NotebookLM 로그인:  venv\Scripts\nlm login
echo   - 서버 실행:          run.bat   (http://127.0.0.1:7000/vodstudio)
echo ============================================================
echo.
pause
exit /b 0

:NOPY
echo.
echo [오류] python 을 찾을 수 없습니다.
echo   https://www.python.org 에서 Python 3.11+ 설치 후
echo   설치 시 "Add python.exe to PATH" 를 체크하세요.
echo.
pause
exit /b 1
