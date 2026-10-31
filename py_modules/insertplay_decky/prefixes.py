"""Portable saves (issue #13): the Proton data of a Steam game on the card lives on the
card, linked to the SSD by a symlink while the card is inserted. Two modes:

- `userdata`: only the Windows user folder (`pfx/drive_c/users/steamuser`: Documents,
  AppData, Saved Games). The rest of the prefix stays on each device's SSD. Measured:
  Proton recopies ~115 MB of DLLs into the prefix on every launch; on a slow card that
  took minutes, with the prefix on the SSD it takes under 1 s;
- `prefix`: the whole prefix (`compatdata/<appid>`). Also carries the registry, but pays
  for that copy on the card on every launch.

No `decky` import: everything here runs and is tested off the Deck. Measurements in
docs/steam-api-notes.md ("Proton prefix on the card via symlink").

Rules:
- nothing is deleted. Whatever loses a conflict is renamed to a backup;
- the data of an open game is not touched;
- we only touch symlinks we created (target in `.insertplay/prefixes/` or
  `.insertplay/users/`)."""
import os
import re
import shutil
import stat
import time

from .store import _load, _save

CARD_DIR = ".insertplay"
CONFIG_NAME = "portable-saves.json"
MODES = ("off", "userdata", "prefix")
USERS = os.path.join("pfx", "drive_c", "users")
STEAMUSER = os.path.join(USERS, "steamuser")
# needs symlinks and Unix permissions (dosdevices, `My Documents` → `Documents`)
SUPPORTED_FS = {"ext4", "ext3", "btrfs", "xfs", "f2fs"}


def config_path(mount: str) -> str:
    return os.path.join(mount, CARD_DIR, CONFIG_NAME)


def compatdata(steam_root: str) -> str:
    return os.path.join(steam_root, "steamapps", "compatdata")


def get_mode(mount: str) -> str:
    data = _load(config_path(mount), {})
    mode = data.get("mode") if isinstance(data, dict) else None
    return mode if mode in MODES else "off"


def _write_mode(mount: str, mode: str, version: str) -> None:
    # minVersion: diagnostics only. A version without the feature ignores the whole folder.
    _save(config_path(mount), {"mode": mode, "minVersion": version})


class Layout:
    """Where a mode's data lives: `card` on the card, `ssd` the path that becomes a link and
    `inner` the part of a whole prefix that corresponds to them."""

    def __init__(self, mode: str, folder: str, inner: str):
        self.mode, self.folder, self.inner = mode, folder, inner
        self.mark = os.sep + os.path.join(CARD_DIR, folder) + os.sep

    def card(self, mount: str, appid: int) -> str:
        base = os.path.join(mount, CARD_DIR, self.folder, str(appid))
        return os.path.join(base, "steamuser") if self.inner else base

    def ssd(self, steam_root: str, appid: int) -> str:
        base = os.path.join(compatdata(steam_root), str(appid))
        return os.path.join(base, self.inner) if self.inner else base

    def valid(self, path: str) -> bool:
        if os.path.islink(path) or not os.path.isdir(path):
            return False
        return bool(self.inner) or os.path.isdir(os.path.join(path, "pfx"))

    def users_dir(self, path: str) -> str:
        """Folder to look in for user data (to pick the newest)."""
        return path if self.inner else os.path.join(path, USERS)


LAYOUTS = {
    "userdata": Layout("userdata", "users", STEAMUSER),
    "prefix": Layout("prefix", "prefixes", ""),
}


def card_prefix(mount: str, appid: int) -> str:
    return LAYOUTS["prefix"].card(mount, appid)


def card_userdata(mount: str, appid: int) -> str:
    return LAYOUTS["userdata"].card(mount, appid)


def _unescape(field: str) -> str:
    """`/proc/mounts` writes a space as `\\040` (`/run/media/deck/Call\\040of\\040Juarez\\040G`).
    A single pass: `\\134040` is a backslash followed by "040", not a space."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _mount_entry(mount: str, mounts_file: str):
    """(filesystem, options) of the most specific mount point that contains `mount`."""
    real = os.path.realpath(mount)
    best, entry = "", (None, ())
    try:
        with open(mounts_file, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                point = _unescape(parts[1])
                inside = real == point or real.startswith(point.rstrip(os.sep) + os.sep)
                if inside and len(point) > len(best):
                    best, entry = point, (parts[2], tuple(parts[3].split(",")))
    except OSError:
        pass
    return entry


def fs_type(mount: str, mounts_file: str = "/proc/mounts"):
    """Filesystem of the most specific mount point that contains `mount`."""
    return _mount_entry(mount, mounts_file)[0]


def status(mount: str, mounts_file: str = "/proc/mounts") -> dict:
    kind, options = _mount_entry(mount, mounts_file)
    reason = None
    if kind not in SUPPORTED_FS:
        reason = f"o cartão é {kind or 'desconhecido'}; precisa ser ext4 (formatado pelo Steam)"
    elif "ro" in options or "emergency_ro" in options:
        # Measured on the Deck: ext4 with an error (bitmap with a bad checksum) is remounted
        # read-only seconds after mounting; `/proc/mounts` shows `emergency_ro`.
        reason = "o cartão está somente leitura (erro no sistema de arquivos; rode e2fsck)"
    return {"mode": get_mode(mount), "fsOk": reason is None, "fsType": kind, "reason": reason}


def _user_data_mtime(users: str):
    """Newest mtime of the user files (outside `AppData/Local/Temp` and
    `AppData/Local/dxvk`), or None
    if there are none. Measured: the prefix Steam creates on a move or install has no
    file there at all; that of a played game has the save or the settings. Without this, the
    freshly created empty prefix would beat the save for being the newest."""
    newest = None
    for root, dirs, files in os.walk(users):
        # `Temp` and the DXVK cache (`AppData/Local/dxvk/*.dxvk.bin`, rewritten on every
        # run) aren't the user's: with them, the device that ran last would win
        # even with no save at all. They are still copied, they just don't count.
        if os.path.basename(root) in ("Temp", "dxvk") and os.path.basename(os.path.dirname(root)) == "Local":
            dirs[:] = []
            continue
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            # a link (`My Documents` → broken `Documents`) is not user data
            if not stat.S_ISREG(st.st_mode):
                continue
            if newest is None or st.st_mtime > newest:
                newest = st.st_mtime
    return newest


def _free_name(path: str) -> str:
    candidate, n = path, 1
    while os.path.lexists(candidate):
        n += 1
        candidate = f"{path}-{n}"
    return candidate


def _stamp(now: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def _ssd_backup(path: str, now: float) -> str:
    target = _free_name(f"{path}.insertplay-backup-{_stamp(now)}")
    os.rename(path, target)
    return target


def _card_backup(mount: str, path: str, appid: int, now: float) -> str:
    folder = os.path.join(mount, CARD_DIR, "backups")
    os.makedirs(folder, exist_ok=True)
    target = _free_name(os.path.join(folder, f"{appid}-{_stamp(now)}"))
    os.rename(path, target)
    return target


def _copy(src: str, dest: str) -> None:
    """Cross-disk copy that never leaves `dest` half-done: copies to `.partial` and
    renames at the end. A stale `.partial` (interrupted copy) is discarded: it is our copy,
    the source is still whole."""
    partial = dest + ".partial"
    if os.path.lexists(partial):
        shutil.rmtree(partial)
    shutil.copytree(src, partial, symlinks=True)
    os.rename(partial, dest)


def live_appids(proc_root: str = "/proc") -> set:
    """Appids with a live process right now. Steam puts `SteamAppId=<appid>` in the environment of
    the game's processes (measured in the `environ` of Bully's Proton on the Steam Machine). The
    frontend's list is taken at the start of the pass; this holds at the moment of each swap."""
    found = set()
    try:
        pids = [n for n in os.listdir(proc_root) if n.isdigit()]
    except OSError:
        return found
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "environ"), "rb") as f:
                env = f.read()
        except OSError:  # another user's process, or one that just exited
            continue
        for var in env.split(b"\0"):
            if var.startswith(b"SteamAppId="):
                value = var[len(b"SteamAppId="):]
                if value.isdigit() and int(value) > 0:
                    found.add(int(value))
    return found


# replaced in tests
LIVE_APPIDS = live_appids


def _running(appid: int, running=()) -> bool:
    return appid in running or appid in LIVE_APPIDS()


def _ours(link: str, layout: Layout) -> bool:
    return os.path.islink(link) and layout.mark in os.readlink(link)


def _link(ssd: str, dest: str) -> None:
    if os.path.islink(ssd):
        os.remove(ssd)  # only the link; the target stays
    os.makedirs(os.path.dirname(ssd), exist_ok=True)
    os.symlink(dest, ssd)


def _leftovers(libraries: list, appid: int) -> list:
    """The SSD → card move takes `compatdata/<appid>` to the card's library (following
    our links). That copy blocks the move back (issue #13)."""
    return [os.path.join(lib, "steamapps", "compatdata", str(appid)) for lib in libraries]


def sync_app(layout: Layout, mount: str, libraries: list, appid: int, steam_root: str, now: float,
             running=()) -> str:
    """Leaves the SSD path (`layout.ssd`) pointing to the card.

    Candidates: what is already on the card, the matching part of the copies a move
    leaves in `steamapps/compatdata` and the real folder on the SSD. The one with the newest
    user data wins; with no data in any, the card's. The others become backups."""
    ssd = layout.ssd(steam_root, appid)
    dest = layout.card(mount, appid)
    lefts = [p for p in _leftovers(libraries, appid) if os.path.isdir(p) and not os.path.islink(p)]
    if os.path.islink(ssd) and not _ours(ssd, layout):
        return "foreign"
    # `compatdata/<appid>` is already a link (prefix mode, another card, someone): the
    # `steamuser` path would land inside its target
    if layout.inner and os.path.islink(os.path.join(compatdata(steam_root), str(appid))):
        return "foreign"
    # the reverse: a whole prefix whose `steamuser` is a link to another card (userdata mode).
    # Copied with the links, this card's saves would go to the other one.
    if not layout.inner and os.path.islink(os.path.join(ssd, STEAMUSER)):
        return "foreign"
    if os.path.islink(ssd) and os.readlink(ssd) == dest and layout.valid(dest):
        # With the link in place, the copy in `steamapps/compatdata` is usually the one the
        # SSD → card move made by following the link: it goes to the backups. But desktop-mode
        # Steam uses exactly that folder. If it has a newer save than the card's, the
        # newest-wins choice decides, with a conflict warning.
        current = _user_data_mtime(layout.users_dir(dest))
        newer = False
        for left in lefts:
            inner = os.path.join(left, layout.inner) if layout.inner else left
            m = _user_data_mtime(layout.users_dir(inner)) if layout.valid(inner) else None
            if m is not None and (current is None or m > current):
                newer = True
        if not newer:
            for path in lefts:
                _card_backup(mount, path, appid, now)
            return "leftover" if lefts else "ok"

    candidates = []  # (path, where) in preference order when there's no data
    if layout.valid(dest):
        candidates.append((dest, "card"))
    for left in lefts:
        inner = os.path.join(left, layout.inner) if layout.inner else left
        if layout.valid(inner):
            candidates.append((inner, "leftover"))
    ssd_real = os.path.isdir(ssd) and not os.path.islink(ssd)
    if ssd_real and layout.valid(ssd):
        candidates.append((ssd, "ssd"))
    if not candidates:
        return "none"

    scored = [(path, where, _user_data_mtime(layout.users_dir(path))) for path, where in candidates]
    with_data = [s for s in scored if s[2] is not None]
    winner, where, newest = max(with_data, key=lambda s: s[2]) if with_data else scored[0]
    # A loser with user data from another date = there was another save. With no data, or with
    # the same newest date (a copy of the same save), the backup is silent.
    conflict = any(m is not None and m != newest for path, _, m in scored if path != winner)

    # the frontend sent the list of open games at the start of the pass; check again
    # before the first change
    if _running(appid, running):
        return "running"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # a target that didn't win gets out of the way, valid or not (folder without `pfx`, partial
    # copy): the rename over it would fail on every pass
    if where != "card" and os.path.lexists(dest):
        _card_backup(mount, dest, appid, now)
    if where == "ssd":
        _copy(ssd, dest)
        # Copying the whole prefix to a slow card takes minutes. If the game opened midway
        # and saved, the copy is already stale: we discard it (it's ours) and the next pass,
        # with the game closed, tries again. The SSD folder stays intact.
        if _user_data_mtime(layout.users_dir(ssd)) != newest:
            shutil.rmtree(dest)
            return "changed"
        if _running(appid, running):  # opened during the copy: the SSD folder is in use
            shutil.rmtree(dest)
            return "running"
    if where == "leftover":
        os.rename(winner, dest)
    for left in lefts:
        if os.path.lexists(left):
            _card_backup(mount, left, appid, now)
    # The SSD folder gets out of the link's way. If it won, it is already copied to the card and
    # stays as a backup on the SSD; if it lost, too. An invalid folder (Steam creates only
    # `config_info` in some steps) is kept too, never deleted.
    if ssd_real:
        _ssd_backup(ssd, now)
    # userdata with no prefix on the device (the game never ran here): creates only the path up to
    # `users/`. Measured: Proton builds the prefix around the link and the save already shows up on
    # the first launch.
    _link(ssd, dest)
    if conflict:
        return "conflict"
    return "migrated" if where != "card" else "linked"


def _links_to(steam_root: str, mount: str) -> dict:
    """`{appid: (layout, link)}` of our symlinks that point to this card."""
    folder = compatdata(steam_root)
    real = os.path.realpath(mount)
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        for layout in LAYOUTS.values():
            link = layout.ssd(steam_root, int(name))
            base = os.path.join(real, CARD_DIR, layout.folder) + os.sep
            if _ours(link, layout) and os.readlink(link).startswith(base):
                out[int(name)] = (layout, link)
    return out


def _restore(link: str, src: str, layout: Layout, appid: int, running=()) -> str:
    """Replaces the link with a copy of what is on the card (which stays intact). The link only goes
    after the copy is complete alongside it and checked: if the game opened or saved during
    the copy, it is discarded and the link stays. Returns `restored`, `running` or `changed`."""
    if _running(appid, running):
        return "running"
    staged = link + ".insertplay-restore"
    # a restore interrupted between the copy and the swap leaves the finished copy here; it
    # becomes a backup instead of blocking every later attempt
    if os.path.lexists(staged):
        os.rename(staged, _free_name(staged + ".old"))
    before = _user_data_mtime(layout.users_dir(src))
    _copy(src, staged)
    if _user_data_mtime(layout.users_dir(src)) != before or _running(appid, running):
        shutil.rmtree(staged)
        return "changed"
    os.remove(link)
    os.rename(staged, link)
    return "restored"


def sync(mount: str, libraries: list, appids: list, running: list, elsewhere: list, steam_root: str, now=None) -> dict:
    """One portable saves pass over a card. When off, does nothing. Returns
    `{appid: result}`.

    - appid on the card: `sync_app` in the card's mode;
    - our link to this card for an appid that left it: moved to another
      library (`elsewhere`), it becomes a copy on the SSD so the game keeps its save;
      uninstalled, it only loses the link. The card is never deleted."""
    mode = get_mode(mount)
    if mode == "off":
        # Turned off on another device: the links this device still has to the card
        # become copies, otherwise the game would keep writing to the card and, without the card, the save
        # would vanish from this device. With no links, does nothing.
        return _release_all(mount, running, steam_root)
    layout = LAYOUTS[mode]
    now = time.time() if now is None else now
    running = set(running) | LIVE_APPIDS()
    elsewhere = set(elsewhere)
    out = {}
    # Mode switched on another device ("Saves" <-> "Prefixo completo"): this device's link for the old
    # mode becomes a copy again before applying the new one. Without this the new mode
    # saw the old link as foreign (`foreign`) and the game kept using the old data.
    for appid, (kind, link) in sorted(_links_to(steam_root, mount).items()):
        if kind is layout:
            continue
        try:
            src = kind.card(mount, appid)
            result = _restore(link, src, kind, appid, running) if kind.valid(src) else "unlinked"
            if result == "unlinked":
                os.remove(link)
        except OSError as exc:
            result = f"error: {exc}"
        if result != "restored":
            out[appid] = result  # open game or failure: the new mode waits for that game
    for appid in sorted(set(appids)):
        if appid in out:
            continue
        if appid in running:
            out[appid] = "running"
            continue
        try:
            out[appid] = sync_app(layout, mount, libraries, appid, steam_root, now, running)
        except OSError as exc:
            out[appid] = f"error: {exc}"
    for appid, (kind, link) in sorted(_links_to(steam_root, mount).items()):
        if appid in out or appid in running:
            continue
        try:
            src = kind.card(mount, appid)
            if appid in elsewhere and kind.valid(src):
                out[appid] = _restore(link, src, kind, appid, running)
            else:
                os.remove(link)
                out[appid] = "unlinked"
        except OSError as exc:
            out[appid] = f"error: {exc}"
    return out


def _release_all(mount: str, running: list, steam_root: str) -> dict:
    """Every link to the card becomes a copy on the SSD. With a card game open, touches
    nothing (returns `running` for it)."""
    links = _links_to(steam_root, mount)
    running = set(running) | LIVE_APPIDS()
    busy = sorted(set(links) & running)
    if busy:
        return {appid: "running" for appid in busy}
    out = {}
    for appid, (layout, link) in sorted(links.items()):
        src = layout.card(mount, appid)
        try:
            if layout.valid(src):
                out[appid] = _restore(link, src, layout, appid, running)
            else:
                os.remove(link)
                out[appid] = "unlinked"
        except OSError as exc:
            out[appid] = f"error: {exc}"
    return out


def set_mode(mount: str, mode: str, libraries: list, appids: list, running: list, elsewhere: list,
             steam_root: str, version: str, now=None) -> dict:
    """Switches the card's mode. Leaving a mode copies back to the SSD what was on the
    card; entering a mode already runs the first pass. If a card game is open,
    nothing changes (result `running`)."""
    if mode not in MODES:
        raise ValueError(f"unknown portable mode: {mode!r}")
    current = get_mode(mount)
    if current == mode:
        # choosing the active mode again isn't a switch: just a pass
        return sync(mount, libraries, appids, running, elsewhere, steam_root, now)
    results = {}
    if current != "off":
        results = _release_all(mount, running, steam_root)
        # open game or failed copy back: the mode stays, to try again
        if any(r in ("running", "changed") or r.startswith("error") for r in results.values()):
            return results
    _write_mode(mount, mode, version)
    if mode != "off":
        results.update(sync(mount, libraries, appids, running, elsewhere, steam_root, now))
    return results


def unlink_dangling(steam_root: str, running=()) -> list:
    """Removes our links whose card isn't mounted (broken link). Measured:
    harmless for Steam, but it would get in the way of reinstalling the game on the SSD with the card out.
    An open game keeps its link: the card may come back, and the normal pass doesn't touch an open
    game to redo it."""
    running = set(running) | LIVE_APPIDS()
    folder = compatdata(steam_root)
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in sorted(names):
        if not name.isdigit() or int(name) in running:
            continue
        for layout in LAYOUTS.values():
            link = layout.ssd(steam_root, int(name))
            if _ours(link, layout) and not os.path.exists(link):
                os.remove(link)
                removed.append(int(name))
    return removed
