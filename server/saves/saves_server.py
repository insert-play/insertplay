"""InsertPlay save server (issue #63): keeps the latest save of each game, plus backups.

Standard library only. Runs behind Caddy, which forwards `/saves/*` after its own token
check; this server checks the token again, so it is never open by itself. Contract:
docs/remote-library.md ("Saves").

    GET  /saves/<key>/meta               meta.json, or 404 with no save yet
    GET  /saves/<key>/current.tar.gz     the save
    PUT  /saves/<key>/current.tar.gz     replace it (If-Match: <rev>, or If-None-Match: * the first time)
    GET  /saves/<key>/strategy.json      the inferred save strategy, or 404
    PUT  /saves/<key>/strategy.json
    GET  /saves/<key>/backups            list of backups, newest first
    GET  /saves/<key>/backups/<name>     one backup
    POST /saves/<key>/backups            store a save the device replaced or lost a conflict with

Layout on disk, under INSERTPLAY_SAVES_DIR:

    <key>/current.tar.gz
    <key>/meta.json         {"rev", "hash", "newestMtime", "device", "uploadedAt", "size"}
    <key>/strategy.json     optional
    <key>/backups/<ts>-<device>.tar.gz

Environment:
    INSERTPLAY_TOKEN            the same token as Caddy (required, >= 32 characters)
    INSERTPLAY_SAVES_DIR        storage root (default /srv/saves)
    INSERTPLAY_SAVES_PORT       port (default 8081)
    INSERTPLAY_SAVE_BACKUP_MB   backups kept per game, in MB (default 50). A single save
                                larger than this gets no backup; syncing it is not affected.
    INSERTPLAY_SAVE_MAX_MB      largest save accepted, in MB (default 1024)
"""
import hmac
import http.server
import json
import os
import re
import sys
import threading
import time

KEY_RE = re.compile(r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|steam-[0-9]{1,10})$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REV_RE = re.compile(r"^[0-9]{1,18}$")
BACKUP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}(?:-[0-9]+)?-[A-Za-z0-9._-]{1,64}\.tar\.gz$")
CURRENT = "current.tar.gz"
META = "meta.json"
STRATEGY = "strategy.json"
BACKUPS = "backups"
STRATEGY_LIMIT = 64 * 1024
CHUNK = 1024 * 1024
MB = 1024 * 1024


class Store:
    """All disk access. One lock per key: two devices uploading the same game never interleave."""

    def __init__(self, root: str, backup_limit: int, max_size: int):
        self.root = root
        self.backup_limit = backup_limit
        self.max_size = max_size
        self._locks = {}
        self._locks_guard = threading.Lock()

    def lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def folder(self, key: str) -> str:
        return os.path.join(self.root, key)

    def meta(self, key: str):
        return _read_json(os.path.join(self.folder(key), META))

    def strategy(self, key: str):
        return _read_json(os.path.join(self.folder(key), STRATEGY))

    def put_strategy(self, key: str, data: dict) -> None:
        folder = self.folder(key)
        os.makedirs(folder, exist_ok=True)
        _write_json(os.path.join(folder, STRATEGY), data)

    def backups(self, key: str) -> list:
        folder = os.path.join(self.folder(key), BACKUPS)
        try:
            names = [n for n in os.listdir(folder) if BACKUP_RE.match(n)]
        except OSError:
            return []
        out = []
        for name in names:
            try:
                st = os.stat(os.path.join(folder, name))
            except OSError:
                continue
            out.append({"name": name, "size": st.st_size, "mtime": st.st_mtime})
        # names start with a UTC timestamp: newest first by name, then by mtime
        out.sort(key=lambda b: (b["name"], b["mtime"]), reverse=True)
        return out

    def backup_path(self, key: str, name: str) -> str:
        return os.path.join(self.folder(key), BACKUPS, name)

    def _backup_name(self, key: str, device: str, now: float) -> str:
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now))
        base = f"{stamp}-{device}.tar.gz"
        n = 1
        while os.path.exists(self.backup_path(key, base)):
            n += 1
            base = f"{stamp}-{n}-{device}.tar.gz"
        return base

    def add_backup(self, key: str, src: str, device: str, now: float) -> dict:
        """Moves `src` into the backups, then trims the oldest until they fit the limit.
        A file larger than the limit is deleted instead: no backup, never an error."""
        size = os.path.getsize(src)
        if size > self.backup_limit:
            os.remove(src)
            return {"stored": False, "reason": "limit", "size": size}
        folder = os.path.join(self.folder(key), BACKUPS)
        os.makedirs(folder, exist_ok=True)
        name = self._backup_name(key, device, now)
        os.replace(src, os.path.join(folder, name))
        self._trim(key, keep=name)
        return {"stored": True, "name": name, "size": size}

    def _trim(self, key: str, keep: str) -> None:
        total = 0
        for backup in self.backups(key):
            total += backup["size"]
            if total > self.backup_limit and backup["name"] != keep:
                try:
                    os.remove(self.backup_path(key, backup["name"]))
                except OSError:
                    pass

    def partial(self, key: str) -> str:
        folder = self.folder(key)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f".upload-{threading.get_ident()}-{time.time_ns()}.partial")

    def replace_current(self, key: str, upload: str, fields: dict, backup: bool, now: float) -> dict:
        """Called with the key's lock held and the rev already checked."""
        folder = self.folder(key)
        current = os.path.join(folder, CURRENT)
        old = self.meta(key)
        backup_result = None
        if os.path.isfile(current):
            if backup and old:
                moved = os.path.join(folder, f".old-{time.time_ns()}.partial")
                os.replace(current, moved)
                backup_result = self.add_backup(key, moved, str(old.get("device") or "unknown"), now)
        os.replace(upload, current)
        rev = str(int(old["rev"]) + 1) if old and REV_RE.match(str(old.get("rev", ""))) else "1"
        meta = {
            "rev": rev,
            "hash": fields["hash"],
            "newestMtime": fields["newestMtime"],
            "device": fields["device"],
            "uploadedAt": now,
            "size": os.path.getsize(current),
        }
        _write_json(os.path.join(folder, META), meta)
        return {"meta": meta, "backup": backup_result}


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: str, data: dict) -> None:
    tmp = f"{path}.{threading.get_ident()}.partial"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "insertplay-saves"
    sys_version = ""

    def log_message(self, fmt, *args):  # one line per request, never headers (the token)
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- helpers

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, code: int, data) -> None:
        self._send(code, json.dumps(data, sort_keys=True).encode("utf-8"))

    def _error(self, code: int, error: str, **extra) -> None:
        # an upload refused before its body was read: the rest of the body must not be
        # parsed as the next request on this connection
        if self.command in ("PUT", "POST"):
            self.close_connection = True
        self._json(code, {"error": error, **extra})

    def _send_file(self, path: str) -> None:
        try:
            f = open(path, "rb")
        except OSError:
            return self._error(404, "not_found")
        with f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _authorized(self) -> bool:
        sent = self.headers.get("Authorization", "")
        return hmac.compare_digest(sent.encode("utf-8", "replace"), f"Bearer {self.server.token}".encode("utf-8"))

    def _route(self):
        """(key, rest) for /saves/<key>/<rest>, or None. The path must be exactly that:
        no query string, no `.`/`..`, no extra slashes."""
        path = self.path
        if "?" in path or "#" in path or "%" in path or "\\" in path:
            return None
        parts = path.split("/")
        if len(parts) < 4 or parts[0] != "" or parts[1] != "saves" or not KEY_RE.match(parts[2]):
            return None
        rest = "/".join(parts[3:])
        if rest in ("meta", CURRENT, STRATEGY, BACKUPS):
            return parts[2], rest
        if len(parts) == 5 and parts[3] == BACKUPS and BACKUP_RE.match(parts[4]):
            return parts[2], rest
        return None

    def _length(self):
        """Content-Length as an int, or None after answering the error."""
        if self.headers.get("Transfer-Encoding"):
            self._error(411, "length_required")
            return None
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.isdigit():
            self._error(411, "length_required")
            return None
        length = int(raw)
        if length > self.server.store.max_size:
            self._error(413, "too_large", limit=self.server.store.max_size)
            self.close_connection = True
            return None
        return length

    def _receive(self, path: str, length: int) -> bool:
        """Body to `path`. False (file removed) when the client sent less than it announced."""
        left = length
        try:
            with open(path, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            left = -1
        if left != 0:
            try:
                os.remove(path)
            except OSError:
                pass
            self.close_connection = True
            return False
        return True

    def _fields(self):
        """The upload's headers, validated, or None after answering the error."""
        h = self.headers
        digest = (h.get("X-Save-Hash") or "").lower()
        device = h.get("X-Save-Device") or ""
        try:
            newest = float(h.get("X-Save-Newest-Mtime") or "")
        except ValueError:
            newest = -1.0
        if not HASH_RE.match(digest) or not DEVICE_RE.match(device) or not (0 <= newest < 1e12):
            self._error(400, "bad_headers")
            return None
        return {"hash": digest, "device": device, "newestMtime": newest}

    # ---- methods

    def _begin(self):
        if not self._authorized():
            self._error(401, "unauthorized")
            self.close_connection = True
            return None
        route = self._route()
        if route is None:
            self._error(404, "not_found")
            return None
        return route

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        route = self._begin()
        if route is None:
            return
        key, rest = route
        store = self.server.store
        if rest == "meta":
            meta = store.meta(key)
            return self._json(200, meta) if meta else self._error(404, "no_save")
        if rest == CURRENT:
            return self._send_file(os.path.join(store.folder(key), CURRENT))
        if rest == STRATEGY:
            data = store.strategy(key)
            return self._json(200, data) if data else self._error(404, "no_strategy")
        if rest == BACKUPS:
            return self._json(200, {"backups": store.backups(key), "limit": store.backup_limit})
        return self._send_file(store.backup_path(key, rest.split("/", 1)[1]))

    def do_PUT(self):
        route = self._begin()
        if route is None:
            return
        key, rest = route
        if rest == CURRENT:
            return self._put_current(key)
        if rest == STRATEGY:
            return self._put_strategy(key)
        self._error(405, "method_not_allowed")

    def do_POST(self):
        route = self._begin()
        if route is None:
            return
        key, rest = route
        if rest != BACKUPS:
            return self._error(405, "method_not_allowed")
        device = self.headers.get("X-Save-Device") or ""
        if not DEVICE_RE.match(device):
            return self._error(400, "bad_headers")
        length = self._length()
        if length is None:
            return
        store = self.server.store
        upload = store.partial(key)
        if not self._receive(upload, length):
            return self._error(400, "incomplete_body")
        with store.lock(key):
            result = store.add_backup(key, upload, device, time.time())
        self._json(200, result)

    def _put_current(self, key: str) -> None:
        fields = self._fields()
        if fields is None:
            return
        match = (self.headers.get("If-Match") or "").strip().strip('"')
        none_match = (self.headers.get("If-None-Match") or "").strip()
        if not match and none_match != "*":
            return self._error(428, "precondition_required")
        if match and not REV_RE.match(match):
            return self._error(400, "bad_headers")
        backup = self.headers.get("X-Save-Backup", "0") == "1"
        length = self._length()
        if length is None:
            return
        store = self.server.store
        # the body goes to disk before the lock: a slow upload never blocks the other device
        upload = store.partial(key)
        if not self._receive(upload, length):
            return self._error(400, "incomplete_body")
        with store.lock(key):
            current = store.meta(key)
            expected = str(current["rev"]) if current else None
            if (match and match != expected) or (not match and expected is not None):
                os.remove(upload)
                return self._json(409, {"error": "conflict", "meta": current})
            result = store.replace_current(key, upload, fields, backup, time.time())
        self._json(201 if expected is None else 200, result)

    def _put_strategy(self, key: str) -> None:
        length = self._length()
        if length is None:
            return
        if length > STRATEGY_LIMIT:
            self.close_connection = True
            return self._error(413, "too_large", limit=STRATEGY_LIMIT)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._error(400, "bad_json")
        if not isinstance(data, dict) or not isinstance(data.get("strategy"), str) or not data["strategy"]:
            return self._error(400, "bad_strategy")
        store = self.server.store
        with store.lock(key):
            store.put_strategy(key, data)
        self._json(200, data)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, token: str, store: Store):
        super().__init__(address, Handler)
        self.token = token
        self.store = store


def _mb(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(0, int(value * MB))


def main() -> int:
    token = os.environ.get("INSERTPLAY_TOKEN", "")
    if len(token) < 32:
        print("INSERTPLAY_TOKEN missing or shorter than 32 characters", file=sys.stderr)
        return 1
    root = os.environ.get("INSERTPLAY_SAVES_DIR", "/srv/saves")
    if not os.path.isdir(root):
        print(f"saves folder {root} does not exist", file=sys.stderr)
        return 1
    port = int(os.environ.get("INSERTPLAY_SAVES_PORT", "8081"))
    store = Store(root, _mb("INSERTPLAY_SAVE_BACKUP_MB", 50), _mb("INSERTPLAY_SAVE_MAX_MB", 1024))
    server = Server(("", port), token, store)
    print(f"insertplay-saves on :{port}, {root}, backups {store.backup_limit // MB} MB per game", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
