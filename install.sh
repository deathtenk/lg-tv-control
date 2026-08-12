#!/usr/bin/env bash
set -euo pipefail

REPO="deathtenk/lg-tv-control"
INSTALL_DIR="$HOME/.local/bin"
BINARY_NAME="lg-tv-control"
ASSET_NAME="lg-tv-control-linux-x86_64"

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

echo
echo "Installed:"
echo "  $INSTALL_DIR/$BINARY_NAME"
