#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PUBLISH_SCRIPT="$REPO_ROOT/src/publish.sh"
PUBLISH_DIR="$REPO_ROOT/publish"
SDCARD_ASSETS_DIR="$REPO_ROOT/src/insertplay-sdcard"
RID="${1:-linux-x64}"
CURRENT_USER="${SUDO_USER:-${USER:-$(id -un)}}"

log() {
  echo "[build-sd-card] $*"
}

die() {
  echo "Error: $*" >&2
  exit 1
}

extract_guid_from_identity() {
  local identity_file="$1"
  grep -Eo '"guid"[[:space:]]*:[[:space:]]*"[^"]+"' "$identity_file" 2>/dev/null \
    | head -n 1 \
    | sed -E 's/.*"([^"]+)"/\1/'
}

is_guid() {
  [[ "$1" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]
}

collect_mount_candidates() {
  declare -A seen_keys=()
  local base
  for base in "/run/media/$CURRENT_USER" "/media/$CURRENT_USER"; do
    [[ -d "$base" ]] || continue

    while IFS= read -r mountpoint; do
      [[ -n "$mountpoint" ]] || continue
      mountpoint -q "$mountpoint" || continue

      local source canonical key
      source="$(findmnt -n -o SOURCE --target "$mountpoint" 2>/dev/null || true)"
      canonical="$(readlink -f "$mountpoint" 2>/dev/null || echo "$mountpoint")"

      if [[ -n "$source" ]]; then
        key="src:$source"
      else
        key="mp:$canonical"
      fi

      if [[ -n "${seen_keys[$key]+x}" ]]; then
        continue
      fi
      seen_keys[$key]=1

      echo "$mountpoint"
    done < <(find "$base" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
  done
}

select_target_mountpoint() {
  mapfile -t candidates < <(collect_mount_candidates)

  if [[ ${#candidates[@]} -eq 0 ]]; then
    die "No mounted SD card or USB drive found for user '$CURRENT_USER'."
  fi

  if [[ ${#candidates[@]} -eq 1 ]]; then
    echo "${candidates[0]}"
    return 0
  fi

  echo "Multiple removable media detected. Choose target:" >&2
  local i
  for i in "${!candidates[@]}"; do
    local mp source fs_type
    mp="${candidates[$i]}"
    source="$(findmnt -n -o SOURCE --target "$mp" 2>/dev/null || echo "?")"
    fs_type="$(findmnt -n -o FSTYPE --target "$mp" 2>/dev/null || echo "?")"
    echo "[$i] $mp (device=$source, fs=$fs_type)" >&2
  done

  local choice
  while true; do
    read -r -p "Enter target index: " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 0 && choice < ${#candidates[@]} )); then
      echo "${candidates[$choice]}"
      return 0
    fi
    echo "Invalid selection. Try again." >&2
  done
}

ensure_shortcut_identity_guid() {
  local sd_root="$1"
  local identity_root="$sd_root/shortcut-identity.json"
  local identity_legacy="$sd_root/insertplay/shortcut-identity.json"
  local existing_identity=""

  if [[ -f "$identity_root" ]]; then
    existing_identity="$identity_root"
  elif [[ -f "$identity_legacy" ]]; then
    existing_identity="$identity_legacy"
  fi

  if [[ -n "$existing_identity" ]]; then
    local existing_guid
    existing_guid="$(extract_guid_from_identity "$existing_identity" || true)"
    if [[ -n "$existing_guid" ]] && is_guid "$existing_guid"; then
      log "Reusing existing shortcut GUID: $existing_guid"
      return 0
    fi

    log "Existing shortcut identity file is invalid: $existing_identity"
  fi

  local cli_bin="$sd_root/insertplay/InsertPlay.Cli"
  local cli_dll="$sd_root/insertplay/InsertPlay.Cli.dll"
  log "No valid shortcut GUID found on SD. Creating via CLI."

  if [[ -x "$cli_bin" ]]; then
    "$cli_bin" runtime ensure-identity --sd-root "$sd_root"
    return 0
  fi

  if [[ -f "$cli_dll" ]] && command -v dotnet >/dev/null 2>&1; then
    dotnet "$cli_dll" runtime ensure-identity --sd-root "$sd_root"
    return 0
  fi

  die "Unable to ensure shortcut identity GUID. InsertPlay CLI not found in published SD output."
}

if [ ! -f "$PUBLISH_SCRIPT" ]; then
  die "publish script not found at $PUBLISH_SCRIPT"
fi

TARGET_MOUNTPOINT="$(select_target_mountpoint)"
TARGET_INSERTPLAY_DIR="$TARGET_MOUNTPOINT/insertplay"
log "Target media: $TARGET_MOUNTPOINT"

log "Publishing latest InsertPlay build for RID: $RID"
"$PUBLISH_SCRIPT" "$RID"

log "Refreshing target directory: $TARGET_INSERTPLAY_DIR"
rm -rf "$TARGET_INSERTPLAY_DIR"
mkdir -p "$TARGET_INSERTPLAY_DIR"
cp -a "$PUBLISH_DIR/insertplay"/. "$TARGET_INSERTPLAY_DIR"/

ensure_shortcut_identity_guid "$TARGET_MOUNTPOINT"

for sdcard_script in autorun.sh install-shortcut.sh; do
  src="$SDCARD_ASSETS_DIR/$sdcard_script"
  dst="$TARGET_MOUNTPOINT/$sdcard_script"

  if [[ -f "$src" ]]; then
    cp -f "$src" "$dst"
    chmod +x "$dst"
    log "Copied $sdcard_script to SD root: $dst"
  else
    log "Warning: sdcard script not found: $src"
  fi
done

if [[ -f "$TARGET_INSERTPLAY_DIR/install.sh" ]]; then
  chmod +x "$TARGET_INSERTPLAY_DIR/install.sh"
fi

if [[ -f "$TARGET_INSERTPLAY_DIR/uninstall.sh" ]]; then
  chmod +x "$TARGET_INSERTPLAY_DIR/uninstall.sh"
fi

log "Install media updated successfully."
log "Copied publish contents to: $TARGET_INSERTPLAY_DIR"
