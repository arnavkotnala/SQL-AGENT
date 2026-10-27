#!/usr/bin/env sh
# LoopSQL AI - local start (macOS/Linux). Serves http://127.0.0.1:8000
set -e
cd "$(dirname "$0")"
[ -x backend/.venv/bin/python ] || python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -q -r backend/requirements.txt
[ -f frontend/dist/index.html ] || (cd frontend && npm ci --no-audit --no-fund && npm run build)
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  echo "Created backend/.env - set GEMINI_API_KEY in it, then run ./start.sh again."
  exit 0
fi
cd backend && exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
