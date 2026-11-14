"""Installs the launch wrapper and a copy of this package in `~/.local/share/insertplay`,
outside the plugin folder: a shortcut keeps launching (without sync) even after the plugin
is removed. Rewritten by the plugin at load when its content changed."""
import hashlib
import os
import shutil

from .sync import Paths

PACKAGE = "insertplay_saves"
ENTRY_NAME = "insertplay-saves.py"
WRAPPER_NAME = "insertplay-run"

# Steam runs: /bin/bash <wrapper> <key> <game folder> -- <command…>  (docs/remote-library.md#launch-hook)
WRAPPER = r"""#!/bin/bash
# InsertPlay save sync launch wrapper (issue #63). Written by the plugin: edits are lost.
#   insertplay-run <key> <game folder> -- <command...>
# Downloads the newest save, runs the game, uploads the save afterwards. Whatever fails
# here, the game still starts: python3 or the module missing, sync off, server down.
if [[ $# -lt 3 || "$3" != "--" ]]; then
	echo "insertplay-run: usage: insertplay-run <key> <game folder> -- <command...>" >&2
	exit 64
fi
key="$1"
root="$2"
shift 3
[[ "$key" =~ ^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|steam-[0-9]{1,10})$ ]] || exec "$@"
D="${INSERTPLAY_HOME:-$HOME/.local/share/insertplay}"
ENTRY="$D/lib/insertplay-saves.py"
PY=/usr/bin/python3
[[ -x "$PY" ]] || PY="$(command -v python3)"
[[ -n "$PY" && -f "$ENTRY" ]] || exec "$@"
# Steam's LD_PRELOAD (32- and 64-bit overlay) and runtime paths are not for python3
py() { env -u LD_PRELOAD -u LD_LIBRARY_PATH -u PYTHONHOME -u PYTHONPATH "$PY" -I "$ENTRY" "$@" >/dev/null 2>&1; }

mkdir -p "$D/saves/locks" 2>/dev/null
# the lock is held for the whole game (the game inherits fd 9): nothing uploads mid-game
exec 9>>"$D/saves/locks/$key.lock" || exec "$@"
flock -w 10 9 || { exec 9>&-; exec "$@"; }
py pull "$key" "$root"
if [[ $? -eq 3 ]]; then
	exec 9>&-
	exec "$@"
fi

# "Sair do jogo" kills the whole game ~1.7 s after SIGTERM (docs/steam-api-notes.md): the
# upload then runs in a unit outside the game's scope, once the lock is free
detached=0
after_kill() {
	[[ $detached -eq 1 ]] && return
	detached=1
	systemd-run --user --collect --quiet --unit="insertplay-saves-$key-$$" --setenv=INSERTPLAY_HOME="$D" \
		env -u LD_PRELOAD -u LD_LIBRARY_PATH "$PY" -I "$ENTRY" push "$key" "$root" --wait 300 >/dev/null 2>&1
}
trap after_kill INT TERM HUP

# In the background, so `wait` returns at Steam's signal and the trap runs at once (in the
# foreground bash runs it only after the game exits, and the kill comes first: measured with
# DuckStation). Like the old exec, the game keeps stdin and SIGINT: a background job would get
# /dev/null and ignore SIGINT/SIGQUIT, which `<&0` and `env --default-signal` undo.
if env --default-signal=INT true 2>/dev/null; then
	env --default-signal=INT,QUIT "$@" <&0 &
else
	"$@" <&0 &
fi
child=$!
wait "$child"
status=$?
while kill -0 "$child" 2>/dev/null; do
	wait "$child"
	status=$?
done
py push "$key" "$root" --locked
exit "$status"
"""

ENTRY = r"""#!/usr/bin/env python3
# InsertPlay save sync CLI (issue #63). Written by the plugin: edits are lost.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from insertplay_saves.cli import main  # noqa: E402

sys.exit(main())
"""


def wrapper_path(home: str = None) -> str:
    return os.path.join(Paths(home).home, "bin", WRAPPER_NAME)


def _sources(package_dir: str) -> list:
    return sorted(n for n in os.listdir(package_dir) if n.endswith(".py"))


def _stamp(package_dir: str) -> str:
    h = hashlib.sha256(WRAPPER.encode() + ENTRY.encode())
    for name in _sources(package_dir):
        h.update(name.encode())
        with open(os.path.join(package_dir, name), "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _write(path: str, text: str, mode: int) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def install(home: str = None, package_dir: str = None) -> bool:
    """Copies the package and writes the wrapper and the entry. Returns True when something
    changed. Never touches anything else in `home`."""
    paths = Paths(home)
    package_dir = package_dir or os.path.dirname(os.path.abspath(__file__))
    lib = os.path.join(paths.home, "lib")
    target = os.path.join(lib, PACKAGE)
    stamp_path = os.path.join(target, ".stamp")
    stamp = _stamp(package_dir)
    try:
        with open(stamp_path, encoding="utf-8") as f:
            if f.read().strip() == stamp and os.path.isfile(wrapper_path(home)) and os.path.isfile(os.path.join(lib, ENTRY_NAME)):
                return False
    except OSError:
        pass
    os.makedirs(lib, exist_ok=True)
    os.makedirs(os.path.join(paths.home, "bin"), exist_ok=True)
    staging = os.path.join(lib, f".{PACKAGE}.new")
    old = os.path.join(lib, f".{PACKAGE}.old")
    for leftover in (staging, old):
        if os.path.isdir(leftover):
            shutil.rmtree(leftover)
    os.makedirs(staging)
    for name in _sources(package_dir):
        shutil.copyfile(os.path.join(package_dir, name), os.path.join(staging, name))
    with open(os.path.join(staging, ".stamp"), "w", encoding="utf-8") as f:
        f.write(stamp)
    if os.path.isdir(target):
        os.replace(target, old)
    os.replace(staging, target)
    if os.path.isdir(old):
        shutil.rmtree(old)
    _write(os.path.join(lib, ENTRY_NAME), ENTRY, 0o644)
    _write(wrapper_path(home), WRAPPER, 0o755)
    return True
