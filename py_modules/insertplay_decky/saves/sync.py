"""Save sync algorithm (docs/remote-library.md#client).

Files under `home` (`~/.local/share/insertplay`), shared by the launch wrapper's CLI and
the plugin backend:

    saves/config.json        url, token, sync, backup, device (mode 0600, written by the plugin)
    saves/state/<key>.json   lastSyncedHash, lastRev, pending, root, spec
    saves/last/<key>.json    result of the last operation, read by the panel and the toasts
    saves/locks/<key>.lock   flock: held by the wrapper for the whole game
    saves/tmp/               archives being built or downloaded
"""
import json
import os
import re
import socket
import time

from . import client as http
from .snapshot import Snapshot, SnapshotError, restore, scan, write_archive
from .strategies import StrategyError, from_block, infer, manifest_spec, read_manifest

KEY_RE = re.compile(r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|steam-[0-9]{1,10})$")
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DEVICE_RE = re.compile(r"[^A-Za-z0-9._-]")
IDENTITY = "shortcut-identity.json"

# results that leave the game without sync this time (the wrapper runs the game directly)
SKIP = ("disabled", "wrong_card", "no_strategy", "busy")


def default_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".local", "share", "insertplay")


def device_name() -> str:
    name = DEVICE_RE.sub("-", socket.gethostname() or "device").strip("-.") or "device"
    return name[:64]


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: str, data: dict, private: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class Paths:
    def __init__(self, home: str = None):
        self.home = home or default_home()
        self.saves = os.path.join(self.home, "saves")
        self.config = os.path.join(self.saves, "config.json")
        self.tmp = os.path.join(self.saves, "tmp")

    def state(self, key: str) -> str:
        return os.path.join(self.saves, "state", f"{key}.json")

    def last(self, key: str) -> str:
        return os.path.join(self.saves, "last", f"{key}.json")

    def lock(self, key: str) -> str:
        return os.path.join(self.saves, "locks", f"{key}.lock")


def load_config(paths: Paths) -> dict:
    data = _read_json(paths.config) or {}
    return {
        "url": data.get("url") if isinstance(data.get("url"), str) else None,
        "token": data.get("token") if isinstance(data.get("token"), str) else None,
        "sync": data.get("sync") is True,
        "backup": data.get("backup") is not False,
        "device": data.get("device") if isinstance(data.get("device"), str) and data.get("device") else device_name(),
    }


def write_config(paths: Paths, url, token, sync: bool, backup: bool, device: str = None) -> None:
    _write_json(
        paths.config,
        {"url": url, "token": token, "sync": bool(sync), "backup": bool(backup), "device": device or device_name()},
        private=True,
    )


def load_state(paths: Paths, key: str) -> dict:
    return _read_json(paths.state(key)) or {}


def save_state(paths: Paths, key: str, state: dict) -> None:
    _write_json(paths.state(key), {**state, "updatedAt": time.time()})


def last_result(paths: Paths, key: str):
    return _read_json(paths.last(key))


def pending_keys(paths: Paths) -> list:
    folder = os.path.join(paths.saves, "state")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        key = name[:-5] if name.endswith(".json") else ""
        if KEY_RE.match(key) and (load_state(paths, key).get("pending") is True):
            out.append(key)
    return out


def identity_guid(root: str):
    for path in (os.path.join(root, IDENTITY), os.path.join(root, "insertplay", IDENTITY)):
        data = None
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and k.lower() == "guid" and isinstance(v, str) and GUID_RE.match(v.strip()):
                    return v.strip().lower()
        return None
    return None


class Lock:
    """flock on `saves/locks/<key>.lock`. `wait` seconds: 0 = don't wait."""

    def __init__(self, paths: Paths, key: str, wait: float = 0):
        self.path = paths.lock(key)
        self.wait = wait
        self.fd = None

    def acquire(self) -> bool:
        import fcntl  # Linux only; imported here so the module loads anywhere

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    self.fd = None
                    return False
                time.sleep(0.25)

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Sync:
    """One game's sync on this device. `root` is the game folder ({SD_ROOT})."""

    def __init__(self, paths: Paths, key: str, root: str, config: dict = None, now=time.time):
        self.paths = paths
        self.key = key
        self.root = root
        self.config = config if config is not None else load_config(paths)
        self.now = now
        self.state = load_state(paths, key)
        self.client = None
        self.spec = None
        self._snap = None

    # ---- results

    def _record(self, op: str, result: str, message: str = "", persist: bool = True, **extra) -> dict:
        record = {
            "key": self.key,
            "op": op,
            "result": result,
            "message": message,
            "at": self.now(),
            "device": self.config["device"],
            "strategy": self.spec.strategy if self.spec else None,
            "source": self.spec.source if self.spec else None,
            "pending": bool(self.state.get("pending")),
            **extra,
        }
        if persist:
            try:
                _write_json(self.paths.last(self.key), record)
            except OSError:
                pass
        return record

    def _synced(self, snap_hash: str, rev) -> None:
        self.state.update({"lastSyncedHash": snap_hash, "lastRev": str(rev) if rev is not None else None})
        self.state["pending"] = False
        self._save()

    def _save(self) -> None:
        self.state["root"] = self.root
        if self.spec is not None and self.spec.source != "manifest":
            self.state["spec"] = {**self.spec.as_json(), "source": self.spec.source}
        save_state(self.paths, self.key, self.state)

    # ---- setup

    def _ready(self, op: str):
        """None when sync can go on, else the result record."""
        if not KEY_RE.match(self.key or ""):
            return self._record(op, "wrong_card", "invalid key")
        cfg = self.config
        if not cfg["sync"] or not cfg["url"] or not cfg["token"]:
            return self._record(op, "disabled")
        if identity_guid(self.root) != self.key:
            return self._record(op, "wrong_card", f"{self.root} is not the game {self.key}")
        try:
            self.client = http.Client(cfg["url"], cfg["token"], cfg["device"])
        except http.ServerError as exc:
            return self._record(op, "error", str(exc))
        return None

    def _resolve(self):
        """Manifest block, then the server's strategy.json, then the one cached from an
        earlier run, then inference (written to the server). Raises StrategyError."""
        manifest = read_manifest(self.root)
        spec = manifest_spec(manifest)
        if spec is not None:
            return spec, False
        offline = False
        try:
            block = self.client.strategy(self.key)
            if block is not None:
                return from_block(block, "server"), False
        except http.Offline:
            offline = True
        cached = self.state.get("spec")
        if isinstance(cached, dict):
            try:
                return from_block(cached, cached.get("source") or "server"), offline
            except StrategyError:
                pass
        spec = infer(self.root, manifest)
        if not offline:
            try:
                self.client.put_strategy(
                    self.key, {**spec.as_json(), "inferred": True, "device": self.config["device"], "at": self.now()}
                )
            except http.Offline:
                offline = True
        return spec, offline

    def _tmp(self, name: str) -> str:
        os.makedirs(self.paths.tmp, exist_ok=True)
        return os.path.join(self.paths.tmp, f"{self.key}-{os.getpid()}-{name}")

    # ---- transfers

    def _upload(self, snap: Snapshot, rev, backup: bool) -> dict:
        path = self._tmp("up.tar.gz")
        try:
            write_archive(self.root, snap, path)
            result = self.client.upload(self.key, path, rev, snap.hash, snap.newest, backup)
        finally:
            if os.path.exists(path):
                os.remove(path)
        self._synced(snap.hash, result["meta"]["rev"])
        return result

    def _backup_local(self, snap: Snapshot) -> dict:
        """Sends the local save to the server's backups before it is overwritten."""
        path = self._tmp("bk.tar.gz")
        try:
            size = write_archive(self.root, snap, path)
            limit = self.client.backup_limit(self.key)
            # the server would drop it anyway: don't send 159 MB for nothing (Alan Wake 2)
            if limit is not None and size > limit:
                return {"stored": False, "reason": "limit", "size": size}
            return self.client.backup(self.key, path)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def _download(self, expected_hash: str) -> Snapshot:
        path = self._tmp("down.tar.gz")
        try:
            got = self.client.download(self.key, path)
            restored = restore(self.root, self.spec, path, got["hash"] or expected_hash)
        finally:
            if os.path.exists(path):
                os.remove(path)
        self._synced(restored.hash, got["rev"])
        return restored

    # ---- operations

    def pull(self) -> dict:
        """Before the game: bring the newest save to this device (table in the contract)."""
        return self._guarded("pull", self._pull)

    def push(self) -> dict:
        """After the game (and on retries): upload the save if it changed."""
        return self._guarded("push", self._push)

    def _guarded(self, op: str, fn) -> dict:
        """Every failure ends in a result record: the wrapper, the panel and the toasts need one."""
        early = self._ready(op)
        if early:
            return early
        try:
            return fn(op)
        except StrategyError as exc:
            return self._record(op, "no_strategy", str(exc))
        except http.Offline as exc:
            return self._offline(op, self._snap, str(exc))
        except http.ServerError as exc:
            return self._record(op, "error", str(exc))
        except (SnapshotError, OSError, ValueError) as exc:
            return self._record(op, "error", f"{type(exc).__name__}: {exc}")

    def _pull(self, op: str) -> dict:
        self.spec, offline = self._resolve()
        snap = self._snap = scan(self.root, self.spec)
        if offline:
            return self._offline(op, snap, "server unreachable")
        return self._decide(op, snap, attempts=2)

    def _push(self, op: str) -> dict:
        self.spec, offline = self._resolve()
        snap = self._snap = scan(self.root, self.spec)
        if snap.empty:
            return self._record("push", "empty")
        if snap.hash == self.state.get("lastSyncedHash"):
            if self.state.get("pending"):
                self.state["pending"] = False
                self._save()
            return self._record("push", "unchanged", hash=snap.hash, rev=self.state.get("lastRev"))
        if offline:
            return self._offline("push", snap, "server unreachable")
        rev = self.state.get("lastRev") if self.state.get("lastSyncedHash") else None
        try:
            result = self._upload(snap, rev, self.config["backup"])
            return self._record("push", "uploaded", hash=snap.hash, rev=result["meta"]["rev"],
                                size=snap.size, backup=result.get("backup"))
        except http.Conflict:
            # the other device uploaded since our last sync: decide like at launch
            return self._decide("push", snap, attempts=1)

    def _offline(self, op: str, snap, message: str) -> dict:
        if snap is not None and not snap.empty and snap.hash != self.state.get("lastSyncedHash"):
            self.state["pending"] = True
            self._save()
        return self._record(op, "offline", message, hash=snap.hash if snap is not None else None)

    def _decide(self, op: str, snap: Snapshot, attempts: int) -> dict:
        meta = self.client.meta(self.key)
        base = self.state.get("lastSyncedHash")
        try:
            if meta is None:
                if snap.empty:
                    return self._record(op, "empty")
                result = self._upload(snap, None, False)
                return self._record(op, "uploaded", hash=snap.hash, rev=result["meta"]["rev"], size=snap.size)
            if meta.get("hash") == snap.hash:
                self._synced(snap.hash, meta.get("rev"))
                return self._record(op, "unchanged", hash=snap.hash, rev=meta.get("rev"))
            local_changed = not snap.empty and snap.hash != base
            server_changed = meta.get("hash") != base
            if not local_changed:
                backup = self._backup_local(snap) if (self.config["backup"] and not snap.empty) else None
                restored = self._download(meta.get("hash"))
                return self._record(op, "downloaded", hash=restored.hash, rev=self.state.get("lastRev"),
                                    fromDevice=meta.get("device"), size=restored.size, backup=backup)
            if not server_changed:
                result = self._upload(snap, meta.get("rev"), self.config["backup"])
                return self._record(op, "uploaded", hash=snap.hash, rev=result["meta"]["rev"], size=snap.size,
                                    backup=result.get("backup"))
            # both changed: the newest wins, the loser always goes to the backups
            if float(meta.get("newestMtime") or 0) > snap.newest:
                backup = self._backup_local(snap)
                restored = self._download(meta.get("hash"))
                return self._record(op, "conflict_downloaded", hash=restored.hash, rev=self.state.get("lastRev"),
                                    fromDevice=meta.get("device"), size=restored.size, backup=backup)
            result = self._upload(snap, meta.get("rev"), True)
            return self._record(op, "conflict_uploaded", hash=snap.hash, rev=result["meta"]["rev"], size=snap.size,
                                backup=result.get("backup"))
        except http.Conflict:
            if attempts > 1:
                return self._decide(op, snap, attempts - 1)
            return self._record(op, "error", "the server changed during the sync; will retry")


def pull(paths: Paths, key: str, root: str) -> dict:
    return Sync(paths, key, root).pull()


def push(paths: Paths, key: str, root: str, wait: float = 0) -> dict:
    """Takes the key's lock (waiting up to `wait` s): never uploads while the game runs.
    State and config are read only once the lock is held: after a wait they may have changed."""
    lock = Lock(paths, key, wait)
    if not lock.acquire():
        # not written to last/<key>.json: the last real result stays visible
        return Sync(paths, key, root)._record("push", "busy", "the game is running", persist=False)
    try:
        return Sync(paths, key, root).push()
    finally:
        lock.release()
