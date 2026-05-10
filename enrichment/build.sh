#!/usr/bin/env bash
set -e

echo "==> Installing frontend dependencies"
cd frontend
npm ci

echo "==> Building frontend"
npm run build

echo "==> Copying dist to backend"
rm -rf ../backend/dist
cp -r dist ../backend/dist

cd ..

echo "==> Installing backend dependencies"
cd backend
pip install -r requirements.txt

echo "==> Build complete"
