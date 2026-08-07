#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PROJECT="$REPO_ROOT/src/insertplay-cli/InsertPlay.Cli.csproj"
PUBLISH_DIR="$REPO_ROOT/publish"
RID="${1:-linux-x64}"

log() {
  echo "[publish] $*"
}

die() {
  echo "Error: $*" >&2
  exit 1
}

if [ ! -f "$PROJECT" ]; then
  die "project not found at $PROJECT"
fi

rm -rf "$PUBLISH_DIR"
mkdir -p "$PUBLISH_DIR/insertplay"

log "Publishing InsertPlay CLI for RID: $RID"
dotnet publish "$PROJECT" \
  -c Release \
  -r "$RID" \
  -p:PublishSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:DebugType=None \
  -p:DebugSymbols=false \
  --self-contained true \
  -o "$PUBLISH_DIR/insertplay"

log "Moving steam-shortcut artwork to $PUBLISH_DIR/assets"
if [ -d "$PUBLISH_DIR/insertplay/assets" ]; then
  mkdir -p "$PUBLISH_DIR/assets"
  mv "$PUBLISH_DIR/insertplay/assets"/* "$PUBLISH_DIR/assets"/
  rmdir "$PUBLISH_DIR/insertplay/assets"
fi

log "Publish complete: $PUBLISH_DIR"
