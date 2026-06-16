#!/usr/bin/env bash
# deploy.sh — stamp the current git release into .env, then rebuild + restart.
# Run this on app-host after pulling/merging code changes.
set -euo pipefail

cd "$(dirname "$0")"

VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "dev")
echo "→ stamping APP_VERSION=${VERSION} into .env"

# Replace or append APP_VERSION in .env (macOS + Linux compatible).
if grep -q '^APP_VERSION=' .env 2>/dev/null; then
  sed -i "s/^APP_VERSION=.*/APP_VERSION=${VERSION}/" .env
else
  echo "APP_VERSION=${VERSION}" >> .env
fi

echo "→ building & deploying"
docker compose up -d --build

echo "✓ done — app running with ${VERSION}"
