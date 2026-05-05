#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo bash scripts/server/setup_server.sh <github_repo_url> [branch]
# Example:
#   sudo bash scripts/server/setup_server.sh https://github.com/your-user/ea-ai-platform.git main

REPO_URL="${1:-}"
BRANCH="${2:-main}"
APP_DIR="/opt/ea-ai-platform"
RUN_USER="${SUDO_USER:-$USER}"

if [[ -z "${REPO_URL}" ]]; then
  echo "ERROR: Missing GitHub repository URL."
  echo "Usage: sudo bash scripts/server/setup_server.sh <github_repo_url> [branch]"
  exit 1
fi

echo "[1/9] Installing OS packages..."
apt-get update
apt-get install -y git curl python3 python3-venv python3-pip nodejs npm build-essential

if ! command -v ollama >/dev/null 2>&1; then
  echo "[2/9] Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "[2/9] Ollama already installed."
fi

echo "[3/9] Preparing app directory..."
mkdir -p /opt
if [[ ! -d "${APP_DIR}/.git" ]]; then
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
else
  cd "${APP_DIR}"
  git fetch origin
  git checkout "${BRANCH}"
  git pull --ff-only origin "${BRANCH}"
fi

cd "${APP_DIR}"
chown -R "${RUN_USER}:${RUN_USER}" "${APP_DIR}"

echo "[4/9] Backend Python environment..."
sudo -u "${RUN_USER}" python3 -m venv "${APP_DIR}/backend/.venv"
sudo -u "${RUN_USER}" "${APP_DIR}/backend/.venv/bin/pip" install --upgrade pip
sudo -u "${RUN_USER}" "${APP_DIR}/backend/.venv/bin/pip" install -r "${APP_DIR}/backend/requirements.txt"

echo "[5/9] Frontend Node dependencies..."
cd "${APP_DIR}/frontend"
if [[ -f package-lock.json ]]; then
  sudo -u "${RUN_USER}" npm ci
else
  sudo -u "${RUN_USER}" npm install
fi
sudo -u "${RUN_USER}" npm run build

echo "[6/9] Preparing environment files..."
cd "${APP_DIR}"
if [[ ! -f "${APP_DIR}/backend/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/backend/.env"
  chown "${RUN_USER}:${RUN_USER}" "${APP_DIR}/backend/.env"
  echo "Created backend/.env from .env.example. Edit it before production use."
fi

echo "[7/9] Installing systemd service files..."
cp "${APP_DIR}/deploy/systemd/ea-ollama.service" /etc/systemd/system/ea-ollama.service
cp "${APP_DIR}/deploy/systemd/ea-backend.service" /etc/systemd/system/ea-backend.service
cp "${APP_DIR}/deploy/systemd/ea-frontend.service" /etc/systemd/system/ea-frontend.service
systemctl daemon-reload

echo "[8/9] Enabling auto-start services..."
systemctl disable --now ollama.service >/dev/null 2>&1 || true
systemctl enable ea-ollama.service
systemctl enable ea-backend.service
systemctl enable ea-frontend.service

echo "[9/9] Starting services..."
systemctl restart ea-ollama.service
systemctl restart ea-backend.service
systemctl restart ea-frontend.service

echo
echo "Server bootstrap complete."
echo "Run these checks:"
echo "  systemctl status ea-ollama ea-backend ea-frontend --no-pager"
echo "  journalctl -u ea-backend -n 100 --no-pager"
echo "  journalctl -u ea-frontend -n 100 --no-pager"
