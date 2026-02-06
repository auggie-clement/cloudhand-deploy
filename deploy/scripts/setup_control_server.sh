#!/usr/bin/env bash
set -euo pipefail

# Cloudhand Control Plane bootstrap (Ubuntu/Debian)
#
# Usage (run as root):
#   bash deploy/scripts/setup_control_server.sh /opt/cloudhand-control-plane
#
# This script:
# - installs required packages
# - starts Postgres via docker compose (in deploy/docker-compose.yml)
# - sets up Python venv + installs deps
# - runs Alembic migrations
# - installs a systemd service bound to 127.0.0.1:8000
#
# You still need to:
# - copy cloudhand-api/.env.example -> cloudhand-api/.env and fill values
# - (optional) configure nginx + TLS for the API endpoint

INSTALL_DIR="${1:-/opt/cloudhand-control-plane}"
APP_USER="${APP_USER:-cloudhand}"
SERVICE_NAME="${SERVICE_NAME:-cloudhand-api}"

echo "==> Installing system packages..."
apt-get update
apt-get install -y \
  git curl unzip gnupg lsb-release \
  python3 python3-venv python3-pip \
  nginx 

systemctl enable --now docker

if ! command -v terraform >/dev/null 2>&1; then
  echo "==> Installing Terraform (HashiCorp apt repo) ..."
  curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor > /usr/share/keyrings/hashicorp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" > /etc/apt/sources.list.d/hashicorp.list
  apt-get update
  apt-get install -y terraform
fi

echo "==> Ensuring application user exists: ${APP_USER}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/opt/${APP_USER}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> Preparing install dir: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${INSTALL_DIR}"

echo ""
echo "IMPORTANT:"
echo " - This script expects your repo to be located at: ${INSTALL_DIR}"
echo " - If you cloned it elsewhere, move it there (or edit the systemd unit after install)."
echo ""

if [ ! -f "${INSTALL_DIR}/cloudhand-api/pyproject.toml" ]; then
  echo "ERROR: ${INSTALL_DIR} does not look like the repo root (missing cloudhand-api/pyproject.toml)."
  echo "Place your cloned repo at ${INSTALL_DIR} and re-run."
  exit 1
fi

echo "==> Starting Postgres (docker compose)..."
cd "${INSTALL_DIR}/deploy"
docker compose up -d postgres

echo "==> Creating API env file if missing..."
if [ ! -f "${INSTALL_DIR}/cloudhand-api/.env" ]; then
  cp "${INSTALL_DIR}/cloudhand-api/.env.example" "${INSTALL_DIR}/cloudhand-api/.env"
  echo "Created ${INSTALL_DIR}/cloudhand-api/.env"
  echo ""
  echo "STOP: Edit ${INSTALL_DIR}/cloudhand-api/.env and then re-run this script."
  exit 0
fi

echo "==> Ensuring CLOUDHAND_KEYS_DIR exists..."
KEYS_DIR=$(grep -E '^CLOUDHAND_KEYS_DIR=' "${INSTALL_DIR}/cloudhand-api/.env" | head -n1 | cut -d= -f2- || true)
KEYS_DIR=${KEYS_DIR:-/var/lib/cloudhand/keys}
mkdir -p "${KEYS_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${KEYS_DIR}" || true


echo "==> Setting up Python venv..."
cd "${INSTALL_DIR}/cloudhand-api"
sudo -u "${APP_USER}" -H bash -lc "python3 -m venv .venv"
sudo -u "${APP_USER}" -H bash -lc "source .venv/bin/activate && pip install -U pip && pip install -e ."

echo "==> Running DB migrations..."
sudo -u "${APP_USER}" -H bash -lc "cd ${INSTALL_DIR}/cloudhand-api && source .venv/bin/activate && alembic upgrade head"

echo "==> Installing systemd unit..."
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "${UNIT_PATH}" <<UNIT
[Unit]
Description=Cloudhand API (control plane)
After=network.target docker.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${INSTALL_DIR}/cloudhand-api
EnvironmentFile=${INSTALL_DIR}/cloudhand-api/.env
ExecStart=${INSTALL_DIR}/cloudhand-api/.venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

echo ""
echo "==> Done."
echo "API should be reachable locally at: http://127.0.0.1:8000"
echo ""
echo "Next steps:"
echo "1) Edit: ${INSTALL_DIR}/cloudhand-api/.env"
echo "   - Set DATABASE_URL to point at your Postgres (docker compose uses port 5432)"
echo "   - Set CLOUDHAND_API_KEY (recommended for headless usage)"
echo "   - Set CERTBOT_EMAIL for Let's Encrypt"
echo "   - Set OPENAI_API_KEY if you use LLM plan generation"
echo ""
echo "2) (optional) Configure nginx reverse proxy + TLS for the API"
echo "   - See deploy/nginx/cloudhand-api.conf"
echo ""
