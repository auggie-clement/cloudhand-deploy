#!/usr/bin/env bash
set -e

# Install Docker + Docker Compose v2 on Ubuntu 22.04
# This script fixes the missing docker-compose-plugin issue.

echo "Removing old Docker packages (if any)..."
sudo apt remove -y docker.io docker-doc docker-compose || true
sudo apt autoremove -y || true

echo "Installing prerequisites..."
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release

echo "Adding Docker GPG key..."
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo "Adding Docker apt repository..."
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu \
$(lsb_release -cs) stable" \
| sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "Installing Docker Engine + Compose plugin..."
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "Enabling Docker service..."
sudo systemctl enable --now docker

echo "Verifying installation..."
docker version
docker compose version

echo "Docker + Docker Compose v2 installed successfully."
