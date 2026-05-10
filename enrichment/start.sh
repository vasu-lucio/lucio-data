#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting Lucio Enrichment..."

# Backend
cd "$DIR"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt -q
fi

.venv/bin/python -m uvicorn backend.main:app --port 8000 &
BACKEND_PID=$!
echo "Backend running on http://localhost:8000 (PID $BACKEND_PID)"

# Frontend
cd "$DIR/frontend"
if [ ! -d "node_modules" ]; then
  npm install --silent
fi

npm run dev &
FRONTEND_PID=$!
echo "Frontend running on http://localhost:5173 (PID $FRONTEND_PID)"

echo ""
echo "Open http://localhost:5173 in your browser"
echo "Password: $(grep APP_PASSWORD $DIR/backend/.env | cut -d= -f2)"
echo ""
echo "Press Ctrl+C to stop both servers"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT
wait
