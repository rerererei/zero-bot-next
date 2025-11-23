#!/bin/bash
set -e

echo "[deploy] start $(date)"

cd /home/ec2-user/zero-bot-next

echo "[deploy] git fetch/reset..."
git fetch origin main
git reset --hard origin/main

echo "[deploy] activate venv..."
source venv/bin/activate

echo "[deploy] install requirements..."
pip install -r requirements.txt

echo "[deploy] restart service..."
sudo systemctl restart zerobot

# 実行確認 
echo "[deploy] checking service status..."
sleep 3

if ! sudo systemctl is-active --quiet zerobot; then
  echo "[deploy] ERROR: zerobot is not active"
  sudo systemctl status zerobot -n 50 || true
  exit 1
fi

echo "[deploy] done $(date)"
