#!/usr/bin/env bash
# Sourced by insertplay-start.sh. Refuses to start the game when the shortcut belongs to
# another card (for example, a different card was inserted in the same slot): the plugin
# passes the shortcut's guid as --guid, and it must match this card's shortcut-identity.json.
#
# Usage, in insertplay-start.sh:
#   source "$SD_ROOT/insertplay-guid-guard.sh"
#   insertplay_guard_verify_or_exit "$SD_ROOT" "$@"
#
# Same on every card: don't edit it.

insertplay_guard_verify_or_exit() {
	local sd_root="$1"
	shift

	local expected_guid="" args=("$@") i
	for ((i = 0; i < ${#args[@]}; i++)); do
		if [[ "${args[$i]}" == "--guid" && $((i + 1)) -lt ${#args[@]} ]]; then
			expected_guid="${args[$((i + 1))]}"
		fi
	done
	# started by hand, without --guid: nothing to check
	[[ -n "$expected_guid" ]] || return 0

	# any key case, like the plugin
	local actual_guid
	actual_guid="$(grep -Eio '"guid"[[:space:]]*:[[:space:]]*"[^"]+"' "$sd_root/shortcut-identity.json" 2>/dev/null \
		| head -n 1 | sed -E 's/.*"([^"]+)"/\1/')"
	if [[ -n "$actual_guid" && "${actual_guid,,}" == "${expected_guid,,}" ]]; then
		return 0
	fi

	echo "[insertplay-start] wrong card: the shortcut expects guid=$expected_guid, this card has guid=${actual_guid:-<none>}" >&2
	command -v notify-send >/dev/null 2>&1 \
		&& notify-send "InsertPlay" "This shortcut doesn't match the inserted SD card." >/dev/null 2>&1
	exit 0
}
