#!/usr/bin/env bash
set -euo pipefail

XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
RUNTIME_ENV="$XDG_CONFIG_HOME/insertplay/runtime.env"

if [[ -f "$RUNTIME_ENV" ]]; then
	# shellcheck disable=SC1090
	source "$RUNTIME_ENV"
fi

GAME_ID="${INSERTPLAY_SD_GAME_ID:-}"
if [[ -z "$GAME_ID" ]]; then
	echo "InsertPlay runtime configuration not found. Run 'insertplay setup' first." >&2
	exit 1
fi

exec steam "steam://rungameid/$GAME_ID"
