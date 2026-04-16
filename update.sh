#!/bin/bash
# update.sh — pull latest Kairos Agent image and restart
set -e

echo "▶ Stopping container..."
docker rm -f kairos-agent 2>/dev/null || true

echo "▶ Pulling latest image..."
docker-compose pull

echo "▶ Starting container..."
docker-compose up -d

echo "✅ Kairos Agent updated and running at http://localhost:7432"
