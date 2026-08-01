#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PROJECT="$REPO_ROOT/src/insertplay-cli/InsertPlay.Cli.csproj"
INSTALLER_SRC="$REPO_ROOT/src/insertplay-cli/assets/installer/install.sh"
UNINSTALLER_SRC="$REPO_ROOT/src/insertplay-cli/assets/installer/uninstall.sh"
DIST_ROOT="$REPO_ROOT/dist/sd-card"
RUNTIME_DIR="$DIST_ROOT/insertplay-cli-runtime"
RID="${1:-linux-x64}"

if [ ! -f "$PROJECT" ]; then
  echo "Error: project not found at $PROJECT" >&2
  exit 1
fi

if [ ! -f "$INSTALLER_SRC" ]; then
  echo "Error: installer not found at $INSTALLER_SRC" >&2
  exit 1
fi

if [ ! -f "$UNINSTALLER_SRC" ]; then
  echo "Error: uninstaller not found at $UNINSTALLER_SRC" >&2
  exit 1
fi

rm -rf "$DIST_ROOT"
mkdir -p "$DIST_ROOT"

echo "Publishing self-contained for RID: $RID"
dotnet publish "$PROJECT" \
  -c Release \
  -r "$RID" \
  -p:PublishSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:DebugType=None \
  -p:DebugSymbols=false \
  --self-contained true \
  -o "$RUNTIME_DIR"

cp "$INSTALLER_SRC" "$DIST_ROOT/install.sh"
chmod +x "$DIST_ROOT/install.sh"
cp "$UNINSTALLER_SRC" "$DIST_ROOT/uninstall.sh"
chmod +x "$DIST_ROOT/uninstall.sh"

echo "Package ready at: $DIST_ROOT"
echo "Contents:"
ls -1 "$DIST_ROOT"
