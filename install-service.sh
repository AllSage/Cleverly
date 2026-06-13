#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/cleverly-ui.service"

if [ ! -f "$SERVICE_FILE" ]; then
  echo "Error: cleverly-ui.service not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing Cleverly UI service..."
echo "Make sure you've edited cleverly-ui.service with your username and paths first!"
echo ""

sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cleverly-ui
sudo systemctl start cleverly-ui
sudo systemctl status cleverly-ui
