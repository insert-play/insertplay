"""Remote library ("Biblioteca Remota", issue #47): the download engine.

Reads the server's index, downloads one game at a time file by file with resume, checks
every sha256 and installs the game as a "virtual card" in `~/Games/InsertPlay/<id>/`.
The contract (index, file lists, folders, RPCs, error codes) is docs/remote-library.md.

Threading: one worker thread does the downloads. The public methods only read and change
the state under `self._lock` (plus `refresh` and the synchronous checks of `install`, which
main.py runs in `asyncio.to_thread`), so Decky's event loop never waits on the network.

The token never goes into a log line, an exception message or an RPC result.
"""
import base64
import collections
import hashlib
import http.client
import json
import os
import posixpath
import re
import shutil
import ssl
import stat
import tempfile
import threading
import time
import urllib.parse

from .manifest import ARTWORK_KINDS, _get, read_game, resolve_placeholder

SCHEMA_VERSION = 1
ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ZERO_ID = "00000000-0000-0000-0000-000000000000"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9a-f]{1,64}$")
# Token characters allowed in an HTTP header value, without spaces.
TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,4096}$")
KINDS = ("icon", "capsule", "horizontal", "hero", "logo")
ARTWORK_EXTS = ("png", "jpg", "jpeg")

INDEX_LIMIT = 16 * 1024 * 1024
LIST_LIMIT = 64 * 1024 * 1024
SPACE_MARGIN = 256 * 1024 * 1024
TIMEOUT_SECONDS = 30
BACKOFF_SECONDS = (2, 5, 10, 30, 60)
CHUNK = 1024 * 1024
SAVE_EVERY_SECONDS = 2.0
EMIT_EVERY_SECONDS = 1.0
SPEED_WINDOW_SECONDS = 5.0
# cancel waits this long for the worker to let go of the game (a stalled socket read
# takes up to TIMEOUT_SECONDS); unloading the plugin waits less, the thread is a daemon
STOP_WAIT_SECONDS = 45.0
UNLOAD_WAIT_SECONDS = 5.0

STATES = ("queued", "downloading", "paused", "error", "installed")

MESSAGES = {
    "not_configured": "Configure o endereço e o token da Biblioteca Remota.",
    "bad_config": "Endereço ou token inválido.",
    "unauthorized": "O servidor recusou o token.",
    "unreachable": "Não foi possível conectar ao servidor da Biblioteca Remota.",
    "bad_index": "O índice da Biblioteca Remota é inválido.",
    "bad_path": "A lista de arquivos do jogo tem um caminho inválido.",
    "unknown_game": "Jogo não encontrado na Biblioteca Remota.",
    "invalid_state": "Essa ação não vale para o estado atual do jogo.",
    "no_space": "Espaço insuficiente para instalar o jogo.",
    "hash_mismatch": "Um arquivo baixado não confere com o servidor.",
    "server_changed": "Os arquivos do jogo mudaram no servidor. Cancele e instale de novo.",
    "io_error": "Erro ao gravar no disco.",
}


class RemoteError(Exception):
    """`code` and the pt-BR message go to the frontend; `detail` only to the log."""

    def __init__(self, code: str, detail: str = "", **extra):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.extra = code, detail, extra

    def result(self) -> dict:
        message = MESSAGES.get(self.code, self.code)
        if self.code == "no_space" and "needed" in self.extra:
            message = (
                f"Espaço insuficiente: são necessários {_gb(self.extra['needed'])}, "
                f"há {_gb(self.extra.get('free', 0))} livres."
            )
        return {"ok": False, "code": self.code, "message": message, **self.extra}


class _Net(Exception):
    """Network error or 5xx: retried with backoff. `short`: the body ended early."""

    def __init__(self, detail: str, short: bool = False):
        super().__init__(detail)
        self.short = short


class _Stop(Exception):
    """The active game was paused, cancelled or the engine is stopping."""


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB".replace(".", ",")


def free_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


# ---------------------------------------------------------------- files on disk


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data, private: bool = False) -> None:
    """Atomic write (temporary file + rename); `private` makes it 0600."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        os.chmod(tmp, 0o600 if private else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        os.chmod(tmp, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _remove_dir(parent: str, name: str) -> bool:
    """Deletes `parent/name` only when it is a real directory directly inside `parent`,
    without following symlinks. Returns whether something was deleted."""
    if not ID_RE.match(name or ""):
        raise RemoteError("io_error", f"refusing to delete {name!r}")
    path = os.path.join(parent, name)
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(parent):
        raise RemoteError("io_error", f"{path} is not inside {parent}")
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RemoteError("io_error", f"{path} is not a directory")
    shutil.rmtree(path)
    return True


def _url_name(url: str) -> str:
    """File name of a download URL, for the log (the URL never carries the token)."""
    return urllib.parse.unquote(url.rsplit("/", 1)[-1])


def _remove_file(path: str) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        raise RemoteError("io_error", f"{path} is a directory")
    os.unlink(path)


# ---------------------------------------------------------------- validation


def check_id(value) -> str:
    if not isinstance(value, str) or not ID_RE.match(value) or value == ZERO_ID:
        raise RemoteError("unknown_game", f"invalid id {value!r}")
    return value


def _path_ok(path, single: bool = False) -> bool:
    """The path rules of the contract, for one path."""
    if not isinstance(path, str) or not path:
        return False
    try:
        raw = path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(raw) > 4096 or "\\" in path or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        return False
    segments = path.split("/")
    if single and len(segments) != 1:
        return False
    for seg in segments:
        if seg in ("", ".", "..") or len(seg.encode("utf-8")) > 255:
            return False
    return not path.endswith(".part")


def _target_ok(path: str, target) -> bool:
    """A link target: relative, the character rules, and inside the game folder lexically."""
    if not isinstance(target, str) or not target or target.startswith("/"):
        return False
    try:
        raw = target.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(raw) > 4096 or "\\" in target or any(ord(c) < 0x20 or ord(c) == 0x7F for c in target):
        return False
    # empty segments ("a//b", "dir/") are allowed: the contract applies only the character
    # rules to targets, and the indexer keeps such links as they are on disk
    if any(len(seg.encode("utf-8")) > 255 for seg in target.split("/")):
        return False
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    return joined != ".." and not joined.startswith("../") and not joined.startswith("/")


def normalize_url(url) -> str:
    """Base URL as the user typed it, checked and ending in `/`. Raises `bad_config`."""
    if not isinstance(url, str):
        raise RemoteError("bad_config", "url is not a string")
    url = url.strip()
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url) or "\\" in url:
        raise RemoteError("bad_config", "url has spaces or control characters")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RemoteError("bad_config", "url must be http:// or https:// with a host")
    if "@" in parts.netloc or parts.query or parts.fragment or "?" in url or "#" in url:
        raise RemoteError("bad_config", "url has user info, a query or a fragment")
    try:
        parts.port
    except ValueError as exc:
        raise RemoteError("bad_config", "invalid port") from exc
    path = parts.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def resolve_relative(base: str, rel, directory: bool = False) -> str:
    """A relative URL from the index, resolved under the base URL. Anything that could
    send the token elsewhere is `bad_index` for the whole index."""
    if not isinstance(rel, str) or not rel:
        raise RemoteError("bad_index", "missing relative url")
    if any(c in rel for c in "\\?#") or any(ord(c) < 0x20 or ord(c) == 0x7F for c in rel):
        raise RemoteError("bad_index", f"bad relative url {rel!r}")
    parts = urllib.parse.urlsplit(rel)
    if parts.scheme or parts.netloc or rel.startswith("/"):
        raise RemoteError("bad_index", f"absolute url {rel!r}")
    segments = rel.split("/")
    if directory and segments[-1] == "":
        segments = segments[:-1]
    for seg in segments:
        plain = urllib.parse.unquote(seg)
        if seg == "" or plain in (".", "..") or "/" in plain or "\\" in plain:
            raise RemoteError("bad_index", f"bad relative url {rel!r}")
    out = urllib.parse.urljoin(base, rel)
    if directory and not out.endswith("/"):
        out += "/"
    b, o = urllib.parse.urlsplit(base), urllib.parse.urlsplit(out)
    if (o.scheme, o.netloc) != (b.scheme, b.netloc) or not o.path.startswith(b.path) or o.query or o.fragment:
        raise RemoteError("bad_index", f"{rel!r} leaves the server")
    return out


def _manifest_ok(manifest) -> bool:
    if not isinstance(manifest, dict):
        return False
    shortcut = _get(manifest, "shortcut")
    if not isinstance(shortcut, dict) or not str(_get(shortcut, "appName") or "").strip():
        return False
    launch = _get(shortcut, "launch")
    return isinstance(launch, dict) and bool(_get(launch, "exe"))


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_index(raw: bytes, base: str, log=None) -> dict:
    """Validated `index.json`. Bad games are dropped (and logged), a bad URL or schema
    refuses the whole index (`bad_index`)."""
    log = log or (lambda msg: None)
    if len(raw) > INDEX_LIMIT:
        raise RemoteError("bad_index", "index too big")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteError("bad_index", f"index is not JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise RemoteError("bad_index", "unsupported schemaVersion")
    library = resolve_relative(base, data.get("library"), directory=True)
    games = data.get("games")
    if not isinstance(games, list):
        raise RemoteError("bad_index", "games is not a list")
    # URLs first: one bad URL refuses the index, even inside a game that would be dropped
    for game in games:
        if isinstance(game, dict) and "files" in game:
            resolve_relative(base, game.get("files"))
    seen = collections.Counter(g.get("id") for g in games if isinstance(g, dict) and isinstance(g.get("id"), str))
    out = []
    for game in games:
        if not isinstance(game, dict):
            log("index: entry is not an object, dropped")
            continue
        gid = game.get("id")
        if not isinstance(gid, str) or not ID_RE.match(gid) or gid == ZERO_ID:
            log(f"index: invalid id {str(gid)[:80]!r}, dropped")
            continue
        if seen[gid] > 1:
            log(f"index: duplicate id {gid}, dropped")
            continue
        problem = None
        if "files" not in game:
            problem = "files"
        elif not _path_ok(game.get("path"), single=True):
            problem = "path"
        elif not isinstance(game.get("version"), str) or not VERSION_RE.match(game["version"]):
            problem = "version"
        elif not _int(game.get("totalSize")) or not _int(game.get("fileCount")):
            problem = "totalSize/fileCount"
        elif not isinstance(game.get("filesSha256"), str) or not HEX64_RE.match(game["filesSha256"]):
            problem = "filesSha256"
        elif not _manifest_ok(game.get("manifest")):
            problem = "manifest"
        artwork = {}
        art = game.get("artwork")
        if problem is None and art is not None and not isinstance(art, dict):
            problem = "artwork"
        for kind, entry in (art or {}).items() if problem is None else ():
            if kind not in KINDS:
                continue
            if (
                not isinstance(entry, dict)
                or not _path_ok(entry.get("path"))
                or not _int(entry.get("size"))
                or not isinstance(entry.get("sha256"), str)
                or not HEX64_RE.match(entry["sha256"])
            ):
                problem = f"artwork.{kind}"
                break
            artwork[kind] = {"path": entry["path"], "size": entry["size"], "sha256": entry["sha256"]}
        if problem:
            log(f"index: game {gid} has an invalid {problem}, dropped")
            continue
        name = game.get("name")
        manifest = game["manifest"]
        if not isinstance(name, str) or not name.strip():
            name = str(_get(_get(manifest, "shortcut"), "appName")).strip()
        out.append(
            {
                "id": gid,
                "version": game["version"],
                "path": game["path"],
                "name": name,
                "totalSize": game["totalSize"],
                "fileCount": game["fileCount"],
                "filesUrl": resolve_relative(base, game["files"]),
                "filesSha256": game["filesSha256"],
                "libraryUrl": library,
                "manifest": manifest,
                "artwork": artwork,
            }
        )
    generated = data.get("generatedAt")
    return {"generatedAt": generated if isinstance(generated, str) else None, "games": out}


def parse_file_list(raw: bytes, game: dict) -> dict:
    """Validated file list of `game` (an index entry). `bad_index` for the format,
    `bad_path` when any path breaks the path rules."""
    if len(raw) > LIST_LIMIT:
        raise RemoteError("bad_index", "file list too big")
    if hashlib.sha256(raw).hexdigest() != game["filesSha256"]:
        raise RemoteError("bad_index", "filesSha256 mismatch")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteError("bad_index", f"file list is not JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise RemoteError("bad_index", "unsupported file list schemaVersion")
    if data.get("id") != game["id"] or data.get("version") != game["version"]:
        raise RemoteError("bad_index", "file list id/version differ from the index")
    files, dirs, links = data.get("files"), data.get("dirs", []), data.get("links", [])
    if not isinstance(files, list) or not isinstance(dirs, list) or not isinstance(links, list):
        raise RemoteError("bad_index", "files/dirs/links are not lists")
    out_files = []
    for f in files:
        if (
            not isinstance(f, dict)
            or not _int(f.get("size"))
            or not isinstance(f.get("sha256"), str)
            or not HEX64_RE.match(f["sha256"])
            or not isinstance(f.get("executable"), bool)
        ):
            raise RemoteError("bad_index", "invalid file entry")
        if not _path_ok(f.get("path")):
            raise RemoteError("bad_path", f"file path {str(f.get('path'))[:200]!r}")
        out_files.append({"path": f["path"], "size": f["size"], "sha256": f["sha256"], "executable": f["executable"]})
    for d in dirs:
        if not _path_ok(d):
            raise RemoteError("bad_path", f"dir path {str(d)[:200]!r}")
    out_links = []
    for link in links:
        if not isinstance(link, dict):
            raise RemoteError("bad_index", "invalid link entry")
        if not _path_ok(link.get("path")) or not _target_ok(link["path"], link.get("target")):
            raise RemoteError("bad_path", f"link {str(link.get('path'))[:200]!r}")
        out_links.append({"path": link["path"], "target": link["target"]})
    leaves = [f["path"] for f in out_files] + [l["path"] for l in out_links]
    every = leaves + list(dirs)
    if len(set(every)) != len(every):
        raise RemoteError("bad_path", "duplicate path")
    leaf_set = set(leaves)
    for path in every:
        parts = path.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in leaf_set:
                raise RemoteError("bad_path", f"{path!r} is under a file or link")
    if sum(f["size"] for f in out_files) != game["totalSize"] or len(out_files) != game["fileCount"]:
        raise RemoteError("bad_index", "file list totals differ from the index")
    return {"id": game["id"], "version": game["version"], "files": out_files, "dirs": list(dirs), "links": out_links}


# ---------------------------------------------------------------- config


class Config:
    """Server URL and token in the settings folder, mode 0600."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        data = _read_json(self.path)
        data = data if isinstance(data, dict) else {}
        url, token = data.get("url"), data.get("token")
        try:
            url = normalize_url(url) if url else None
        except RemoteError:
            url = None
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            token = None
        return {"url": url, "token": token}

    def public(self) -> dict:
        cfg = self.load()
        return {"url": cfg["url"], "tokenSet": bool(cfg["token"])}

    def set(self, url, token) -> dict:
        """`url` "" clears; `token` "" clears, None keeps."""
        cfg = self.load()
        if url is None:
            new_url = cfg["url"]
        elif url == "":
            new_url = None
        else:
            new_url = normalize_url(url)
        if token is None:
            new_token = cfg["token"]
        elif token == "":
            new_token = None
        elif not isinstance(token, str) or not TOKEN_RE.match(token):
            raise RemoteError("bad_config", "token has characters not allowed in a header")
        else:
            new_token = token
        _write_json(self.path, {"url": new_url, "token": new_token}, private=True)
        return {"url": new_url, "tokenSet": bool(new_token)}


# ---------------------------------------------------------------- HTTP


class _Http:
    """One persistent connection (keep-alive) to the server: a game with ~30 000 files
    makes one request per file. Never follows redirects; only talks to the base URL's
    scheme, host and port. Not thread-safe: one per thread."""

    def __init__(self, base: str, token: str, timeout: float = TIMEOUT_SECONDS):
        parts = urllib.parse.urlsplit(base)
        self.scheme, self.netloc = parts.scheme, parts.netloc
        self.host, self.port = parts.hostname, parts.port
        self.token, self.timeout = token, timeout
        self.conn = None

    def _connection(self):
        if self.conn is None:
            if self.scheme == "https":
                self.conn = http.client.HTTPSConnection(
                    self.host, self.port, timeout=self.timeout, context=ssl.create_default_context()
                )
            else:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def get(self, url: str, headers: dict = None):
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme, parts.netloc) != (self.scheme, self.netloc):
            raise RemoteError("bad_index", "url outside the server")
        path = parts.path or "/"
        hdrs = {"Authorization": "Bearer " + self.token, "Accept-Encoding": "identity", "User-Agent": "InsertPlay"}
        hdrs.update(headers or {})
        for attempt in (0, 1):
            reused = self.conn is not None
            conn = self._connection()
            try:
                conn.request("GET", path, headers=hdrs)
                return conn.getresponse()
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError) as exc:
                self.close()
                # an idle keep-alive connection the server closed: one immediate retry
                if reused and attempt == 0:
                    continue
                raise _Net(type(exc).__name__) from None
            except (OSError, http.client.HTTPException) as exc:
                self.close()
                raise _Net(type(exc).__name__) from None
        raise _Net("connection failed")

    def discard(self, resp) -> None:
        """Drops an unwanted body; a big one costs the connection, not the time to read it."""
        try:
            resp.read(65536)
        except Exception:
            pass
        if not resp.isclosed():
            self.close()

    def open(self, url: str, headers: dict = None, client_error: str = "server_changed"):
        """GET answering 200/206; maps the other statuses to the contract's outcomes."""
        resp = self.get(url, headers)
        status = resp.status
        if status in (200, 206):
            return resp
        self.discard(resp)
        if status == 401:
            raise RemoteError("unauthorized", "server answered 401")
        if status >= 500:
            raise _Net(f"HTTP {status}")
        if 300 <= status < 400:
            raise RemoteError("server_changed", f"HTTP {status} (redirects are not followed)")
        raise RemoteError(client_error, f"HTTP {status}")

    def get_bytes(self, url: str, limit: int, client_error: str) -> bytes:
        resp = self.open(url, client_error=client_error)
        if resp.status != 200:
            self.discard(resp)
            raise RemoteError(client_error, f"HTTP {resp.status}")
        length = resp.getheader("Content-Length")
        if length is not None and length.isdigit() and int(length) > limit:
            self.discard(resp)
            raise RemoteError("bad_index", "response too big")
        try:
            body = resp.read(limit + 1)
        except (OSError, http.client.HTTPException) as exc:
            self.close()
            raise _Net(type(exc).__name__, short=True) from None
        if len(body) > limit:
            self.close()
            raise RemoteError("bad_index", "response too big")
        if not resp.isclosed():
            self.close()
        return body


# ---------------------------------------------------------------- engine


class RemoteLibrary:
    """Index cache, state, queue and the worker thread.

    `games_root` is `~/Games/InsertPlay`, `data_dir` is `<runtime>/library` and
    `config_path` is `<settings>/remote_library.json`. `log(msg)` and `on_change(progress)`
    may be called from the worker thread."""

    def __init__(self, games_root: str, data_dir: str, config_path: str, log=None, on_change=None):
        self.games_root = games_root
        self.incoming_root = os.path.join(games_root, ".incoming")
        self.data_dir = data_dir
        self.state_path = os.path.join(data_dir, "state.json")
        self.index_path = os.path.join(data_dir, "index.json")
        self.lists_dir = os.path.join(data_dir, "lists")
        self.artwork_dir = os.path.join(data_dir, "artwork")
        self.config = Config(config_path)
        self.log = log or (lambda msg: None)
        self.on_change = on_change or (lambda progress: None)
        self.backoff = BACKOFF_SECONDS
        self.timeout = TIMEOUT_SECONDS
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread = None
        self._loaded = False  # stop() saves the state only after start() loaded it
        self._games = {}
        self._queue = []
        self._active = None
        self._progress = {}  # id -> {"samples": deque[(t, bytes)]} for the active game
        self._index = {"games": [], "generatedAt": None, "fetchedAt": None, "stale": False, "baseUrl": None}
        self._last_save = 0.0
        self._last_emit = 0.0

    # ---- lifecycle

    def start(self) -> None:
        """Loads the state and starts the worker. `downloading` and `queued` games resume."""
        with self._lock:
            self._load()
            self._loaded = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="insertplay-remote", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread:
            self._thread.join(UNLOAD_WAIT_SECONDS)
        with self._lock:
            # an unload before start() (or a failed load) must not overwrite the saved state
            # with an empty one: installed games would lose their records
            if self._loaded:
                self._save(force=True)

    def _load(self) -> None:
        cached = _read_json(self.index_path)
        if isinstance(cached, dict) and isinstance(cached.get("games"), list):
            self._index = {
                "games": [g for g in cached["games"] if isinstance(g, dict) and ID_RE.match(str(g.get("id")))],
                "generatedAt": cached.get("generatedAt"),
                "fetchedAt": cached.get("fetchedAt"),
                "stale": bool(cached.get("stale")),
                "baseUrl": cached.get("baseUrl"),
            }
        data = _read_json(self.state_path)
        data = data if isinstance(data, dict) else {}
        games = data.get("games") if isinstance(data.get("games"), dict) else {}
        self._games = {}
        for gid, entry in games.items():
            if not isinstance(entry, dict) or not ID_RE.match(gid) or gid == ZERO_ID or entry.get("state") not in STATES:
                continue
            self._games[gid] = entry
        resumed = [g for g, e in self._games.items() if e["state"] == "downloading"]
        saved_queue = data.get("queue") if isinstance(data.get("queue"), list) else []
        saved_queue = [g for g in saved_queue if isinstance(g, str)]
        queue = resumed + [g for g in saved_queue if g in self._games and self._games[g]["state"] == "queued"]
        queue += [g for g, e in self._games.items() if e["state"] == "queued" and g not in queue]
        for gid in resumed:
            self._games[gid]["state"] = "queued"
        self._queue = list(dict.fromkeys(queue))
        if resumed or self._queue:
            self.log(f"resuming {len(self._queue)} queued download(s)")

    def _save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_save < SAVE_EVERY_SECONDS:
            return
        self._last_save = now
        _write_json(self.state_path, {"schemaVersion": 1, "queue": list(self._queue), "games": self._games})

    def _save_index(self) -> None:
        _write_json(self.index_path, self._index)

    def _notify(self, force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < EMIT_EVERY_SECONDS:
            return
        self._last_emit = now
        try:
            self.on_change(self.progress())
        except Exception:
            pass

    # ---- status

    def _index_game(self, gid: str):
        for game in self._index["games"]:
            if game.get("id") == gid:
                return game
        return None

    def install_path(self, gid: str) -> str:
        return os.path.join(self.games_root, gid)

    def _cached_artwork(self, gid: str) -> dict:
        folder = os.path.join(self.artwork_dir, gid)
        out = {}
        try:
            names = os.listdir(folder)
        except OSError:
            return out
        for name in sorted(names):
            kind, _, ext = name.rpartition(".")
            if kind in KINDS and ext in ARTWORK_EXTS and kind not in out:
                path = os.path.join(folder, name)
                if stat.S_ISREG(os.lstat(path).st_mode):
                    out[kind] = path
        return out

    def _shortcut(self, gid: str, manifest: dict) -> dict:
        """Same shape as `manifest.load_card`, with `{SD_ROOT}` = the install folder."""
        root = self.install_path(gid)
        shortcut = _get(manifest, "shortcut") or {}
        launch = _get(shortcut, "launch") or {}
        args = resolve_placeholder(str(_get(launch, "args") or ""), root).strip()
        art = self._cached_artwork(gid)
        return {
            "guid": gid,
            "appName": str(_get(shortcut, "appName") or "").strip(),
            "exe": resolve_placeholder(str(_get(launch, "exe") or ""), root),
            "startDir": resolve_placeholder(str(_get(launch, "startDir") or root), root),
            "launchOptions": f"{args} --guid {gid}" if args else f"--guid {gid}",
            "artwork": {k: art[k] for k in ARTWORK_KINDS if k in art},
            "icon": art.get("icon"),
            "game": read_game(manifest),
        }

    def _status(self, gid: str) -> dict:
        idx = self._index_game(gid)
        entry = self._games.get(gid)
        info = idx or entry or {}
        state = entry["state"] if entry else "not_installed"
        total = (entry or {}).get("totalSize", info.get("totalSize", 0)) or 0
        done = (entry or {}).get("bytesDone", 0) or 0
        files_done = (entry or {}).get("filesDone", 0) or 0
        if state == "installed":
            done, files_done = total, info.get("fileCount", 0)
        if state == "installed":
            percent = 100
        elif state == "not_installed" or not total:
            percent = 0
        else:
            percent = min(99, (100 * done) // total)
        speed = 0
        prog = self._progress.get(gid)
        if state == "downloading" and prog and len(prog["samples"]) >= 2:
            (t0, b0), (t1, b1) = prog["samples"][0], prog["samples"][-1]
            if t1 > t0:
                speed = int((b1 - b0) / (t1 - t0))
        installed_version = (entry or {}).get("installedVersion") if state == "installed" else None
        return {
            "id": gid,
            "name": info.get("name") or "",
            "state": state,
            "percent": percent,
            "bytesDone": done,
            "bytesTotal": total,
            "speed": max(0, speed),
            "filesDone": files_done,
            "fileCount": (entry or {}).get("fileCount", info.get("fileCount", 0)),
            "error": (entry or {}).get("error") if state == "error" else None,
            "version": (idx or entry or {}).get("version"),
            "installedVersion": installed_version,
            "updateAvailable": bool(state == "installed" and idx and idx["version"] != installed_version),
            "inIndex": idx is not None,
            "installPath": self.install_path(gid),
            "shortcut": self._shortcut(gid, info.get("manifest") or {}),
        }

    def progress(self) -> dict:
        with self._lock:
            ids = [g for g, e in self._games.items() if e["state"] in ("queued", "downloading", "paused", "error")]
            return {"active": self._active, "queue": list(self._queue), "games": [self._status(g) for g in ids]}

    def games(self) -> dict:
        with self._lock:
            ids = [g["id"] for g in self._index["games"]]
            # state games that left the index (installed or not) stay listed with inIndex false
            ids += [g for g in self._games if g not in ids]
            return {
                "ok": True,
                "games": [self._status(g) for g in ids],
                "stale": bool(self._index.get("stale")),
                "fetchedAt": self._index.get("fetchedAt"),
            }

    # ---- config and refresh

    def get_config(self) -> dict:
        return self.config.public()

    def set_config(self, url, token) -> dict:
        try:
            before = self.config.load()["url"]
            out = self.config.set(url, token)
        except RemoteError as exc:
            return exc.result()
        if out["url"] != before:
            with self._lock:
                # games of another server must not be installable with this one's token
                self._index = {"games": [], "generatedAt": None, "fetchedAt": None, "stale": False, "baseUrl": out["url"]}
                self._save_index()
            self.log(f"server set to {out['url']}")
        return {"ok": True, **out}

    def refresh(self) -> dict:
        """Fetches the index and the artwork. On error the cached index stays."""
        cfg = self.config.load()
        if not cfg["url"] or not cfg["token"]:
            return RemoteError("not_configured").result()
        client = _Http(cfg["url"], cfg["token"], self.timeout)
        try:
            try:
                raw = client.get_bytes(urllib.parse.urljoin(cfg["url"], "index.json"), INDEX_LIMIT, "bad_index")
            except _Net as exc:
                raise RemoteError("unreachable", str(exc)) from None
            parsed = parse_index(raw, cfg["url"], self.log)
            with self._lock:
                self._index = {
                    "games": parsed["games"],
                    "generatedAt": parsed["generatedAt"],
                    "fetchedAt": int(time.time()),
                    "stale": False,
                    "baseUrl": cfg["url"],
                }
                self._save_index()
            self._refresh_artwork(client, parsed["games"])
        except RemoteError as exc:
            with self._lock:
                self._index["stale"] = True
                self._save_index()
            self.log(f"refresh failed: {exc}")
            return exc.result()
        finally:
            client.close()
        result = self.games()
        self.log(f"index: {len(parsed['games'])} game(s)")
        return {"ok": True, "games": result["games"], "generatedAt": parsed["generatedAt"]}

    def _refresh_artwork(self, client: _Http, games: list) -> None:
        for game in games:
            folder = os.path.join(self.artwork_dir, game["id"])
            for kind in KINDS:
                entry = game["artwork"].get(kind)
                ext = entry["path"].rpartition(".")[2].lower() if entry else ""
                keep = f"{kind}.{ext}" if entry and ext in ARTWORK_EXTS else None
                for other in ARTWORK_EXTS:
                    name = f"{kind}.{other}"
                    if name != keep:
                        _remove_file(os.path.join(folder, name))
                if not keep:
                    continue
                target = os.path.join(folder, keep)
                try:
                    if os.path.isfile(target) and not os.path.islink(target) and _sha256_file(target) == entry["sha256"]:
                        continue
                    url = game["libraryUrl"] + urllib.parse.quote(game["path"], safe="") + "/"
                    url += urllib.parse.quote(entry["path"], safe="/")
                    data = client.get_bytes(url, entry["size"], "server_changed")
                    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                        raise RemoteError("hash_mismatch", f"artwork {kind}")
                    _write_bytes(target, data)
                except RemoteError as exc:
                    if exc.code == "unauthorized":
                        raise
                    self.log(f"artwork {game['id']} {kind}: {exc}")
                except _Net as exc:
                    self.log(f"artwork {game['id']} {kind}: {exc}")
        keep_ids = {g["id"] for g in games} | set(self._games)
        try:
            names = os.listdir(self.artwork_dir)
        except OSError:
            names = []
        for name in names:
            if ID_RE.match(name) and name not in keep_ids:
                _remove_dir(self.artwork_dir, name)

    def artwork_base64(self, gid, kind) -> str:
        try:
            check_id(gid)
        except RemoteError:
            return ""
        if kind not in KINDS:
            return ""
        path = self._cached_artwork(gid).get(kind)
        if not path:
            return ""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    # ---- actions

    def _verified_bytes(self, gid: str) -> int:
        entry = self._games.get(gid) or {}
        return int(entry.get("bytesVerified", 0) or 0)

    def _space_check(self, total: int, verified: int) -> None:
        os.makedirs(self.games_root, exist_ok=True)
        free = free_bytes(self.games_root)
        needed = max(0, total - verified) + SPACE_MARGIN
        if free < needed:
            raise RemoteError("no_space", f"needed {needed}, free {free}", needed=needed, free=free)

    def install(self, gid) -> dict:
        try:
            gid = check_id(gid)
            cfg = self.config.load()
            if not cfg["url"] or not cfg["token"]:
                raise RemoteError("not_configured")
            with self._lock:
                entry = self._games.get(gid)
                state = entry["state"] if entry else "not_installed"
                if state in ("queued", "downloading"):
                    return {"ok": True, "game": self._status(gid)}
                if state == "installed":
                    raise RemoteError("invalid_state", "already installed")
                idx = self._index_game(gid)
                if state == "not_installed" and idx is None:
                    raise RemoteError("unknown_game", gid)
                total = (entry or idx)["totalSize"]
                verified = self._verified_bytes(gid)
            if state == "not_installed" and os.path.lexists(self.install_path(gid)):
                raise RemoteError("io_error", f"{self.install_path(gid)} exists and was not installed by the plugin")
            self._space_check(total, verified)
            with self._lock:
                entry = self._games.get(gid)
                if entry is None:
                    if idx is None:  # cancelled meanwhile, and no longer in the index
                        raise RemoteError("unknown_game", gid)
                    if self._active == gid:
                        raise RemoteError("invalid_state", "the previous download is still stopping")
                    entry = {k: idx[k] for k in ("version", "path", "name", "totalSize", "fileCount", "filesUrl",
                                                  "filesSha256", "libraryUrl", "manifest")}
                    entry.update({"bytesDone": 0, "bytesVerified": 0, "filesDone": 0})
                    # leftovers of an install the state no longer knows (state lost, a cancel
                    # that timed out, maybe another version): a final name there would count as
                    # verified, so the first scan hashes those files instead (.part files are
                    # hashed anyway when they complete)
                    if os.path.lexists(os.path.join(self.incoming_root, gid)):
                        entry["rehash"] = True
                        _remove_file(os.path.join(self.lists_dir, f"{gid}.json"))
                    self._games[gid] = entry
                elif entry["state"] not in ("paused", "error"):
                    return {"ok": True, "game": self._status(gid)}
                entry["state"] = "queued"
                entry["error"] = None
                if gid not in self._queue:
                    self._queue.append(gid)
                self._save(force=True)
                self._cv.notify_all()
                status = self._status(gid)
            self.log(f"{gid} ({status['name']}): queued")
            self._notify()
            return {"ok": True, "game": status}
        except RemoteError as exc:
            return exc.result()

    def pause(self, gid) -> dict:
        try:
            gid = check_id(gid)
            with self._lock:
                entry = self._games.get(gid)
                if not entry or entry["state"] not in ("queued", "downloading"):
                    raise RemoteError("invalid_state", "not downloading")
                entry["state"] = "paused"
                if gid in self._queue:
                    self._queue.remove(gid)
                self._save(force=True)
                status = self._status(gid)
            self.log(f"{gid}: paused")
            self._notify()
            return {"ok": True, "game": status}
        except RemoteError as exc:
            return exc.result()

    def resume(self, gid) -> dict:
        try:
            gid = check_id(gid)
            with self._lock:
                entry = self._games.get(gid)
                if not entry or entry["state"] not in ("paused", "error"):
                    raise RemoteError("invalid_state", "not paused")
                entry["state"] = "queued"
                entry["error"] = None
                if gid not in self._queue:
                    self._queue.append(gid)
                self._save(force=True)
                self._cv.notify_all()
                status = self._status(gid)
            self.log(f"{gid}: resumed")
            self._notify()
            return {"ok": True, "game": status}
        except RemoteError as exc:
            return exc.result()

    def _wait_inactive(self, gid: str) -> None:
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while self._active == gid:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RemoteError("io_error", "download did not stop in time")
            self._cv.wait(min(left, 0.5))

    def cancel(self, gid) -> dict:
        try:
            gid = check_id(gid)
            with self._lock:
                entry = self._games.get(gid)
                if not entry or entry["state"] == "installed":
                    raise RemoteError("invalid_state", "nothing to cancel")
                del self._games[gid]
                if gid in self._queue:
                    self._queue.remove(gid)
                self._save(force=True)
                self._cv.notify_all()
                self._wait_inactive(gid)
            _remove_dir(self.incoming_root, gid)
            _remove_file(os.path.join(self.lists_dir, f"{gid}.json"))
            with self._lock:
                status = self._status(gid)
            self.log(f"{gid}: cancelled")
            self._notify()
            return {"ok": True, "game": status}
        except RemoteError as exc:
            return exc.result()

    def uninstall(self, gid) -> dict:
        try:
            gid = check_id(gid)
            with self._lock:
                entry = self._games.get(gid)
                if not entry or entry["state"] != "installed":
                    raise RemoteError("unknown_game", "not installed")
            _remove_dir(self.games_root, gid)
            _remove_file(os.path.join(self.lists_dir, f"{gid}.json"))
            with self._lock:
                self._games.pop(gid, None)
                self._save(force=True)
                in_index = self._index_game(gid) is not None
                status = self._status(gid)
            if not in_index:
                _remove_dir(self.artwork_dir, gid)
            self.log(f"{gid} ({entry.get('name')}): uninstalled")
            self._notify()
            return {"ok": True, "game": status}
        except RemoteError as exc:
            return exc.result()

    # ---- worker

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                gid = None
                while self._queue:
                    candidate = self._queue.pop(0)
                    if self._games.get(candidate, {}).get("state") == "queued":
                        gid = candidate
                        break
                if gid is None:
                    self._cv.wait(1.0)
                    continue
                entry = self._games[gid]
                entry["state"] = "downloading"
                self._active = gid
                self._progress[gid] = {"samples": collections.deque()}
                self._save(force=True)
            self._notify()
            try:
                self._install(gid, entry)
            except _Stop:
                pass
            except Exception as exc:  # RemoteError or a disk error
                err = exc if isinstance(exc, RemoteError) else RemoteError("io_error", f"{type(exc).__name__}: {exc}")
                with self._lock:
                    if self._games.get(gid) is entry and entry["state"] == "downloading":
                        result = err.result()
                        entry["state"] = "error"
                        entry["error"] = {"code": result["code"], "message": result["message"]}
                self.log(f"{gid}: error {err}")
            finally:
                with self._cv:
                    self._active = None
                    self._progress.pop(gid, None)
                    self._save(force=True)
                    self._cv.notify_all()
                self._notify()

    def _check(self, gid: str, entry: dict) -> None:
        with self._lock:
            if self._stop.is_set() or self._games.get(gid) is not entry or entry["state"] != "downloading":
                raise _Stop()

    def _sleep(self, seconds: float, gid: str, entry: dict) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self._check(gid, entry)
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self._stop.wait(min(left, 0.25))

    def _add_progress(self, gid: str, entry: dict, delta: int) -> None:
        with self._lock:
            if self._games.get(gid) is not entry:
                return
            entry["bytesDone"] = max(0, entry.get("bytesDone", 0) + delta)
            prog = self._progress.get(gid)
            if prog is not None:
                now = time.monotonic()
                prog["samples"].append((now, entry["bytesDone"]))
                while len(prog["samples"]) > 2 and now - prog["samples"][0][0] > SPEED_WINDOW_SECONDS:
                    prog["samples"].popleft()
            self._save()
        self._notify(force=False)

    def _retrying(self, gid: str, entry: dict, fn):
        attempt = 0
        while True:
            self._check(gid, entry)
            try:
                return fn()
            except _Net as exc:
                if attempt >= len(self.backoff):
                    raise RemoteError("unreachable", str(exc)) from None
                self.log(f"{gid}: {exc}, retry {attempt + 1} in {self.backoff[attempt]} s")
                self._sleep(self.backoff[attempt], gid, entry)
                attempt += 1

    def _file_list(self, client: _Http, gid: str, entry: dict) -> dict:
        pinned = os.path.join(self.lists_dir, f"{gid}.json")
        data = _read_json(pinned)
        if isinstance(data, dict) and data.get("id") == gid and data.get("version") == entry["version"]:
            return data
        raw = self._retrying(gid, entry, lambda: client.get_bytes(entry["filesUrl"], LIST_LIMIT, "server_changed"))
        parsed = parse_file_list(raw, {"id": gid, **entry})
        _write_json(pinned, parsed)
        return parsed

    def _ensure_dir(self, base: str, rel: str, known: set) -> None:
        """Creates `base/rel` one segment at a time, refusing any segment that exists and
        is not a real directory (a symlink, for example): nothing is written through a link."""
        if not rel or rel in known:
            return
        current = ""
        for seg in rel.split("/"):
            current = f"{current}/{seg}" if current else seg
            if current in known:
                continue
            path = os.path.join(base, current)
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                os.mkdir(path, 0o755)
                os.chmod(path, 0o755)
            else:
                if not stat.S_ISDIR(st.st_mode):
                    raise RemoteError("bad_path", f"{current} exists and is not a directory")
            known.add(current)

    def _game_folder(self, gid: str) -> str:
        """`.incoming/<id>/`, created with the checks: neither may be a symlink."""
        os.makedirs(self.games_root, exist_ok=True)
        root_real = os.path.realpath(self.games_root)
        for path in (self.incoming_root, os.path.join(self.incoming_root, gid)):
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                os.mkdir(path, 0o755)
                continue
            if not stat.S_ISDIR(st.st_mode):
                raise RemoteError("io_error", f"{path} is not a directory")
        folder = os.path.join(self.incoming_root, gid)
        if os.path.realpath(folder) != os.path.join(root_real, ".incoming", gid):
            raise RemoteError("io_error", f"{folder} resolves outside {self.games_root}")
        return folder

    def _install(self, gid: str, entry: dict) -> None:
        cfg = self.config.load()
        if not cfg["url"] or not cfg["token"]:
            raise RemoteError("not_configured")
        started = time.monotonic()
        client = _Http(cfg["url"], cfg["token"], self.timeout)
        try:
            listing = self._file_list(client, gid, entry)
            target = self.install_path(gid)
            if os.path.lexists(target):
                raise RemoteError("io_error", f"{target} exists and was not installed by the plugin")
            folder = self._game_folder(gid)
            files = listing["files"]

            # what an earlier run left: a final name exists only after its sha256 matched
            # (unless the folder predates the state: then `rehash` checks every final name)
            rehash = bool(entry.get("rehash"))
            verified, done, files_done = set(), 0, 0
            for f in files:
                final = os.path.join(folder, f["path"])
                try:
                    st = os.lstat(final)
                    if stat.S_ISREG(st.st_mode) and st.st_size == f["size"]:
                        if rehash:
                            self._check(gid, entry)
                        if not rehash or _sha256_file(final) == f["sha256"]:
                            verified.add(f["path"])
                            done += f["size"]
                            files_done += 1
                            continue
                    if rehash and not stat.S_ISDIR(st.st_mode):
                        os.unlink(final)
                except OSError:
                    pass
                try:
                    st = os.lstat(final + ".part")
                    if stat.S_ISREG(st.st_mode):
                        done += min(st.st_size, f["size"])
                except OSError:
                    pass
            verified_bytes = sum(f["size"] for f in files if f["path"] in verified)
            with self._lock:
                self._check(gid, entry)
                entry.update({"bytesDone": done, "bytesVerified": verified_bytes, "filesDone": files_done})
                entry.pop("rehash", None)
                entry["fileCount"], entry["totalSize"] = len(files), sum(f["size"] for f in files)
                self._save(force=True)
            self._space_check(entry["totalSize"], verified_bytes)
            self.log(f"{gid} ({entry.get('name')}): downloading {len(files) - len(verified)} of {len(files)} file(s)")

            known_dirs = set()
            base_url = entry["libraryUrl"] + urllib.parse.quote(entry["path"], safe="") + "/"
            for f in files:
                if f["path"] in verified:
                    continue
                self._check(gid, entry)
                self._download(client, base_url, folder, f, gid, entry, known_dirs)
                with self._lock:
                    if self._games.get(gid) is entry:
                        entry["filesDone"] = entry.get("filesDone", 0) + 1
                        entry["bytesVerified"] = entry.get("bytesVerified", 0) + f["size"]
                        self._save()

            self._check(gid, entry)
            for d in listing.get("dirs", []):
                self._ensure_dir(folder, d, known_dirs)
            self._make_links(folder, listing.get("links", []), known_dirs)

            with self._lock:
                self._check(gid, entry)
                if os.path.lexists(target):
                    raise RemoteError("io_error", f"{target} exists and was not installed by the plugin")
                os.rename(folder, target)
                entry.update(
                    {
                        "state": "installed",
                        "installedVersion": entry["version"],
                        "installedAt": int(time.time()),
                        "bytesDone": entry["totalSize"],
                        "filesDone": len(files),
                        "error": None,
                    }
                )
                self._save(force=True)
            seconds = max(time.monotonic() - started, 0.001)
            self.log(
                f"{gid} ({entry.get('name')}): installed, {entry['totalSize'] / 1e6:.0f} MB in {seconds:.0f} s"
            )
        finally:
            client.close()

    def _make_links(self, folder: str, links: list, known_dirs: set) -> None:
        for link in links:
            self._ensure_dir(folder, posixpath.dirname(link["path"]), known_dirs)
            path = os.path.join(folder, link["path"])
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISLNK(st.st_mode):
                    raise RemoteError("bad_path", f"{link['path']} exists and is not a link")
                os.unlink(path)
            os.symlink(link["target"], path)
        real = os.path.realpath(folder)
        for link in links:
            resolved = os.path.realpath(os.path.join(folder, link["path"]))
            if resolved != real and not resolved.startswith(real + os.sep):
                raise RemoteError("bad_path", f"link {link['path']} resolves outside the game folder")

    def _download(self, client, base_url, folder, f, gid, entry, known_dirs) -> None:
        self._ensure_dir(folder, posixpath.dirname(f["path"]), known_dirs)
        final = os.path.join(folder, f["path"])
        part = final + ".part"
        url = base_url + urllib.parse.quote(f["path"], safe="/")
        mode = 0o755 if f["executable"] else 0o644
        for attempt in (1, 2):
            digest = self._fetch(client, url, part, f["size"], mode, gid, entry)
            if digest == f["sha256"]:
                os.rename(part, final)
                return
            os.unlink(part)
            self._add_progress(gid, entry, -f["size"])
            self.log(f"{gid}: sha256 mismatch on {f['path']} (attempt {attempt})")
        raise RemoteError("hash_mismatch", f["path"])

    def _fetch(self, client, url, part, size, mode, gid, entry) -> str:
        """Downloads `part` up to `size` bytes, resuming what is there. Returns the sha256."""
        try:
            st = os.lstat(part)
            if not stat.S_ISREG(st.st_mode):
                _remove_file(part)
        except FileNotFoundError:
            pass
        fd = os.open(part, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o644)
        with os.fdopen(fd, "r+b") as fh:
            have = os.fstat(fd).st_size
            if have > size:
                fh.truncate(0)
                self._add_progress(gid, entry, -size)
                have = 0
            cur = {"have": have, "hash": hashlib.sha256()}
            left = have
            while left:
                block = fh.read(min(CHUNK, left))
                if not block:
                    break
                cur["hash"].update(block)
                left -= len(block)
            attempt = 0
            while cur["have"] < size:
                self._check(gid, entry)
                before = cur["have"]
                try:
                    self._fetch_once(client, url, fh, size, cur, gid, entry)
                except _Net as exc:
                    if cur["have"] > before:
                        attempt = 0
                    if attempt >= len(self.backoff):
                        code = "server_changed" if exc.short else "unreachable"
                        raise RemoteError(code, str(exc)) from None
                    # interruptions are rare: the log shows where the next request resumes
                    self.log(
                        f"{gid}: {exc} on {_url_name(url)} at byte {cur['have']}, "
                        f"retry {attempt + 1} in {self.backoff[attempt]} s"
                    )
                    self._sleep(self.backoff[attempt], gid, entry)
                    attempt += 1
            fh.flush()
            os.fchmod(fd, mode)
        return cur["hash"].hexdigest()

    def _fetch_once(self, client, url, fh, size, cur, gid, entry) -> None:
        """One request. `cur` (bytes in the `.part` and their running sha256) stays right
        when the connection drops halfway."""
        have = cur["have"]
        headers = {"Range": f"bytes={have}-"} if have else {}
        resp = client.open(url, headers)
        if resp.status == 206:
            expected = f"bytes {have}-{size - 1}/{size}"
            if not have or (resp.getheader("Content-Range") or "").strip() != expected:
                client.discard(resp)
                raise RemoteError("server_changed", "unexpected Content-Range")
            self.log(f"{gid}: resuming {_url_name(url)} at byte {have} of {size} (206)")
        elif have:
            # 200 to a Range request: the server sends the whole file, start over
            fh.seek(0)
            fh.truncate(0)
            self._add_progress(gid, entry, -have)
            cur["have"], cur["hash"] = 0, hashlib.sha256()
        remaining = size - cur["have"]
        length = resp.getheader("Content-Length")
        if length is not None and (not length.strip().isdigit() or int(length) != remaining):
            client.discard(resp)
            raise RemoteError("server_changed", f"Content-Length {length} != {remaining}")
        fh.seek(cur["have"])
        try:
            while remaining:
                try:
                    # read1: what has arrived, so a pause or cancel is not stuck behind a full block
                    block = resp.read1(min(CHUNK, remaining))
                except (OSError, http.client.HTTPException) as exc:
                    client.close()
                    raise _Net(type(exc).__name__, short=True) from None
                if not block:
                    client.close()
                    raise _Net("body ended early", short=True)
                fh.write(block)
                cur["hash"].update(block)
                cur["have"] += len(block)
                remaining -= len(block)
                self._add_progress(gid, entry, len(block))
                self._check(gid, entry)
        except _Stop:
            client.close()
            raise
        if not resp.isclosed():
            try:
                extra = resp.read(1)
            except (OSError, http.client.HTTPException):
                extra = b""
            if extra:
                client.close()
                raise RemoteError("server_changed", "body longer than the file")
