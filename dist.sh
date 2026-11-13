#!/usr/bin/env bash
# Bouwt dist/pricewatch-<versie>.tar.gz: alleen wat een andere machine nodig
# heeft. Tests, fixtures en caches blijven thuis.
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERSION=$(python3 -c "import json;print(json.load(open('$SRC_DIR/manifest.json'))['version'])")
NAME=pricewatch-$VERSION
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/$NAME"
for item in manifest.json Panel.qml install.sh README.md LICENSE bin src systemd; do
  [[ -e $SRC_DIR/$item ]] && cp -a "$SRC_DIR/$item" "$STAGE/$NAME/"
done
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE" -name '*.pyc' -delete
chmod +x "$STAGE/$NAME/install.sh" "$STAGE/$NAME/bin/"*

mkdir -p "$SRC_DIR/dist"
tar -czf "$SRC_DIR/dist/$NAME.tar.gz" -C "$STAGE" "$NAME"

printf '\033[1m%s\033[0m\n' "dist/$NAME.tar.gz"
sha256sum "$SRC_DIR/dist/$NAME.tar.gz" | sed 's| .*/| |'
printf '  uitpakken met: tar -xzf %s -C ~/.config/omarchy/plugins-staging\n' "dist/$NAME.tar.gz"
