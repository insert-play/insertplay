#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SD_ROOT="$BASE_DIR"
while [[ ! -f "$SD_ROOT/autorun.sh" && "$SD_ROOT" != "/" ]]; do
    SD_ROOT="$(dirname "$SD_ROOT")"
done
if [[ -f "$SD_ROOT/insertplay-guid-guard.sh" ]]; then
    source "$SD_ROOT/insertplay-guid-guard.sh"
    insertplay_guard_verify_or_exit "$SD_ROOT" "$@"
fi

GAME="$BASE_DIR/game/nfs.exe"
PREFIX="$BASE_DIR/prefix"

STEAM_RUNTIME="$BASE_DIR/runtime/SteamLinuxRuntime_4/_v2-entry-point"
PROTON="$BASE_DIR/proton/proton"

export STEAM_COMPAT_DATA_PATH="$PREFIX"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_RUNTIME_PREFER_HOST_LIBRARIES=0

# Overrides necessários para o patch Linux
export WINEDLLOVERRIDES="dinput8=n,b;d3dx9_34=n,b"

cd "$BASE_DIR/game"

exec "$STEAM_RUNTIME" \
    -- "$PROTON" run "$GAME"
