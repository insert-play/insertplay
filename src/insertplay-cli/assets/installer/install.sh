#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_HOME="$HOME/.local/share/insertplay"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/insertplay"
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

  cat > "$tmp_unit" <<EOF
[Unit]
Description=InsertPlay SD Monitor Service
After=local-fs.target

[Service]
Type=simple
User=$user_name
Environment=HOME=$home_dir
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

if [ -x "$LAUNCHER" ] && "$LAUNCHER" help >/dev/null 2>&1; then
  echo "InsertPlay is already installed."
  exit 0
fi

PAYLOAD=""
if [ -d "$SCRIPT_DIR/insertplay-cli-runtime" ]; then
  PAYLOAD="$SCRIPT_DIR/insertplay-cli-runtime"
elif [ -d "$SCRIPT_DIR/insertplay-cli" ]; then
  PAYLOAD="$SCRIPT_DIR/insertplay-cli"
elif [ -f "$SCRIPT_DIR/InsertPlay.Cli" ]; then
  PAYLOAD="$SCRIPT_DIR"
fi

if [ -z "$PAYLOAD" ]; then
  echo "Error: payload not found (place insertplay-cli-runtime/ or insertplay-cli/ next to this script)." >&2
  exit 1
fi

mkdir -p "$INSTALL_HOME" "$BIN_DIR"
cp -a "$PAYLOAD"/. "$INSTALL_HOME"/

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

echo "Installation complete. Running setup..."
"$LAUNCHER" setup

if prompt_install_service; then
  install_system_service
else
  echo "Service installation skipped."
fi
