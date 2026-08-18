#!/usr/bin/env bash
set -euo pipefail

REPO="deathtenk/lg-tv-control"
INSTALL_DIR="$HOME/.local/bin"
BINARY_NAME="lg-tv-control"
ASSET_NAME="lg-tv-control-linux-x86_64"
SERVICE_DIRECTORY_PATH="/etc/systemd/system"
ENV_SOURCE_FILE="./lg-tv-control.env"
ENV_TARGET_DIRECTORY="/home/deck/.config/lg-tv-control"
ENV_TARGET_FILE="$ENV_TARGET_DIRECTORY/env"
ENABLE_DEBUG_SERVICE="${LG_TV_DEBUG:-false}"
SERVICES=(
  "lg-tv-control-resume.service"
  "lg-tv-control-steam-button.service"
  "lg-tv-control-debug.service"
)

mkdir -p "$INSTALL_DIR"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading latest lg-tv-control release..."

curl -fL \
  "https://github.com/$REPO/releases/latest/download/$ASSET_NAME" \
  -o "$TMP_DIR/$ASSET_NAME"

curl -fL \
  "https://github.com/$REPO/releases/latest/download/$ASSET_NAME.sha256" \
  -o "$TMP_DIR/$ASSET_NAME.sha256"

echo "Verifying checksum..."

(
  cd "$TMP_DIR"
  sha256sum -c "$ASSET_NAME.sha256"
)

echo "Installing..."

install -m 0755 \
  "$TMP_DIR/$ASSET_NAME" \
  "$INSTALL_DIR/$BINARY_NAME"

if [[ -f "$ENV_SOURCE_FILE" ]]; then
  echo "Installing environment file..."
  sudo install -d -m 0755 "$ENV_TARGET_DIRECTORY"
  sudo install -m 0600 "$ENV_SOURCE_FILE" "$ENV_TARGET_FILE"
fi

echo
echo "Installed:"
echo "  $INSTALL_DIR/$BINARY_NAME"

echo "Stopping existing services..."

for service in "${SERVICES[@]}"; do
  sudo systemctl stop "$service" 2>/dev/null || true
  sudo systemctl disable "$service" 2>/dev/null || true
done

echo "Installing service definitions..."
sudo install -d -m 0755 "$SERVICE_DIRECTORY_PATH"
sudo install -m 0644 \
  ./lg-tv-control-resume.service \
  "$SERVICE_DIRECTORY_PATH/lg-tv-control-resume.service"
sudo install -m 0644 \
  ./lg-tv-control-steam-button.service \
  "$SERVICE_DIRECTORY_PATH/lg-tv-control-steam-button.service"

if [[ "$ENABLE_DEBUG_SERVICE" == "true" ]]; then
  sudo install -m 0644 \
    ./lg-tv-control-debug.service \
    "$SERVICE_DIRECTORY_PATH/lg-tv-control-debug.service"
fi

sudo systemctl daemon-reload
sudo systemctl enable lg-tv-control-resume.service
sudo systemctl enable lg-tv-control-steam-button.service

if [[ "$ENABLE_DEBUG_SERVICE" == "true" ]]; then
  sudo systemctl enable lg-tv-control-debug.service
fi

echo "lg-tv-control-resume service installed."
echo "lg-tv-control-steam-button service installed."

if [[ "$ENABLE_DEBUG_SERVICE" == "true" ]]; then
  echo "lg-tv-control-debug service installed."
else
  echo "lg-tv-control-debug service skipped. Set LG_TV_DEBUG=true to install it."
fi
