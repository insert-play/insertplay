#!/usr/bin/env bash
# Sourced by a per-game insertplay-start.sh. Provides insertplay_guard_verify_or_exit,
# which refuses to start the game if this insertplay-start.sh was launched via a Steam
# shortcut created for a *different* SD card (e.g. the wrong card is currently inserted).
#
# Usage (from insertplay-start.sh, after SD_ROOT has been resolved):
#   source "$SD_ROOT/insertplay-guid-guard.sh"
#   insertplay_guard_verify_or_exit "$SD_ROOT" "$@"

insertplay_guard_extract_guid_arg() {
	local args=("$@")
	local i
	for ((i = 0; i < ${#args[@]}; i++)); do
		if [[ "${args[$i]}" == "--guid" && $((i + 1)) -lt ${#args[@]} ]]; then
			printf '%s\n' "${args[$((i + 1))]}"
			return 0
		fi
	done

	return 1
}

insertplay_guard_read_identity_guid() {
	local sd_root="$1"
	local identity_path=""

	if [[ -f "$sd_root/shortcut-identity.json" ]]; then
		identity_path="$sd_root/shortcut-identity.json"
	elif [[ -f "$sd_root/insertplay/shortcut-identity.json" ]]; then
		identity_path="$sd_root/insertplay/shortcut-identity.json"
	else
		return 1
	fi

	grep -Eo '"guid"[[:space:]]*:[[:space:]]*"[^"]+"' "$identity_path" 2>/dev/null \
		| head -n 1 \
		| sed -E 's/.*"([^"]+)"/\1/'
}

insertplay_guard_verify_or_exit() {
	local sd_root="$1"
	shift

	local expected_guid
	expected_guid="$(insertplay_guard_extract_guid_arg "$@")" || return 0

	local actual_guid
	actual_guid="$(insertplay_guard_read_identity_guid "$sd_root")"

	if [[ -n "$actual_guid" && "${actual_guid,,}" == "${expected_guid,,}" ]]; then
		return 0
	fi

	echo "[insertplay-start] wrong SD card: this shortcut expects guid=$expected_guid but the inserted card has guid=${actual_guid:-<none>}. Not starting the game." >&2
	command -v notify-send >/dev/null 2>&1 \
		&& notify-send "InsertPlay" "This shortcut doesn't match the inserted SD card." >/dev/null 2>&1

	exit 0
}
