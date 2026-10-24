#!/usr/bin/env bash
# Removes the legacy InsertPlay (CLI + insertplay.service) installed by install.sh.
# Idempotent: running it again reports every item as already absent.
#
#   sudo bash uninstall.sh [--purge] [--user NAME]   as root; cleans the home of $SUDO_USER (or NAME)
#   bash uninstall.sh [--purge]                      as the user; calls sudo for the system files
#
# --purge also removes the configuration (~/.config/insertplay) and the logs (~/.local/state/insertplay).
# Steam shortcuts are NOT touched: remove them through Steam (SteamClient.Apps.RemoveShortcut).
set -euo pipefail

SERVICE_NAME="insertplay.service"
UNIT_PATH="/etc/systemd/system/$SERVICE_NAME"
WANTS_LINK="/etc/systemd/system/multi-user.target.wants/$SERVICE_NAME"
POLKIT_RULE_PATH="/etc/polkit-1/rules.d/49-insertplay-controller-sleep.rules"
# lock directory created by insertplay-service.sh
SERVICE_LOCK_DIR="/tmp/sd-monitor.lock"

PURGE_CONFIG="false"
TARGET_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --purge) PURGE_CONFIG="true" ;;
    --user)
      TARGET_USER="${2:-}"
      shift
      ;;
    *)
      echo "Usage: $0 [--purge] [--user NAME]" >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$(id -u)" -eq 0 ]; then
  TARGET_USER="${TARGET_USER:-${SUDO_USER:-}}"
  if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
    echo "Error: run with sudo from the user's session, or pass --user NAME." >&2
    exit 2
  fi
  TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
else
  TARGET_USER="$(id -un)"
  TARGET_HOME="$HOME"
fi

case "$TARGET_HOME" in
  /?*) ;;
  *)
    echo "Error: could not resolve the home directory of '$TARGET_USER'." >&2
    exit 2
    ;;
esac

INSTALL_HOME="$TARGET_HOME/.local/share/insertplay"
UPDATE_LOCK="$TARGET_HOME/.local/share/insertplay.update.lock"
LAUNCHER="$TARGET_HOME/.local/bin/insertplay"
CONFIG_DIR="$TARGET_HOME/.config/insertplay"
STATE_DIR="$TARGET_HOME/.local/state/insertplay"

REMOVED=0
ABSENT=0

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi

  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
    return
  fi

  echo "Error: sudo not found. Cannot run privileged command: $*" >&2
  return 1
}

# Exit status of the existence test + removal below when the path does not exist; any other
# nonzero status (failed sudo, failed rm) aborts the script instead of reading as "absent".
ABSENT_STATUS=10

# The test and the removal run in one (possibly elevated) shell: /etc/polkit-1/rules.d is
# root-only, so the existence test itself may need sudo.
remove_path() {
  local path="$1" privileged="${2:-}"
  local -a runner=()
  if [ -n "$privileged" ]; then
    runner=(run_privileged)
  fi

  local status=0
  "${runner[@]}" sh -c 'if [ -e "$1" ] || [ -L "$1" ]; then rm -rf -- "$1"; else exit "$2"; fi' \
    sh "$path" "$ABSENT_STATUS" || status=$?
  case "$status" in
    0)
      echo "removed:        $path"
      REMOVED=$((REMOVED + 1))
      ;;
    "$ABSENT_STATUS")
      echo "already absent: $path"
      ABSENT=$((ABSENT + 1))
      ;;
    *)
      echo "Error: could not check or remove $path (exit $status); nothing after this was removed." >&2
      exit 1
      ;;
  esac
}

remove_system_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; skipping $SERVICE_NAME."
    return
  fi

  # ActiveState also covers a unit still loaded after its file was deleted and one waiting
  # to restart (Restart=always shows as "activating"); a missing unit reports "inactive".
  local active
  active="$(systemctl show -p ActiveState --value "$SERVICE_NAME" 2>/dev/null || true)"
  case "$active" in
    "" | inactive | failed) ;;
    *)
      run_privileged systemctl stop "$SERVICE_NAME"
      echo "stopped:        $SERVICE_NAME ($active)"
      ;;
  esac

  if systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
    if [ "$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)" = "enabled" ]; then
      run_privileged systemctl disable --quiet "$SERVICE_NAME"
      echo "disabled:       $SERVICE_NAME"
    fi
  fi

  remove_path "$WANTS_LINK" privileged
  remove_path "$UNIT_PATH" privileged
  run_privileged systemctl daemon-reload || echo "Warning: systemctl daemon-reload failed." >&2
  run_privileged systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
}

echo "Removing the legacy InsertPlay for user '$TARGET_USER' ($TARGET_HOME)..."

# Authenticate once up front: a cancelled or failed sudo must stop the script before it
# deletes anything, not leave the system files behind after removing the user's.
if [ "$(id -u)" -ne 0 ] && ! run_privileged true; then
  echo "Error: administrator access is required for the system files; nothing was removed." >&2
  exit 1
fi

remove_system_service
remove_path "$POLKIT_RULE_PATH" privileged
remove_path "$SERVICE_LOCK_DIR" privileged
remove_path "$INSTALL_HOME"
remove_path "$UPDATE_LOCK"
remove_path "$LAUNCHER"

if [ "$PURGE_CONFIG" = "true" ]; then
  remove_path "$CONFIG_DIR"
  remove_path "$STATE_DIR"
else
  echo "kept:           $CONFIG_DIR and $STATE_DIR (use --purge to remove them)"
fi

if pgrep -f "$INSTALL_HOME" >/dev/null 2>&1; then
  echo "Warning: processes from $INSTALL_HOME are still running:" >&2
  pgrep -af "$INSTALL_HOME" >&2 || true
fi

echo "Done: $REMOVED removed, $ABSENT already absent."
