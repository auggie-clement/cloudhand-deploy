#!/usr/bin/env bash
set -euo pipefail

echo "=== Docker + Compose hard reset for Ubuntu 22.04 / 24.04 ==="

echo "[1/7] Stopping services if running..."
sudo systemctl stop docker 2>/dev/null || true
sudo systemctl stop containerd 2>/dev/null || true

echo "[2/7] Removing snap-based Docker/containerd if present..."
if command -v snap >/dev/null 2>&1; then
  sudo snap remove docker 2>/dev/null || true
  sudo snap remove containerd 2>/dev/null || true
fi

echo "[3/7] Purging all apt-managed Docker / containerd variants..."
sudo apt-get update
sudo apt-get purge -y \
  docker-ce docker-ce-cli docker-ce-rootless-extras docker-buildx-plugin docker-compose-plugin \
  docker.io docker-doc docker-compose \
  containerd.io containerd runc \
  moby-engine moby-cli moby-containerd moby-runc || true

sudo apt-get autoremove -y --purge || true

echo "[4/7] Removing leftover directories..."
sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker /etc/containerd

echo "[5/7] Fixing dpkg / apt state..."
sudo dpkg --configure -a
sudo apt-get -f install -y

echo "[6/7] Adding Docker official repository..."
sudo install -m 0755 -d /etc/apt/keyrings
sudo rm -f /etc/apt/keyrings/docker.gpg /etc/apt/sources.list.d/docker.list

curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

CODENAME="$(. /etc/os-release && echo $VERSION_CODENAME)"

echo "Using Ubuntu codename: $CODENAME"

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu \
$CODENAME stable" \
| sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update

echo "[6.5/7] Pinning Docker packages to avoid Ubuntu containerd conflicts..."
sudo tee /etc/apt/preferences.d/docker-pin >/dev/null <<'EOF'
Package: containerd.io docker-ce docker-ce-cli docker-compose-plugin docker-buildx-plugin
Pin: origin download.docker.com
Pin-Priority: 1001
EOF

echo "[7/7] Installing Docker Engine + Compose v2..."
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "Enabling Docker service..."
sudo systemctl enable --now docker

echo "=== Verification ==="
docker version
docker compose version
apt-cache policy containerd.io | sed -n '1,20p'

echo "=== SUCCESS: Docker + Compose v2 installed cleanly ==="
