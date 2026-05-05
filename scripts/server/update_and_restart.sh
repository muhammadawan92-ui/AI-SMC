#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/ea-ai-platform"
BRANCH="${1:-main}"
RUN_USER="${SUDO_USER:-$USER}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "ERROR: ${APP_DIR} is not a git repository."
  exit 1
fi

echo "[1/5] Pulling latest code..."
cd "${APP_DIR}"
git fetch origin
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

echo "[2/5] Updating backend dependencies..."
sudo -u "${RUN_USER}" "${APP_DIR}/backend/.venv/bin/pip" install -r "${APP_DIR}/backend/requirements.txt"

echo "[3/5] Updating frontend dependencies and build..."
cd "${APP_DIR}/frontend"
if [[ -f package-lock.json ]]; then
  sudo -u "${RUN_USER}" npm ci
else
  sudo -u "${RUN_USER}" npm install
fi
sudo -u "${RUN_USER}" npm run build

echo "[4/5] Restarting services..."
systemctl restart ea-backend.service
systemctl restart ea-frontend.service

echo "[5/5] Done."
systemctl status ea-backend.service ea-frontend.service --no-pager
