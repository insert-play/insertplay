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

CONTROLLER_SLEEP_ENABLED="${INSERTPLAY_CONTROLLER_SLEEP_ENABLED:-0}"
CONTROLLER_VENDOR_ID="${INSERTPLAY_CONTROLLER_VENDOR_ID:-}"
CONTROLLER_PRODUCT_ID="${INSERTPLAY_CONTROLLER_PRODUCT_ID:-}"

if [[ "$CONTROLLER_SLEEP_ENABLED" == "1" && ( -z "$CONTROLLER_VENDOR_ID" || -z "$CONTROLLER_PRODUCT_ID" ) ]]; then
    CONTROLLER_SLEEP_ENABLED="0"
fi

log() {
    # Logs to stderr so systemd/journald can capture it if present.
    printf '[%(%Y-%m-%d %H:%M:%S)T] %s\n' -1 "$*" >&2
}

# Prints "1" on stdout if the configured controller (Vendor:Product from
# /proc/bus/input/devices) currently has a joystick handler, i.e. is connected.
is_configured_controller_connected() {
    local vendor="" product="" handlers=""
    local target="${CONTROLLER_VENDOR_ID,,}:${CONTROLLER_PRODUCT_ID,,}"

    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            I:\ *)
                vendor=""
                product=""
                if [[ "$line" =~ Vendor=([0-9a-fA-F]+) ]]; then
                    vendor="${BASH_REMATCH[1],,}"
                fi
                if [[ "$line" =~ Product=([0-9a-fA-F]+) ]]; then
                    product="${BASH_REMATCH[1],,}"
                fi
                ;;
            H:\ Handlers=*)
                handlers="${line#H: Handlers=}"
                if [[ "${vendor}:${product}" == "$target" && "$handlers" =~ (^|[[:space:]])js[0-9]+([[:space:]]|$) ]]; then
                    echo "1"
                    return 0
                fi
                ;;
            "")
                vendor=""
                product=""
                ;;
        esac
    done < /proc/bus/input/devices

    echo "0"
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
controller_last_connected=""

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

    if [[ "$CONTROLLER_SLEEP_ENABLED" == "1" ]]; then
        controller_now_connected="$(is_configured_controller_connected)"

        if [[ "$controller_last_connected" == "1" && "$controller_now_connected" == "0" ]]; then
            log "Configured controller ($CONTROLLER_VENDOR_ID:$CONTROLLER_PRODUCT_ID) disconnected. Suspending system."
            output="$(systemctl suspend 2>&1)"
            rc=$?

            if [[ "$rc" -eq 0 ]]; then
                log "System suspend command completed successfully."
            else
                log "Failed to suspend system. rc=$rc output=$output"
            fi
        fi

        controller_last_connected="$controller_now_connected"
    fi

    sleep "$POLL_INTERVAL"
done
