#!/bin/bash
#
# Standalone diagnostic tool: logs controller (joystick/gamepad) connect and
# disconnect events. Run this, then physically unplug the controller you want
# insertplay-service.sh to react to, and read the Vendor/Product IDs off the
# "REMOVED" line. Those IDs are what you set as INSERTPLAY_CONTROLLER_VENDOR_ID
# and INSERTPLAY_CONTROLLER_PRODUCT_ID.
#
# Only reads /proc/bus/input/devices (a stable Linux kernel interface), so
# this works the same way on any distro/PC without extra tools installed.

set -u

POLL_INTERVAL="${POLL_INTERVAL:-1}"

log() {
    printf '[%(%Y-%m-%d %H:%M:%S)T] %s\n' -1 "$*" >&2
}

# Prints one line per currently connected joystick/gamepad device, as
# "vendor:product<TAB>name".
list_joystick_devices() {
    local vendor="" product="" name="" handlers=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            I:\ *)
                vendor=""
                product=""
                name=""
                if [[ "$line" =~ Vendor=([0-9a-fA-F]+) ]]; then
                    vendor="${BASH_REMATCH[1],,}"
                fi
                if [[ "$line" =~ Product=([0-9a-fA-F]+) ]]; then
                    product="${BASH_REMATCH[1],,}"
                fi
                ;;
            N:\ Name=*)
                name="${line#N: Name=}"
                name="${name%\"}"
                name="${name#\"}"
                ;;
            H:\ Handlers=*)
                handlers="${line#H: Handlers=}"
                if [[ "$handlers" =~ (^|[[:space:]])js[0-9]+([[:space:]]|$) ]]; then
                    printf '%s:%s\t%s\n' "$vendor" "$product" "$name"
                fi
                ;;
            "")
                vendor=""
                product=""
                name=""
                ;;
        esac
    done < /proc/bus/input/devices
}

log "Watching for joystick/gamepad connect and disconnect events (poll every ${POLL_INTERVAL}s). Ctrl+C to stop."

declare -A last_seen=()

while true; do
    declare -A current_seen=()

    while IFS=$'\t' read -r key name; do
        [[ -z "$key" ]] && continue
        current_seen["$key"]="$name"

        if [[ -z "${last_seen["$key"]+x}" ]]; then
            log "ADDED   Vendor:Product=$key Name=\"$name\""
        fi
    done < <(list_joystick_devices)

    for key in "${!last_seen[@]}"; do
        if [[ -z "${current_seen["$key"]+x}" ]]; then
            log "REMOVED Vendor:Product=$key Name=\"${last_seen[$key]}\""
        fi
    done

    unset last_seen
    declare -A last_seen=()
    for key in "${!current_seen[@]}"; do
        last_seen["$key"]="${current_seen[$key]}"
    done

    sleep "$POLL_INTERVAL"
done
