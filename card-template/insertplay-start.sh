#!/usr/bin/env bash
# Launches the game on this card. The plugin runs it as
#   /bin/bash "<card>/insertplay-start.sh" --guid <guid>
#
# Edit the game block below for your emulator and game. See docs/card-format.md.
set -e

# This script lives at the card root, so the card root is the script's own folder.
SD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Refuses to start when the shortcut belongs to another card. Keep it.
source "$SD_ROOT/insertplay-guid-guard.sh"
insertplay_guard_verify_or_exit "$SD_ROOT" "$@"

# --- game: edit below ------------------------------------------------------------------
# An emulator AppImage on the card, with its data (settings, BIOS, memory cards, saves)
# in its own folder on the card, so everything travels with it. DuckStation keeps its data
# in $XDG_DATA_HOME/duckstation. For a game with several discs, point GAME to a .m3u that
# lists them, one file name per line.
EMULATOR="$SD_ROOT/duckstation/DuckStation-x64.AppImage"
GAME="$SD_ROOT/game.cue"

export XDG_DATA_HOME="$SD_ROOT"

# copying to the card can drop the exec bit
chmod +x "$EMULATOR" 2>/dev/null || true
cd "$SD_ROOT"
exec "$EMULATOR" -nogui -fullscreen "$GAME"
