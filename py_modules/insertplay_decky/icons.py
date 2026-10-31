"""Shortcut icons (issue #17).

Measured on the Deck (2026-10-27): Steam stores only the *path* of a shortcut's icon and
loads it on demand. With the file on the card, the shortcut has no icon when Steam
restarts with the card out, and a failure on a path only clears on the next restart. So
the icon is copied to the SSD, with the content hash in the name: a new icon on the
card becomes a new path, without running into what Steam cached for an old path.
"""
import glob
import hashlib
import os
import shutil
import struct
import time

# steamid64 = this value + account id (the folder in userdata/)
STEAMID64_BASE = 76561197960265728
HASH_CHARS = 12


def account_id(steamid: str) -> int:
    account = int(steamid) - STEAMID64_BASE
    if account <= 0:
        raise ValueError(f"not a steamid64: {steamid!r}")
    return account


def shortcuts_vdf(steam_root: str, steamid: str) -> str:
    return os.path.join(steam_root, "userdata", str(account_id(steamid)), "config", "shortcuts.vdf")


def _read_map(data: bytes, i: int) -> tuple:
    out = {}
    while True:
        kind = data[i]
        i += 1
        if kind == 0x08:
            return out, i
        end = data.index(b"\x00", i)
        key = data[i:end].decode("utf-8", "replace").lower()
        i = end + 1
        if kind == 0x00:
            out[key], i = _read_map(data, i)
        elif kind == 0x01:
            end = data.index(b"\x00", i)
            out[key] = data[i:end].decode("utf-8", "replace")
            i = end + 1
        elif kind == 0x02:
            out[key] = struct.unpack_from("<i", data, i)[0]
            i += 4
        elif kind == 0x07:
            out[key] = struct.unpack_from("<Q", data, i)[0]
            i += 8
        else:
            raise ValueError(f"unknown VDF type {kind:#x} at byte {i - 1}")


def shortcut_icons(path: str) -> dict:
    """appid → icon path ("" = no icon), read from the binary `shortcuts.vdf`.

    The frontend can't see that path (neither `appStore` nor `appDetailsStore`); the
    file is the only source. Measured: Steam writes it ~0.16 s after `SetShortcutIcon`.
    SteamGridDB, in turn, edits the file directly."""
    with open(path, "rb") as f:
        data = f.read()
    root, _ = _read_map(data, 0)
    out = {}
    for entry in (root.get("shortcuts") or {}).values():
        if isinstance(entry, dict) and "appid" in entry:
            out[(entry["appid"] & 0xFFFFFFFF) | 0x80000000] = str(entry.get("icon") or "")
    return out


def _ours(path: str, icons_dir: str) -> bool:
    return os.path.dirname(os.path.normpath(path)) == os.path.normpath(icons_dir)


def content_hash(source: str) -> str:
    sha = hashlib.sha256()
    with open(source, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()[:HASH_CHARS]


def target_path(current, source: str, guid: str, icons_dir: str, now=time.time) -> str:
    """Path of the copy of `source` on the SSD: `<guid>-<hash>-<generation>.<ext>`.

    Same content already applied → the current path, and nothing to do. New content →
    always a never-used path, even if the content was used before: Steam
    doesn't reread a path it already loaded in the session (A → B → A would stay on B until restart)."""
    digest = content_hash(source)
    if current and _ours(current, icons_dir):
        stem = os.path.splitext(os.path.basename(current))[0]
        if stem == f"{guid}-{digest}" or stem.startswith(f"{guid}-{digest}-"):
            return current
    ext = os.path.splitext(source)[1].lower() or ".png"
    generation = int(now() * 1000)
    while True:
        path = os.path.join(icons_dir, f"{guid}-{digest}-{generation:x}{ext}")
        if not os.path.exists(path):
            return path
        generation += 1


def plan(current, target: str, icons_dir: str) -> bool:
    """Apply `target`? `current` is the shortcut's icon; `None` = the shortcut is not
    in `shortcuts.vdf` yet (just created). Only replaces a missing icon or
    one the plugin itself wrote: anything else was the user's choice."""
    if current is None or current == "":
        return True
    return current != target and _ours(current, icons_dir)


def stage(source: str, target: str) -> None:
    """Copies the icon to `target`, if it isn't there yet. Writes to a temp file and swaps
    in one go: Steam never sees a half-written file."""
    if os.path.isfile(target):
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = f"{target}.tmp"
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)


def referenced_icons(steam_root: str) -> set:
    """Icons used by any shortcut of any Steam account. The copies folder belongs to the
    device, not the account: an old version may be in use by the other account.
    Raises if a `shortcuts.vdf` can't be read: when in doubt, nothing is deleted."""
    used = set()
    for path in glob.glob(os.path.join(steam_root, "userdata", "*", "config", "shortcuts.vdf")):
        used.update(os.path.normpath(icon) for icon in shortcut_icons(path).values() if icon)
    return used


def prune(icons_dir: str, keep: set) -> list:
    """Deletes the copies not in `keep` (paths in use). The replaced version only goes on a
    later call, after Steam wrote the new one to `shortcuts.vdf`: if
    `SetShortcutIcon` fails, the shortcut isn't left pointing at a deleted file."""
    removed = []
    try:
        names = os.listdir(icons_dir)
    except FileNotFoundError:
        return removed
    keep = {os.path.normpath(path) for path in keep}
    for name in names:
        path = os.path.normpath(os.path.join(icons_dir, name))
        if path in keep:
            continue
        try:
            os.remove(path)
            removed.append(name)
        except OSError:
            pass
    return removed
