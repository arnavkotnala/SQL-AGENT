@echo off
REM LoopSQL AI - one-click local start (Windows). Runs on http://127.0.0.1:8000 (this computer only).
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo Python 3.11+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
where npm >nul 2>nul || (echo Node.js 18+ is required: https://nodejs.org/ & pause & exit /b 1)

if not exist backend\.venv\Scripts\python.exe (
  echo [1/4] Creating Python environment...
  python -m venv backend\.venv || exit /b 1
)
echo [2/4] Installing backend packages...
backend\.venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt || exit /b 1

if not exist frontend\dist\index.html (
  echo [3/4] Building the web interface...
  pushd frontend
  call npm ci --no-audit --no-fund || (popd & exit /b 1)
  call npm run build || (popd & exit /b 1)
  popd
)

if not exist backend\.env (
  copy backend\.env.example backend\.env >nul
  echo.
  echo  backend\.env was created. Open it and set GEMINI_API_KEY ^(https://aistudio.google.com/apikey^),
  echo  then run start.bat again.
  notepad backend\.env
  pause
  exit /b 0
)

echo [4/4] Starting LoopSQL AI at http://127.0.0.1:8000
start "" http://127.0.0.1:8000
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
