#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_HOME="$HOME/.local/share/insertplay"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/insertplay"
SERVICE_NAME="insertplay.service"
UNIT_PATH="/etc/systemd/system/$SERVICE_NAME"
POLKIT_RULE_PATH="/etc/polkit-1/rules.d/49-insertplay-controller-sleep.rules"

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

prompt_install_service() {
  if [ ! -t 0 ]; then
    return 1
  fi

  local answer
  read -r -p "Install insertplay-service as a system service (start on boot)? (y/N): " answer
  case "${answer:-N}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

CONTROLLER_SLEEP_ENABLED=0
CONTROLLER_SLEEP_VENDOR_ID=""
CONTROLLER_SLEEP_PRODUCT_ID=""

prompt_controller_sleep() {
  if [ ! -t 0 ]; then
    return 1
  fi

  local answer
  read -r -p "Suspend the PC automatically when a specific controller is disconnected? (y/N): " answer
  case "${answer:-N}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# Prints one line per currently connected joystick/gamepad device, as
# "vendor:product<TAB>name", by reading /proc/bus/input/devices.
list_connected_controllers() {
  local vendor="" product="" name="" handlers=""

  while IFS= read -r line || [ -n "$line" ]; do
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

prompt_controller_ids_manually() {
  local vendor product
  read -r -p "Controller Vendor ID (hex, e.g. 045e, blank to skip): " vendor
  read -r -p "Controller Product ID (hex, e.g. 028e, blank to skip): " product

  if [ -z "$vendor" ] || [ -z "$product" ]; then
    echo "Vendor/Product ID not provided; controller-sleep feature left disabled."
    return
  fi

  CONTROLLER_SLEEP_ENABLED=1
  CONTROLLER_SLEEP_VENDOR_ID="$vendor"
  CONTROLLER_SLEEP_PRODUCT_ID="$product"
}

prompt_controller_ids() {
  local -a keys=() names=()
  local key name choice

  while IFS=$'\t' read -r key name; do
    [ -z "$key" ] && continue
    keys+=("$key")
    names+=("$name")
  done < <(list_connected_controllers)

  if [ "${#keys[@]}" -eq 0 ]; then
    echo "No controller detected under /proc/bus/input/devices."
    echo "Connect the controller you want to use, then either re-run the installer or enter its Vendor/Product ID manually."
    prompt_controller_ids_manually
    return
  fi

  echo "Detected controllers:"
  local i
  for i in "${!keys[@]}"; do
    printf '  %d) %s (%s)\n' "$((i + 1))" "${names[$i]}" "${keys[$i]}"
  done
  printf '  %d) Enter Vendor/Product ID manually\n' "$(( ${#keys[@]} + 1 ))"

  read -r -p "Select the controller to use (blank to skip): " choice

  if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -gt "$(( ${#keys[@]} + 1 ))" ]; then
    echo "Controller-sleep feature left disabled."
    return
  fi

  if [ "$choice" -eq "$(( ${#keys[@]} + 1 ))" ]; then
    prompt_controller_ids_manually
    return
  fi

  local selected="${keys[$((choice - 1))]}"
  CONTROLLER_SLEEP_ENABLED=1
  CONTROLLER_SLEEP_VENDOR_ID="${selected%%:*}"
  CONTROLLER_SLEEP_PRODUCT_ID="${selected##*:}"
}

# insertplay-service.sh runs as a system service (not an interactive login
# session), so logind's default polkit policy would otherwise prompt for
# interactive authentication when it calls `systemctl suspend`. This grants
# passwordless suspend to the configured user only.
install_controller_sleep_polkit_rule() {
  local user_name="$1"
  local tmp_rule
  tmp_rule="$(mktemp)"

  cat > "$tmp_rule" <<EOF
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.login1.suspend") == 0 &&
        subject.user == "$user_name") {
        return polkit.Result.YES;
    }
});
EOF

  if ! run_privileged install -D -m 0644 "$tmp_rule" "$POLKIT_RULE_PATH"; then
    echo "Warning: failed to install polkit rule at $POLKIT_RULE_PATH." >&2
    echo "'systemctl suspend' will likely fail with an authentication error when the controller-sleep feature triggers." >&2
    rm -f "$tmp_rule"
    return
  fi

  rm -f "$tmp_rule"
  echo "Polkit rule installed: $user_name can suspend the system without interactive authentication."
}

install_system_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found. Skipping service installation."
    return
  fi

  local service_script="$INSTALL_HOME/insertplay-service.sh"
  if [ ! -f "$service_script" ]; then
    echo "Warning: service script not found at $service_script" >&2
    return
  fi

  chmod +x "$service_script" || true

  local user_name home_dir tmp_unit
  user_name="$(id -un)"
  home_dir="$HOME"
  tmp_unit="$(mktemp)"

  if [ "$CONTROLLER_SLEEP_ENABLED" -eq 1 ]; then
    install_controller_sleep_polkit_rule "$user_name"
  fi

  cat > "$tmp_unit" <<EOF
[Unit]
Description=InsertPlay SD Monitor Service
After=local-fs.target

[Service]
Type=simple
User=$user_name
Environment=HOME=$home_dir
EOF

  if [ "$CONTROLLER_SLEEP_ENABLED" -eq 1 ]; then
    cat >> "$tmp_unit" <<EOF
Environment=INSERTPLAY_CONTROLLER_SLEEP_ENABLED=1
Environment=INSERTPLAY_CONTROLLER_VENDOR_ID=$CONTROLLER_SLEEP_VENDOR_ID
Environment=INSERTPLAY_CONTROLLER_PRODUCT_ID=$CONTROLLER_SLEEP_PRODUCT_ID
EOF
  fi

  cat >> "$tmp_unit" <<EOF
ExecStart=/usr/bin/env bash $service_script
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  if ! run_privileged install -m 0644 "$tmp_unit" "$UNIT_PATH"; then
    echo "Warning: failed to install $UNIT_PATH. Service was not configured." >&2
    rm -f "$tmp_unit"
    return
  fi

  rm -f "$tmp_unit"

  if ! run_privileged systemctl daemon-reload; then
    echo "Warning: failed to reload systemd daemon." >&2
    return
  fi

  if ! run_privileged systemctl enable --now "$SERVICE_NAME"; then
    echo "Warning: failed to enable/start $SERVICE_NAME." >&2
    return
  fi

  echo "Service $SERVICE_NAME installed and started."
}

restart_system_service_if_active() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found. Skipping service restart after update."
    return
  fi

  if ! systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
    echo "Service $SERVICE_NAME is not installed. Skipping restart."
    return
  fi

  if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Service $SERVICE_NAME is installed but not active. Skipping restart."
    return
  fi

  if run_privileged systemctl restart "$SERVICE_NAME"; then
    echo "Service $SERVICE_NAME restarted to use the updated runtime."
  else
    echo "Warning: failed to restart $SERVICE_NAME after update." >&2
  fi
}

ensure_controller_sleep_polkit_rule() {
  if [ ! -f "$UNIT_PATH" ] || [ -f "$POLKIT_RULE_PATH" ]; then
    return
  fi

  if ! grep -q '^Environment=INSERTPLAY_CONTROLLER_SLEEP_ENABLED=1' "$UNIT_PATH" 2>/dev/null; then
    return
  fi

  echo "Controller-sleep feature is enabled but its polkit rule is missing; installing it now."
  install_controller_sleep_polkit_rule "$(id -un)"
}

IS_UPDATE=0
if [ -x "$LAUNCHER" ] && "$LAUNCHER" help >/dev/null 2>&1; then
  IS_UPDATE=1
  echo "Existing InsertPlay installation detected. Running update..."
fi

PAYLOAD=""
if [ -d "$SCRIPT_DIR/insertplay-cli-runtime" ]; then
  PAYLOAD="$SCRIPT_DIR/insertplay-cli-runtime"
elif [ -d "$SCRIPT_DIR/insertplay-cli" ]; then
  PAYLOAD="$SCRIPT_DIR/insertplay-cli"
elif [ -f "$SCRIPT_DIR/InsertPlay.Cli" ]; then
  PAYLOAD="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../../InsertPlay.Cli" ]; then
  PAYLOAD="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [ -d "$SCRIPT_DIR/../../insertplay-cli-runtime" ]; then
  PAYLOAD="$(cd "$SCRIPT_DIR/../.." && pwd)/insertplay-cli-runtime"
elif [ -d "$SCRIPT_DIR/../../insertplay-cli" ]; then
  PAYLOAD="$(cd "$SCRIPT_DIR/../.." && pwd)/insertplay-cli"
fi

if [ -z "$PAYLOAD" ]; then
  echo "Error: payload not found (place insertplay-cli-runtime/ or insertplay-cli/ next to this script)." >&2
  exit 1
fi

mkdir -p "$INSTALL_HOME" "$BIN_DIR"
cp -a "$PAYLOAD"/. "$INSTALL_HOME"/

ASSETS_DIR="$SCRIPT_DIR/../assets"
if [ -d "$ASSETS_DIR" ]; then
  mkdir -p "$INSTALL_HOME/assets"
  cp -a "$ASSETS_DIR"/. "$INSTALL_HOME/assets"/
fi

if [ -f "$INSTALL_HOME/InsertPlay.Cli" ]; then
  chmod +x "$INSTALL_HOME/InsertPlay.Cli" || true
  cat > "$LAUNCHER" << 'EOF'
#!/usr/bin/env bash
exec "$HOME/.local/share/insertplay/InsertPlay.Cli" "$@"
EOF
elif [ -f "$INSTALL_HOME/InsertPlay.Cli.dll" ]; then
  if ! command -v dotnet >/dev/null 2>&1; then
    echo "Error: InsertPlay.Cli.dll found, but dotnet is not installed." >&2
    exit 1
  fi
  cat > "$LAUNCHER" << 'EOF'
#!/usr/bin/env bash
exec dotnet "$HOME/.local/share/insertplay/InsertPlay.Cli.dll" "$@"
EOF
else
  echo "Error: InsertPlay executable not found after copy." >&2
  exit 1
fi

chmod +x "$LAUNCHER"

if [ "$IS_UPDATE" -eq 1 ]; then
  ensure_controller_sleep_polkit_rule
  restart_system_service_if_active
  echo "Update complete. Existing configuration was preserved."
  echo "Tip: run 'insertplay setup' only if you want to reconfigure Steam user/paths."
else
  echo "Installation complete. Running setup..."
  "$LAUNCHER" setup

  if prompt_install_service; then
    if prompt_controller_sleep; then
      prompt_controller_ids
    fi
    install_system_service
  else
    echo "Service installation skipped."
  fi
fi
