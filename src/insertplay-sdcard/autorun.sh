#!/usr/bin/env bash
set -euo pipefail

SD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
	echo "[insertplay autorun] $*" >&2
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

resolve_identity_guid() {
	local identity_file=""
	local guid=""

	if [[ -f "$SD_ROOT/shortcut-identity.json" ]]; then
		identity_file="$SD_ROOT/shortcut-identity.json"
	elif [[ -f "$SD_ROOT/insertplay/shortcut-identity.json" ]]; then
		identity_file="$SD_ROOT/insertplay/shortcut-identity.json"
	fi

	if [[ -z "$identity_file" ]]; then
		log "Shortcut identity file not found on SD card."
		return 1
	fi

	guid="$(extract_guid_from_identity "$identity_file")"
	if [[ -z "$guid" ]]; then
		log "Shortcut identity file does not contain a guid."
		return 1
	fi

	if ! is_guid "$guid"; then
		log "Shortcut identity guid is invalid: $guid."
		return 1
	fi

	printf '%s\n' "$(printf '%s' "$guid" | tr '[:upper:]' '[:lower:]')"
}

run_runtime_autorun() {
	local guid="$1"
	local -a runtime_args=(runtime autorun-launch --sd-root "$SD_ROOT")

	if [[ -n "$guid" ]]; then
		runtime_args+=(--guid "$guid")
		log "Delegating autorun to CLI with guid=$guid"
	else
		log "Delegating autorun to CLI without guid"
	fi

	if [[ -x "$SD_ROOT/insertplay/InsertPlay.Cli" ]]; then
		exec "$SD_ROOT/insertplay/InsertPlay.Cli" "${runtime_args[@]}"
	fi

	if [[ -f "$SD_ROOT/insertplay/InsertPlay.Cli.dll" ]] && command -v dotnet >/dev/null 2>&1; then
		exec dotnet "$SD_ROOT/insertplay/InsertPlay.Cli.dll" "${runtime_args[@]}"
	fi

	if command -v insertplay >/dev/null 2>&1; then
		exec insertplay "${runtime_args[@]}"
	fi

	log "InsertPlay CLI not found. Install InsertPlay or copy InsertPlay.Cli to SD card."
	exit 127
}

SHORTCUT_GUID=""
if SHORTCUT_GUID="$(resolve_identity_guid)"; then
	:
fi

run_runtime_autorun "$SHORTCUT_GUID"
