#!/usr/bin/env bash
set -euo pipefail

SD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESTART_STEAM=0

if [[ "${1-}" == "--restart-steam" ]]; then
	RESTART_STEAM=1
	shift
fi

if [[ $# -gt 0 ]]; then
	echo "Usage: $(basename "$0") [--restart-steam]" >&2
	exit 1
fi

RUNTIME_INSTALL_ARGS=(runtime install-shortcut --sd-root "$SD_ROOT")
if [[ $RESTART_STEAM -eq 1 ]]; then
	RUNTIME_INSTALL_ARGS+=(--restart-steam)
fi

log() {
	echo "[insertplay install-shortcut] $*" >&2
}

try_runtime_install() {
	local source_name="$1"
	shift

	local output
	set +e
	output="$($@ "${RUNTIME_INSTALL_ARGS[@]}" 2>&1)"
	local status=$?
	set -e

	if [[ $status -eq 0 ]]; then
		echo "$output"
		return 0
	fi

	if grep -qiE "runtime install-shortcut|InsertPlay CLI \(MVP\)|Comandos:" <<< "$output"; then
		log "$source_name found, but it does not support 'runtime install-shortcut'."
		log "Please update InsertPlay on this machine or copy a newer InsertPlay.Cli to SD in insertplay/."
		log "Output from $source_name:"
		echo "$output" >&2
		return 2
	fi

	log "$source_name failed with exit code $status"
	echo "$output" >&2
	return 1
}

run_cli() {
	# Prefer SD-provided CLI first so media can carry a newer runtime command.
	if [[ -x "$SD_ROOT/insertplay/InsertPlay.Cli" ]]; then
		if try_runtime_install "SD InsertPlay.Cli" "$SD_ROOT/insertplay/InsertPlay.Cli"; then
			return 0
		else
			local rc=$?
			if [[ $rc -eq 2 ]]; then
				return 2
			fi
		fi
	fi

	if [[ -f "$SD_ROOT/insertplay/InsertPlay.Cli.dll" ]] && command -v dotnet >/dev/null 2>&1; then
		if try_runtime_install "SD InsertPlay.Cli.dll" dotnet "$SD_ROOT/insertplay/InsertPlay.Cli.dll"; then
			return 0
		else
			local rc=$?
			if [[ $rc -eq 2 ]]; then
				return 2
			fi
		fi
	fi

	if command -v insertplay >/dev/null 2>&1; then
		if try_runtime_install "system insertplay" insertplay; then
			return 0
		else
			local rc=$?
			if [[ $rc -eq 2 ]]; then
				return 2
			fi
		fi
	fi

	return 127
}

if ! run_cli; then
	status=$?
	if [[ $status -eq 127 ]]; then
		log "InsertPlay CLI not found. Install InsertPlay or copy InsertPlay.Cli to SD card."
		exit 127
	fi

	exit "$status"
fi
