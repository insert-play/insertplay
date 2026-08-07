#!/usr/bin/env bash
set -euo pipefail

XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
RUNTIME_ENV="$XDG_CONFIG_HOME/insertplay/runtime.env"
SD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_SHORTCUTS_DIR="$XDG_CONFIG_HOME/insertplay/shortcuts"

log() {
	echo "[insertplay autorun] $*" >&2
}

is_positive_integer() {
	[[ "$1" =~ ^[0-9]+$ ]] && (( "$1" > 0 ))
}

is_guid() {
	[[ "$1" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]
}

extract_guid_from_identity() {
	local identity_file="$1"
	local guid

	guid="$(grep -Eo '"guid"[[:space:]]*:[[:space:]]*"[^"]+"' "$identity_file" 2>/dev/null | head -n 1 | sed -E 's/.*"([^"]+)"/\1/' || true)"
	printf '%s\n' "$guid"
}

resolve_runtime_shortcut_env() {
	local identity_file=""
	local guid=""

	if [[ -f "$SD_ROOT/shortcut-identity.json" ]]; then
		identity_file="$SD_ROOT/shortcut-identity.json"
	elif [[ -f "$SD_ROOT/insertplay/shortcut-identity.json" ]]; then
		identity_file="$SD_ROOT/insertplay/shortcut-identity.json"
	fi

	if [[ -z "$identity_file" ]]; then
		log "Shortcut identity file not found on SD card. Falling back to generic shortcut."
		return 1
	fi

	guid="$(extract_guid_from_identity "$identity_file")"
	if [[ -z "$guid" ]]; then
		log "Shortcut identity file does not contain a guid. Falling back to generic shortcut."
		return 1
	fi

	if ! is_guid "$guid"; then
		log "Shortcut identity guid is invalid: $guid. Falling back to generic shortcut."
		return 1
	fi

	guid="$(printf '%s' "$guid" | tr '[:upper:]' '[:lower:]')"
	local local_env="$RUNTIME_SHORTCUTS_DIR/$guid.env"
	if [[ ! -f "$local_env" ]]; then
		log "No local runtime shortcut mapping for guid=$guid. Falling back to generic shortcut."
		return 1
	fi

	printf '%s\n' "$local_env"
}

is_process_running() {
	local process_name="$1"
	pgrep -f -- "$process_name" >/dev/null 2>&1
}

wait_for_process() {
	local process_name="$1"
	local timeout_seconds="$2"

	for ((i=0; i<timeout_seconds; i++)); do
		if is_process_running "$process_name"; then
			return 0
		fi
		sleep 1
	done

	return 1
}

if [[ -f "$RUNTIME_ENV" ]]; then
	# shellcheck disable=SC1090
	source "$RUNTIME_ENV"
fi

RUNTIME_SHORTCUT_ENV=""
if RUNTIME_SHORTCUT_ENV="$(resolve_runtime_shortcut_env)"; then
	# shellcheck disable=SC1090
	source "$RUNTIME_SHORTCUT_ENV"
	log "Loaded specific runtime shortcut mapping from $RUNTIME_SHORTCUT_ENV"
fi

SPECIFIC_GAME_ID="${INSERTPLAY_SPECIFIC_GAME_ID:-}"
SPECIFIC_SHORTCUT_GUID="${INSERTPLAY_SHORTCUT_GUID:-}"
SPECIFIC_PROCESS_NAME="${INSERTPLAY_SPECIFIC_PROCESS_NAME:-}"
SPECIFIC_TIMEOUT_SECONDS="${INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS:-}"

if [[ -n "$SPECIFIC_GAME_ID" ]]; then
	if [[ -n "$SPECIFIC_PROCESS_NAME" ]] && is_positive_integer "$SPECIFIC_TIMEOUT_SECONDS"; then
		if [[ -n "$SPECIFIC_SHORTCUT_GUID" ]]; then
			log "Starting specific shortcut guid=$SPECIFIC_SHORTCUT_GUID gameId=$SPECIFIC_GAME_ID"
		else
			log "Starting specific shortcut gameId=$SPECIFIC_GAME_ID"
		fi
		if steam "steam://rungameid/$SPECIFIC_GAME_ID"; then
			log "Waiting process '$SPECIFIC_PROCESS_NAME' for ${SPECIFIC_TIMEOUT_SECONDS}s"
			if wait_for_process "$SPECIFIC_PROCESS_NAME" "$SPECIFIC_TIMEOUT_SECONDS"; then
				log "Specific launch succeeded."
				exit 0
			fi

			log "Specific process was not detected in time. Falling back to generic shortcut."
		else
			log "Failed to open specific steam shortcut. Falling back to generic shortcut."
		fi
	else
		log "Specific runtime shortcut data is incomplete. Falling back to generic shortcut."
	fi
else
	log "Specific runtime shortcut env not available. Falling back to generic shortcut."
fi

GAME_ID="${INSERTPLAY_SD_GAME_ID:-}"
if [[ -z "$GAME_ID" ]]; then
	echo "InsertPlay runtime configuration not found. Run 'insertplay setup' first." >&2
	exit 1
fi

log "Starting generic shortcut gameId=$GAME_ID"
exec steam "steam://rungameid/$GAME_ID"
