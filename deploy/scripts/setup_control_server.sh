#!/usr/bin/env bash
set -euo pipefail

# Cloudhand Control Plane bootstrap (Ubuntu/Debian)
#
# Usage (run as root):
#   bash deploy/scripts/setup_control_server.sh /opt/cloudhand-control-plane
#   bash deploy/scripts/setup_control_server.sh /opt/cloudhand-control-plane --nginx-only
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
MODE="${2:-}"
APP_USER="${APP_USER:-cloudhand}"
SERVICE_NAME="${SERVICE_NAME:-cloudhand-api}"
ENV_FILE="${INSTALL_DIR}/cloudhand-api/.env"

env_value() {
  local key="$1"
  local file="$2"
  local val=""
  val=$(grep -E "^${key}=" "${file}" | head -n1 | cut -d= -f2- || true)

  # Strip surrounding quotes (common in .env files)
  val="${val%\"}"
  val="${val#\"}"
  val="${val%\'}"
  val="${val#\'}"

  echo "${val}"
}

configure_nginx_and_tls() {
  local domains_csv="$1"
  local certbot_email="$2"
  local upstream_port="$3"

  domains_csv=$(echo "${domains_csv}" | tr ',' ' ' | xargs || true)
  if [ -z "${domains_csv}" ]; then
    echo "Skipping nginx/certbot: set CLOUDHAND_API_DOMAIN in ${INSTALL_DIR}/cloudhand-api/.env (e.g. self-deploy.moshq.com)"
    return 0
  fi

  echo "==> Configuring nginx reverse proxy for: ${domains_csv}"
  local nginx_site="/etc/nginx/sites-available/${SERVICE_NAME}"
  cat > "${nginx_site}" <<NGINX
server {
    listen 80;
    server_name ${domains_csv};

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:${upstream_port};
        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSockets
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINX

  mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
  ln -sf "${nginx_site}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
  rm -f /etc/nginx/sites-enabled/default || true

  nginx -t
  systemctl reload nginx || systemctl restart nginx

  if [ -z "${certbot_email}" ] || [ "${certbot_email}" = "you@example.com" ]; then
    echo "Skipping Let's Encrypt: set CERTBOT_EMAIL in ${INSTALL_DIR}/cloudhand-api/.env"
    return 0
  fi

  echo "==> Installing certbot (nginx plugin)..."
  apt-get update
  apt-get install -y certbot python3-certbot-nginx

  # Build certbot -d args from the space-separated domains list.
  local domain_args=()
  for d in ${domains_csv}; do
    domain_args+=("-d" "${d}")
  done

  echo "==> Requesting/renewing Let's Encrypt certificate via certbot..."
  set +e
  certbot --nginx "${domain_args[@]}" \
    --non-interactive --agree-tos --email "${certbot_email}" \
    --redirect --keep-until-expiring
  local rc=$?
  set -e

  if [ "${rc}" -ne 0 ]; then
    echo "WARN: certbot failed (exit code ${rc})."
    echo "      Verify:"
    echo "      - DNS A record points at this server"
    echo "      - ports 80/tcp and 443/tcp are reachable from the internet"
    echo "      Then re-run:"
    echo "        certbot --nginx ${domain_args[*]} --non-interactive --agree-tos --email \"${certbot_email}\" --redirect --keep-until-expiring"
    return 0
  fi

  echo "==> Certbot OK."
}

if [ "${MODE}" = "--nginx-only" ]; then
  if [ ! -f "${INSTALL_DIR}/cloudhand-api/pyproject.toml" ]; then
    echo "ERROR: ${INSTALL_DIR} does not look like the repo root (missing cloudhand-api/pyproject.toml)."
    echo "Place your cloned repo at ${INSTALL_DIR} and re-run."
    exit 1
  fi

  if [ ! -f "${ENV_FILE}" ]; then
    echo "ERROR: missing ${ENV_FILE}"
    echo "Create it (copy from cloudhand-api/.env.example) and set CLOUDHAND_API_DOMAIN + CERTBOT_EMAIL, then re-run."
    exit 1
  fi

  API_BIND_PORT=$(env_value "CLOUDHAND_API_BIND_PORT" "${ENV_FILE}")
  API_BIND_PORT=${API_BIND_PORT:-8000}
  API_DOMAIN=$(env_value "CLOUDHAND_API_DOMAIN" "${ENV_FILE}")
  CERTBOT_EMAIL=$(env_value "CERTBOT_EMAIL" "${ENV_FILE}")

  echo "==> Ensuring nginx is installed..."
  apt-get update
  apt-get install -y nginx
  systemctl enable nginx || true
  systemctl start nginx || true

  configure_nginx_and_tls "${API_DOMAIN}" "${CERTBOT_EMAIL}" "${API_BIND_PORT}"
  exit 0
fi

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
if [ ! -f "${ENV_FILE}" ]; then
  cp "${INSTALL_DIR}/cloudhand-api/.env.example" "${ENV_FILE}"
  echo "Created ${ENV_FILE}"
  echo ""
  echo "STOP: Edit ${ENV_FILE} and then re-run this script."
  exit 0
fi

echo "==> Ensuring CLOUDHAND_KEYS_DIR exists..."
KEYS_DIR=$(grep -E '^CLOUDHAND_KEYS_DIR=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)
KEYS_DIR=${KEYS_DIR:-/var/lib/cloudhand/keys}
mkdir -p "${KEYS_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${KEYS_DIR}" || true

API_BIND_HOST=$(env_value "CLOUDHAND_API_BIND_HOST" "${ENV_FILE}")
API_BIND_HOST=${API_BIND_HOST:-127.0.0.1}
API_BIND_PORT=$(env_value "CLOUDHAND_API_BIND_PORT" "${ENV_FILE}")
API_BIND_PORT=${API_BIND_PORT:-8000}
API_DOMAIN=$(env_value "CLOUDHAND_API_DOMAIN" "${ENV_FILE}")
CERTBOT_EMAIL=$(env_value "CERTBOT_EMAIL" "${ENV_FILE}")


echo "==> Setting up Python venv..."
cd "${INSTALL_DIR}/cloudhand-api"
sudo -u "${APP_USER}" -H bash -lc "python3 -m venv .venv"
sudo -u "${APP_USER}" -H bash -lc "source .venv/bin/activate && pip install -U pip && pip install -e . && pip install psycopg2-binary"

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
ExecStart=${INSTALL_DIR}/cloudhand-api/.venv/bin/uvicorn src.main:app --host ${API_BIND_HOST} --port ${API_BIND_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

echo ""
echo "==> Done."
echo "API bind: ${API_BIND_HOST}:${API_BIND_PORT}"
echo "Try locally:"
echo "  curl -sS http://127.0.0.1:${API_BIND_PORT}/health || true"
echo ""

configure_nginx_and_tls "${API_DOMAIN}" "${CERTBOT_EMAIL}" "${API_BIND_PORT}"

echo "Next steps:"
echo "1) Edit: ${ENV_FILE}"
echo "   - Set DATABASE_URL to point at your Postgres (docker compose uses port 5432)"
echo "   - Set CLOUDHAND_API_KEY (recommended for headless usage)"
echo "   - Set CERTBOT_EMAIL for Let's Encrypt"
echo "   - Set CLOUDHAND_API_DOMAIN (e.g. self-deploy.moshq.com) to auto-configure nginx + TLS"
echo "   - Set OPENAI_API_KEY if you use LLM plan generation"
echo ""
echo "2) If you set CLOUDHAND_API_DOMAIN, re-run this script (or run with --nginx-only) to configure nginx + Let's Encrypt"
echo ""
