#!/usr/bin/env bash
set -euo pipefail

APP_TITLE="InsertPlay"
TARGET_SCRIPT="insertplay-start.sh"
INSTALL_SCRIPT="install-shortcut.sh"
STEAM_BLUE="#66c0f4"
STEAM_TEXT="#c7d5e0"
DISPLAY_SECONDS=3
OVERLAY_BORDERS=120

get_screen_size() {
  local size
  size="$(xrandr --current 2>/dev/null | awk '/\*/ {print $1; exit}' || true)"
  if [[ -z "$size" ]]; then
    printf '1920 1080\n'
    return
  fi

  printf '%s\n' "$size" | awk -Fx '{print $1, $2}'
}

show_graphic_error() {
  local heading="$1"
  local detail="$2"
  local screen_w screen_h box_w box_h
  read -r screen_w screen_h <<<"$(get_screen_size)"
  box_w=$((screen_w * 86 / 100))
  box_h=$((screen_h * 82 / 100))

  local styled_msg="<span font_desc='Noto Sans Bold 30' foreground='${STEAM_BLUE}'>${heading}</span>\n\n<span font_desc='Noto Sans 22' foreground='${STEAM_TEXT}'>${detail}</span>"
  local yad_msg="\n\n\n<span font_desc='Noto Sans Bold 46' foreground='${STEAM_BLUE}'>${heading}</span>\n\n<span font_desc='Noto Sans 34' foreground='${STEAM_TEXT}'>${detail}</span>\n\n"

  local steps
  steps=$((DISPLAY_SECONDS * 10))

  if command -v yad >/dev/null 2>&1; then
    {
      local i pct
      for i in $(seq 0 "$steps"); do
        pct=$((i * 100 / steps))
        printf '#Returning to Game Selection...\n'
        printf '%s\n' "$pct"
        sleep 0.1
      done
    } | GTK_THEME=Adwaita:dark yad --progress \
      --center \
      --on-top \
      --skip-taskbar \
      --undecorated \
      --fullscreen \
      --borders="$OVERLAY_BORDERS" \
      --no-cancel \
      --auto-close \
      --timeout="$DISPLAY_SECONDS" \
      --text-align=center \
      --title="$APP_TITLE" \
      --text="$yad_msg" \
      --percentage=0 \
      --width="$box_w" \
      --height="$box_h" >/dev/null 2>&1 && return
  fi

  if command -v zenity >/dev/null 2>&1; then
    {
      local i pct
      for i in $(seq 0 "$steps"); do
        pct=$((i * 100 / steps))
        printf '#<span font_desc="Noto Sans 18" foreground="%s">Returning to Game Selection...</span>\n' "$STEAM_TEXT"
        printf '%s\n' "$pct"
        sleep 0.1
      done
    } | GTK_THEME=Adwaita:dark zenity --progress \
      --no-cancel \
      --auto-close \
      --title="$APP_TITLE" \
      --window-icon=steam \
      --text="$styled_msg" \
      --percentage=0 \
      --width="$box_w" \
      --height="$box_h" >/dev/null 2>&1 || true
    return
  fi

  if command -v kdialog >/dev/null 2>&1; then
    # Rich text in a passive popup to maintain style without a button.
    local rich_msg="<div style='font-family:Noto Sans; font-size:22pt; color:${STEAM_BLUE};'>${heading}</div><br/><div style='font-family:Noto Sans; font-size:16pt; color:${STEAM_TEXT};'>${detail}</div>"
    kdialog --title "$APP_TITLE" --passivepopup "$rich_msg" "$DISPLAY_SECONDS" >/dev/null 2>&1 || true
    return
  fi

  if command -v notify-send >/dev/null 2>&1; then
    notify-send "$APP_TITLE" "$heading $detail" >/dev/null 2>&1 || true
    sleep "$DISPLAY_SECONDS"
    return
  fi

  printf '\n\033[38;5;117m%s\033[0m\n\033[38;5;152m%s\033[0m\n' "$heading" "$detail" >&2
  sleep "$DISPLAY_SECONDS"
}

ensure_shortcut_installed() {
  local media_root="$1"
  local installer_path="$media_root/$INSTALL_SCRIPT"

  if [[ ! -f "$installer_path" ]]; then
    return 0
  fi

  chmod +x "$installer_path" 2>/dev/null || true
  if bash "$installer_path"; then
    return 0
  fi

  show_graphic_error "Shortcut install failed." "Launching current game..."
  return 0
}

find_target_script() {
  local roots=(
    "/run/media/${USER:-}"
    "/media/${USER:-}"
    "/media"
    "/mnt"
  )

  local root
  local found
  for root in "${roots[@]}"; do
    [[ -n "$root" && -d "$root" ]] || continue
    found="$(find "$root" -maxdepth 4 -type f -name "$TARGET_SCRIPT" -print -quit 2>/dev/null || true)"
    if [[ -n "$found" ]]; then
      printf '%s\n' "$found"
      return 0
    fi
  done

  return 1
}

main() {
  local target
  if ! target="$(find_target_script)"; then
    show_graphic_error "No InsertPlay media detected." "Returning to Game Selection..."
    exit 1
  fi

  local media_root
  media_root="$(dirname "$target")"

  ensure_shortcut_installed "$media_root"

  exec /bin/bash "$target"
}

main "$@"
