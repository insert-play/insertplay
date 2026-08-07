#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PROJECT="$REPO_ROOT/src/insertplay-cli/InsertPlay.Cli.csproj"
PUBLISH_DIR="$REPO_ROOT/publish"
RID="${1:-linux-x64}"
CURRENT_USER="${SUDO_USER:-${USER:-$(id -un)}}"

log() {
  echo "[build-sd-card] $*"
}

die() {
  echo "Error: $*" >&2
  exit 1
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

if [ ! -f "$PROJECT" ]; then
  die "project not found at $PROJECT"
fi

TARGET_MOUNTPOINT="$(select_target_mountpoint)"
TARGET_INSERTPLAY_DIR="$TARGET_MOUNTPOINT/insertplay"
RUNTIME_ASSETS_DIR="$PUBLISH_DIR/assets/runtime"
log "Target media: $TARGET_MOUNTPOINT"

rm -rf "$PUBLISH_DIR"
mkdir -p "$PUBLISH_DIR"

log "Publishing latest InsertPlay build for RID: $RID"
dotnet publish "$PROJECT" \
  -c Release \
  -r "$RID" \
  -p:PublishSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:DebugType=None \
  -p:DebugSymbols=false \
  --self-contained true \
  -o "$PUBLISH_DIR"

log "Refreshing target directory: $TARGET_INSERTPLAY_DIR"
rm -rf "$TARGET_INSERTPLAY_DIR"
mkdir -p "$TARGET_INSERTPLAY_DIR"
cp -a "$PUBLISH_DIR"/. "$TARGET_INSERTPLAY_DIR"/

for runtime_script in autorun.sh install-shortcut.sh; do
  src="$RUNTIME_ASSETS_DIR/$runtime_script"
  dst="$TARGET_MOUNTPOINT/$runtime_script"

  if [[ -f "$src" ]]; then
    cp -f "$src" "$dst"
    chmod +x "$dst"
    log "Copied $runtime_script to SD root: $dst"
  else
    log "Warning: runtime script not found in publish output: $src"
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
