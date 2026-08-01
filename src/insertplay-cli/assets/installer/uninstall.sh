#!/usr/bin/env bash
set -euo pipefail

INSTALL_HOME="$HOME/.local/share/insertplay"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/insertplay"
CONFIG_DIR="$HOME/.config/insertplay"
PURGE_CONFIG="false"
SERVICE_NAME="insertplay.service"
UNIT_PATH="/etc/systemd/system/$SERVICE_NAME"

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi

  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
    return
  fi

  echo "Warning: sudo not found. Cannot run privileged command: $*" >&2
  return 1
}

remove_system_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return
  fi

  local had_service="false"
  if [ -f "$UNIT_PATH" ]; then
    had_service="true"
  fi

  if systemctl list-unit-files "$SERVICE_NAME" >/dev/null 2>&1; then
    had_service="true"
  fi

  if [ "$had_service" != "true" ]; then
    return
  fi

  run_privileged systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  run_privileged systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true

  if [ -f "$UNIT_PATH" ]; then
    if ! run_privileged rm -f "$UNIT_PATH"; then
      echo "Warning: failed to remove $UNIT_PATH" >&2
      return
    fi
  fi

  run_privileged systemctl daemon-reload >/dev/null 2>&1 || true
  echo "Service $SERVICE_NAME removed (if it existed)."
}

if [ "${1:-}" = "--purge" ]; then
  PURGE_CONFIG="true"
fi

if [ ! -d "$INSTALL_HOME" ] && [ ! -e "$LAUNCHER" ] && [ ! -f "$UNIT_PATH" ]; then
  echo "InsertPlay is not installed."
  exit 0
fi

remove_system_service

if [ -d "$INSTALL_HOME" ]; then
  rm -rf "$INSTALL_HOME"
fi

if [ -e "$LAUNCHER" ]; then
  rm -f "$LAUNCHER"
fi

if [ "$PURGE_CONFIG" = "true" ] && [ -d "$CONFIG_DIR" ]; then
  rm -rf "$CONFIG_DIR"
  echo "Configuration removed at $CONFIG_DIR"
else
  echo "Configuration preserved at $CONFIG_DIR"
  echo "Use --purge to also remove the configuration."
fi

echo "InsertPlay uninstalled successfully."
