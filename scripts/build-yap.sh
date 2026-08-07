#!/usr/bin/env bash
# Build the yap engine binary (finnvoor/yap, CC0) from pinned source.
# Requires Xcode Command Line Tools (swift). Output: bin/yap
set -euo pipefail

PIN="7c19dd37b4e3689b5e78425548a43abaf5c0ab91"  # upstream main, 2026-07-20
SRC="${YAP_SRC_DIR:-$(mktemp -d -t yap-src)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if command -v yap >/dev/null 2>&1; then
  echo "yap already installed at $(command -v yap) — nothing to build (brew install yap is the primary path)"
  exit 0
fi

if [ ! -d "$SRC/.git" ]; then
  git clone --quiet https://github.com/finnvoor/yap "$SRC"
  git -C "$SRC" checkout --quiet "$PIN"
fi

(cd "$SRC" && swift build -c release)
mkdir -p "$ROOT/bin"
cp "$SRC/.build/release/yap" "$ROOT/bin/yap"
echo "built $ROOT/bin/yap"
