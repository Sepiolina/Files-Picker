#!/usr/bin/env bash
# Build the engine for this computer (into gui/bin/) so the GUI can find it.
#   ./build.sh          build for this machine
#   ./build.sh all      also cross-build Windows / macOS / Linux into dist/
set -euo pipefail
cd "$(dirname "$0")/engine"

exe=picker; [[ "$(go env GOOS)" == "windows" ]] && exe=picker.exe
go build -trimpath -ldflags "-s -w" -o "../gui/bin/$exe" ./cmd/picker
echo "built gui/bin/$exe"

if [[ "${1:-}" == "all" ]]; then
  for target in windows/amd64 windows/arm64 darwin/amd64 darwin/arm64 linux/amd64 linux/arm64; do
    os=${target%/*}; arch=${target#*/}; ext=""; [[ $os == windows ]] && ext=.exe
    GOOS=$os GOARCH=$arch CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" \
      -o "../dist/engine/$os-$arch/picker$ext" ./cmd/picker
    echo "built dist/engine/$os-$arch/picker$ext"
  done
fi
