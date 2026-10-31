#!/usr/bin/env bash
# Launches the game on this card. The plugin runs it as
#   /bin/bash "<card>/insertplay-start.sh" --guid <guid>
# and the guid check below refuses to start when the shortcut belongs to another card
# (for example, a different card was inserted in the same slot).
#
# Edit GAME (and, if needed, the Proton/runtime paths and WINEDLLOVERRIDES) for your game.
# See docs/card-format.md.
set -e

# The card root is the mount point (the plugin's {SD_ROOT}), wherever this script lives on
# the card; without findmnt, the script's own folder.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SD_ROOT="$(findmnt -n -o TARGET --target "$SCRIPT_DIR" 2>/dev/null || true)"
SD_ROOT="${SD_ROOT:-$SCRIPT_DIR}"

# --- guid check: keep it ---------------------------------------------------------------
expected_guid=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
	if [[ "${args[$i]}" == "--guid" && $((i + 1)) -lt ${#args[@]} ]]; then
		expected_guid="${args[$((i + 1))]}"
	fi
done
if [[ -n "$expected_guid" ]]; then
	# same lookup as the plugin: card root first, then insertplay/ (older cards); any key case
	identity="$SD_ROOT/shortcut-identity.json"
	[[ -f "$identity" ]] || identity="$SD_ROOT/insertplay/shortcut-identity.json"
	actual_guid="$(grep -Eio '"guid"[[:space:]]*:[[:space:]]*"[^"]+"' "$identity" 2>/dev/null \
		| head -n 1 | sed -E 's/.*"([^"]+)"/\1/')"
	if [[ "${actual_guid,,}" != "${expected_guid,,}" ]]; then
		echo "[insertplay-start] wrong card: the shortcut expects guid=$expected_guid, this card has guid=${actual_guid:-<none>}" >&2
		command -v notify-send >/dev/null 2>&1 \
			&& notify-send "InsertPlay" "This shortcut doesn't match the inserted SD card." >/dev/null 2>&1
		exit 0
	fi
fi

# --- game: edit below ------------------------------------------------------------------
# A Windows game run with the Proton and the Steam Linux Runtime copied to the card, and the
# Wine prefix kept on the card, so saves travel with it.
GAME="$SD_ROOT/game/Game.exe"
PREFIX="$SD_ROOT/prefix"
STEAM_RUNTIME="$SD_ROOT/runtime/SteamLinuxRuntime_4/_v2-entry-point"
PROTON="$SD_ROOT/proton/proton"

export STEAM_COMPAT_DATA_PATH="$PREFIX"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_RUNTIME_PREFER_HOST_LIBRARIES=0
# export WINEDLLOVERRIDES="dinput8=n,b"

mkdir -p "$PREFIX"
cd "$(dirname "$GAME")"
exec "$STEAM_RUNTIME" -- "$PROTON" run "$GAME"
