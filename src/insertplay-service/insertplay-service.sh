#!/bin/bash

set -u

CURRENT_USER="${SUDO_USER:-${USER:-$(id -un 2>/dev/null || true)}}"

if [[ -z "${CURRENT_USER}" ]]; then
    echo "Could not determine current user for media directory detection." >&2
    exit 1
fi

DEFAULT_MEDIA_DIR="/run/media/${CURRENT_USER}"
ALT_MEDIA_DIR="/media/${CURRENT_USER}"
MEDIA_DIR="${MEDIA_DIR:-$DEFAULT_MEDIA_DIR}"

if [[ ! -d "$MEDIA_DIR" && -d "$ALT_MEDIA_DIR" ]]; then
    MEDIA_DIR="$ALT_MEDIA_DIR"
fi

POLL_INTERVAL="${POLL_INTERVAL:-1}"
LOCK_DIR="/tmp/sd-monitor.lock"
SOUND_FILE="/usr/share/sounds/freedesktop/stereo/device-added.oga"

log() {
    # Logs to stderr so systemd/journald can capture it if present.
    printf '[%(%Y-%m-%d %H:%M:%S)T] %s\n' -1 "$*" >&2
}

cleanup() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "Another sd-monitor instance is already running."
    exit 0
fi

trap cleanup EXIT INT TERM

declare -A last_seen=()

while true; do
    declare -A current_seen=()

    if [[ -d "$MEDIA_DIR" ]]; then
        while IFS= read -r dir; do
            [[ -z "$dir" ]] && continue
            current_seen["$dir"]=1

            # New mount detected.
            if [[ -z "${last_seen["$dir"]+x}" ]]; then
                if [[ -f "$SOUND_FILE" ]]; then
                    paplay "$SOUND_FILE" >/dev/null 2>&1 || true
                fi

                if [[ -f "$dir/autorun.sh" ]]; then
                    chmod +x "$dir/autorun.sh" 2>/dev/null || true
                    bash "$dir/autorun.sh" &
                    log "Executed autorun.sh for $dir"
                else
                    log "Detected mount without autorun.sh: $dir"
                fi
            fi
        done < <(find "$MEDIA_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
    fi

    unset last_seen
    declare -A last_seen=()
    for dir in "${!current_seen[@]}"; do
        last_seen["$dir"]=1
    done

    sleep "$POLL_INTERVAL"
done
