#!/usr/bin/env python3
"""Indexer of the InsertPlay remote library ("Biblioteca Remota").

Reads a library root (one InsertPlay card per folder) read-only and writes
`index.json` and `lists/<id>.json` to an output folder outside the library, as
described in docs/remote-library.md (contract v1). Every file is written to a
temporary name and renamed, so a server reading the output folder never serves a
half-written index.

Standard library only, Python 3.9+ (Debian 11 ships 3.9).

    python3 -I remote_library_index.py <library root> <output folder> [--no-cache]

Exit codes: 0 index written (some games may have been skipped, see stderr),
1 index written but at least one game was skipped and --strict was given,
2 nothing written (bad arguments, missing library, output inside the library).
"""
import argparse
import datetime
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
import time

SCHEMA_VERSION = 1
MANIFEST_NAME = "steam-shortcut.json"
IDENTITY_NAME = "shortcut-identity.json"
ARTWORK_KINDS = ("icon", "capsule", "horizontal", "hero", "logo")
SD_ROOT_PREFIX = "{SD_ROOT}/"
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_SEGMENT_BYTES = 255
MAX_PATH_BYTES = 4096
MAX_LIST_BYTES = 64 * 1024 * 1024  # the client refuses bigger file lists
MAX_INDEX_BYTES = 16 * 1024 * 1024
PACK_NAME = "pack.json"
PACK_COMPRESSIONS = ("none", "zstd")
ARTWORK_DIR = "steam-artwork"
CACHE_NAME = "hash-cache.json"
STATUS_NAME = "status.json"
CHUNK = 1024 * 1024


class SkipGame(Exception):
    """The game folder can't be indexed; the message says why."""


class FatalError(Exception):
    """Nothing can be written."""


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- path rules

def path_problem(path: str):
    """None if `path` follows the contract's path rules, otherwise the reason."""
    if not isinstance(path, str) or not path:
        return "empty path"
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError:
        return "not valid UTF-8"
    if len(encoded) > MAX_PATH_BYTES:
        return f"longer than {MAX_PATH_BYTES} bytes"
    if "\\" in path:
        return "contains a backslash"
    if CONTROL_RE.search(path):
        return "contains a control character"
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            return "empty, '.' or '..' segment"
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            return f"segment longer than {MAX_SEGMENT_BYTES} bytes"
    if path.endswith(".part"):
        return "ends in .part (reserved by the client)"
    return None


def link_target_problem(link_path: str, target: str):
    """None if the symlink `link_path -> target` stays inside the game folder."""
    if not target:
        return "empty target"
    try:
        encoded = target.encode("utf-8")
    except UnicodeEncodeError:
        return "target is not valid UTF-8"
    if len(encoded) > MAX_PATH_BYTES:
        return "target too long"
    if "\\" in target or CONTROL_RE.search(target):
        return "target has a backslash or a control character"
    if target.startswith("/"):
        return "absolute target"
    for segment in target.split("/"):
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            return "target segment too long"
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(link_path), target))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        return "target outside the game folder"
    return None


# ---------------------------------------------------------------- reading a game

def _get(data: dict, key: str):
    """Case-insensitive key, as the plugin reads the manifest (manifest._get)."""
    if key in data:
        return data[key]
    lowered = key.lower()
    for k, v in data.items():
        if isinstance(k, str) and k.lower() == lowered:
            return v
    return None


def _read_json_object(path: str, what: str) -> dict:
    try:
        st = os.lstat(path)
    except OSError:
        raise SkipGame(f"no {what} at the folder root")
    if not stat.S_ISREG(st.st_mode):
        raise SkipGame(f"{what} is not a regular file")
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise SkipGame(f"could not read {what}: {exc}")
    if not isinstance(data, dict):
        raise SkipGame(f"{what} is not a JSON object")
    return data


def read_manifest(game_dir: str) -> dict:
    data = _read_json_object(os.path.join(game_dir, MANIFEST_NAME), MANIFEST_NAME)
    shortcut = _get(data, "shortcut")
    if not isinstance(shortcut, dict):
        raise SkipGame(f"{MANIFEST_NAME} has no 'shortcut' object")
    app_name = _get(shortcut, "appName")
    launch = _get(shortcut, "launch")
    if not isinstance(app_name, str) or not app_name.strip():
        raise SkipGame(f"{MANIFEST_NAME} needs shortcut.appName")
    if not isinstance(launch, dict) or not _get(launch, "exe"):
        raise SkipGame(f"{MANIFEST_NAME} needs shortcut.launch.exe")
    return data


def read_guid(game_dir: str) -> str:
    data = _read_json_object(os.path.join(game_dir, IDENTITY_NAME), IDENTITY_NAME)
    guid = _get(data, "guid")
    if not isinstance(guid, str) or not guid.strip():
        raise SkipGame(f"{IDENTITY_NAME} has no guid")
    guid = guid.strip().lower()
    if not GUID_RE.match(guid):
        raise SkipGame(f"invalid guid in {IDENTITY_NAME}: {guid!r}")
    if guid == ZERO_GUID:
        raise SkipGame(f"{IDENTITY_NAME} still has the all-zero template guid")
    return guid


def hash_file(path: str):
    """(sha256, executable) of a file, read in chunks."""
    digest = hashlib.sha256()
    head = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            if len(head) < 4:
                head += chunk[: 4 - len(head)]
            digest.update(chunk)
    return digest.hexdigest(), is_executable(head)


def is_executable(head: bytes) -> bool:
    """ELF binaries (AppImages included) and shebang scripts; never the file mode."""
    return head.startswith(b"\x7fELF") or head.startswith(b"#!")


def walk_game(game_dir: str, cache_prefix: str, old_cache: dict, new_cache: dict):
    """files, dirs, links of a game folder, without following symlinks."""
    files, dirs, links = [], [], []
    real_root = os.path.realpath(game_dir)

    def visit(abs_dir: str, rel_dir: str) -> None:
        try:
            with os.scandir(abs_dir) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            raise SkipGame(f"could not list {rel_dir or '.'}: {exc}")
        if not entries and rel_dir:
            dirs.append(rel_dir)
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            problem = path_problem(rel)
            if problem:
                raise SkipGame(f"path {rel!r}: {problem}")
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SkipGame(f"could not stat {rel!r}: {exc}")
            if stat.S_ISLNK(st.st_mode):
                try:
                    target = os.readlink(entry.path)
                except OSError as exc:
                    raise SkipGame(f"could not read symlink {rel!r}: {exc}")
                problem = link_target_problem(rel, target)
                if problem:
                    raise SkipGame(f"symlink {rel!r} -> {target!r}: {problem}")
                # Lexical checks miss chains such as `x -> .` then `sub/y -> ../x/..`:
                # resolve on disk too.
                real = os.path.realpath(entry.path)
                if real != real_root and not real.startswith(real_root + os.sep):
                    raise SkipGame(f"symlink {rel!r} -> {target!r} resolves outside the game folder")
                links.append({"path": rel, "target": target})
            elif stat.S_ISDIR(st.st_mode):
                visit(entry.path, rel)
            elif stat.S_ISREG(st.st_mode):
                key = f"{cache_prefix}/{rel}"
                cached = old_cache.get(key)
                if (isinstance(cached, list) and len(cached) == 4
                        and cached[0] == st.st_size and cached[1] == st.st_mtime_ns):
                    sha, executable = cached[2], bool(cached[3])
                else:
                    try:
                        sha, executable = hash_file(entry.path)
                    except OSError as exc:
                        raise SkipGame(f"could not read {rel!r}: {exc}")
                new_cache[key] = [st.st_size, st.st_mtime_ns, sha, executable]
                files.append({"path": rel, "size": st.st_size, "sha256": sha, "executable": executable})
            else:
                raise SkipGame(f"{rel!r} is not a regular file, a directory or a symlink")

    visit(game_dir, "")
    files.sort(key=lambda f: f["path"])
    dirs.sort()
    links.sort(key=lambda l: l["path"])
    return files, dirs, links


def resolve_artwork(manifest: dict, files_by_path: dict) -> dict:
    """Same order as the plugin for a card: the manifest path, then steam-artwork/<kind>.png."""
    shortcut = _get(manifest, "shortcut") or {}
    artwork_def = _get(shortcut, "artwork")
    if not isinstance(artwork_def, dict):
        artwork_def = {}
    artwork = {}
    for kind in ARTWORK_KINDS:
        candidates = []
        value = _get(artwork_def, kind)
        if isinstance(value, str) and value.strip():
            value = value.strip()
            if value.startswith(SD_ROOT_PREFIX):
                value = value[len(SD_ROOT_PREFIX):]
            if path_problem(value) is None:
                candidates.append(value)
        candidates.append(f"steam-artwork/{kind}.png")
        for candidate in candidates:
            found = files_by_path.get(candidate)
            if found:
                artwork[kind] = {"path": found["path"], "size": found["size"], "sha256": found["sha256"]}
                break
    return artwork


def compute_version(files: list, dirs: list, links: list) -> str:
    payload = json.dumps({"files": files, "dirs": dirs, "links": links},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def index_packed_game(game_dir: str, name: str, guid: str, manifest: dict, files: list, dirs: list, links: list) -> dict:
    """Index entry of a game stored as tar parts (`pack.json`); no file list. Raises SkipGame."""
    data = _read_json_object(os.path.join(game_dir, PACK_NAME), PACK_NAME)
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise SkipGame(f"{PACK_NAME}: unsupported schemaVersion")
    compression = data.get("compression")
    if compression not in PACK_COMPRESSIONS:
        raise SkipGame(f"{PACK_NAME}: compression must be one of {', '.join(PACK_COMPRESSIONS)}")
    if not _count(data.get("extractedSize")) or not _count(data.get("fileCount")):
        raise SkipGame(f"{PACK_NAME}: extractedSize and fileCount must be non-negative integers")
    names = data.get("parts")
    if not isinstance(names, list) or not names or len(set(names)) != len(names):
        raise SkipGame(f"{PACK_NAME}: parts must be a non-empty list without repeats")
    if dirs or links:
        raise SkipGame("a packed game folder may only hold metadata, pack.json and its parts (found empty dirs or links)")
    by_path = {f["path"]: f for f in files}
    parts = []
    for part in names:
        problem = path_problem(part) if isinstance(part, str) else "not a string"
        if problem:
            raise SkipGame(f"{PACK_NAME}: part {part!r}: {problem}")
        found = by_path.get(part)
        if found is None:
            raise SkipGame(f"{PACK_NAME}: part {part!r} is missing")
        parts.append({"path": part, "size": found["size"], "sha256": found["sha256"]})
    allowed = {MANIFEST_NAME, IDENTITY_NAME, PACK_NAME, *names}
    for f in files:
        if f["path"] not in allowed and not f["path"].startswith(ARTWORK_DIR + "/"):
            raise SkipGame(f"{f['path']!r} is outside the packed layout (metadata, {ARTWORK_DIR}/, pack.json, parts)")
    pack = {"compression": compression, "parts": parts}
    payload = json.dumps(pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    version = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    entry = {
        "id": guid,
        "version": version,
        "path": name,
        "name": _get(_get(manifest, "shortcut"), "appName").strip(),
        "totalSize": data["extractedSize"],
        "fileCount": data["fileCount"],
        "pack": pack,
        "manifest": manifest,
        "artwork": resolve_artwork(manifest, by_path),
    }
    return {"entry": entry, "list_bytes": None, "dirs": 0, "links": 0}


def index_game(library: str, name: str, old_cache: dict, new_cache: dict) -> dict:
    """Index entry and file list of one game folder. Raises SkipGame."""
    problem = path_problem(name)
    if problem:
        raise SkipGame(f"folder name: {problem}")
    game_dir = os.path.join(library, name)
    manifest = read_manifest(game_dir)
    guid = read_guid(game_dir)
    files, dirs, links = walk_game(game_dir, name, old_cache, new_cache)
    if any(f["path"] == PACK_NAME for f in files):
        return index_packed_game(game_dir, name, guid, manifest, files, dirs, links)
    version = compute_version(files, dirs, links)
    file_list = {"schemaVersion": SCHEMA_VERSION, "id": guid, "version": version, "files": files}
    if dirs:
        file_list["dirs"] = dirs
    if links:
        file_list["links"] = links
    list_bytes = json.dumps(file_list, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(list_bytes) > MAX_LIST_BYTES:
        raise SkipGame(f"file list is {len(list_bytes)} bytes, over the client limit of {MAX_LIST_BYTES}")
    shortcut = _get(manifest, "shortcut")
    entry = {
        "id": guid,
        "version": version,
        "path": name,
        "name": _get(shortcut, "appName").strip(),
        "totalSize": sum(f["size"] for f in files),
        "fileCount": len(files),
        "files": f"lists/{guid}.json",
        "filesSha256": hashlib.sha256(list_bytes).hexdigest(),
        "manifest": manifest,
        "artwork": resolve_artwork(manifest, {f["path"]: f for f in files}),
    }
    return {"entry": entry, "list_bytes": list_bytes, "dirs": len(dirs), "links": len(links)}


# ---------------------------------------------------------------- output

def write_atomic(path: str, data: bytes) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _previous_result(output: str):
    """The finished result in status.json (or the one it carried as `previous`), if any."""
    try:
        with open(os.path.join(output, STATUS_NAME), encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(old, dict):
        return None
    if old.get("state") == "running":
        old = old.get("previous")
        if not isinstance(old, dict):
            return None
    keys = ("state", "startedAt", "finishedAt", "seconds", "games", "skipped", "error")
    return {k: old.get(k) for k in keys}


def write_status(output: str, state: str, started_at: str, finished_at=None, games=None, skipped=None,
                 seconds=None, error=None, previous=None) -> None:
    """Writes status.json next to index.json: what the last (or the running) indexing did.

    `state` is "running", "ok" or "error". `skipped` is a list of (folder, reason). A run killed
    without a chance to write ("kill -9", power loss) leaves "running" behind; readers should
    compare `startedAt` with the clock before trusting it.
    """
    data = {
        "schemaVersion": 1,
        "state": state,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "seconds": None if seconds is None else round(seconds, 1),
        "games": games,
        "skipped": None if skipped is None else [{"path": n, "reason": r} for n, r in skipped],
        "error": error,
    }
    if previous is not None:
        data["previous"] = previous
    write_atomic(os.path.join(output, STATUS_NAME), (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    entries = data.get("files") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def build_index(library: str, output: str, use_cache: bool = True, now=None) -> dict:
    """Indexes `library` into `output`. Returns {"games": [...], "skipped": [(name, reason)]}."""
    if not os.path.isdir(library):
        raise FatalError(f"library root {library!r} is not a directory (disk not mounted?)")
    real_library = os.path.realpath(library)
    real_output = os.path.realpath(output)
    if _inside(real_output, real_library) or _inside(real_library, real_output):
        raise FatalError("the output folder must be outside the library (and not contain it)")
    lists_dir = os.path.join(output, "lists")
    os.makedirs(lists_dir, exist_ok=True)

    cache_path = os.path.join(output, CACHE_NAME)
    old_cache = load_cache(cache_path) if use_cache else {}
    new_cache = {}

    try:
        with os.scandir(library) as it:
            candidates = sorted(it, key=lambda e: e.name)
    except OSError as exc:
        raise FatalError(f"could not list the library root: {exc}")

    indexed, skipped = [], []
    for entry in candidates:
        name = entry.name
        if entry.is_symlink():
            skipped.append((name, "symlink at the library root (not followed)"))
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue  # loose files at the root are not games
        started = time.monotonic()
        try:
            result = index_game(library, name, old_cache, new_cache)
        except SkipGame as exc:
            skipped.append((name, str(exc)))
            continue
        result["seconds"] = time.monotonic() - started
        indexed.append(result)

    by_guid = {}
    for result in indexed:
        by_guid.setdefault(result["entry"]["id"], []).append(result)
    games = []
    for guid, results in by_guid.items():
        if len(results) > 1:
            paths = ", ".join(r["entry"]["path"] for r in results)
            for r in results:
                skipped.append((r["entry"]["path"], f"duplicate guid {guid} (folders: {paths})"))
            continue
        games.append(results[0])
    games.sort(key=lambda r: r["entry"]["path"])
    skipped.sort()

    generated = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    index = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated,
        "library": "library/",
        "games": [r["entry"] for r in games],
    }
    index_bytes = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(index_bytes) > MAX_INDEX_BYTES:
        raise FatalError(f"index.json would be {len(index_bytes)} bytes, over the client limit of {MAX_INDEX_BYTES}")

    # File lists first, the index last: a client that reads the new index finds its lists.
    for r in games:
        if r["list_bytes"] is not None:
            write_atomic(os.path.join(lists_dir, f"{r['entry']['id']}.json"), r["list_bytes"])
    write_atomic(os.path.join(output, "index.json"), index_bytes)

    keep = {f"{r['entry']['id']}.json" for r in games if r["list_bytes"] is not None}
    for name in os.listdir(lists_dir):
        stale = name.endswith(".json") and GUID_RE.match(name[:-5]) and name not in keep
        if stale or ".json.tmp-" in name:
            try:
                os.unlink(os.path.join(lists_dir, name))
            except OSError:
                pass
    if use_cache:
        write_atomic(cache_path, json.dumps({"files": new_cache}, separators=(",", ":")).encode("utf-8"))
    return {"games": games, "skipped": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Index an InsertPlay remote library (contract v1).")
    parser.add_argument("library", help="library root: one InsertPlay card per folder (read only)")
    parser.add_argument("output", help="output folder for index.json and lists/ (outside the library)")
    parser.add_argument("--no-cache", action="store_true",
                        help=f"rehash every file instead of reusing {CACHE_NAME} (path, size, mtime)")
    parser.add_argument("--strict", action="store_true", help="exit with 1 when a game was skipped")
    args = parser.parse_args(argv)

    started = time.monotonic()
    started_at = _utc_now()

    def status(state, **fields):
        # status.json is best effort: failing to write it must not fail the indexing
        try:
            write_status(args.output, state, started_at, **fields)
        except OSError as exc:
            log(f"warning: could not write {STATUS_NAME}: {exc}")

    if os.path.isdir(args.output):
        # keep the last finished result visible while this run is going
        status("running", previous=_previous_result(args.output))
    try:
        result = build_index(args.library, args.output, use_cache=not args.no_cache)
    except FatalError as exc:
        log(f"error: {exc}")
        status("error", finished_at=_utc_now(), seconds=time.monotonic() - started, error=str(exc))
        return 2
    except Exception as exc:
        status("error", finished_at=_utc_now(), seconds=time.monotonic() - started, error=f"{type(exc).__name__}: {exc}")
        raise
    for r in result["games"]:
        e = r["entry"]
        packed = f"packed ({len(e['pack']['parts'])} part(s)), " if "pack" in e else ""
        log(f"indexed {e['path']}: {e['name']!r} id={e['id']} version={e['version']} {packed}"
            f"{e['fileCount']} files, {r['dirs']} empty dirs, {r['links']} links, "
            f"{e['totalSize']} bytes, artwork={','.join(e['artwork']) or '-'} ({r['seconds']:.1f} s)")
    for name, reason in result["skipped"]:
        log(f"skipped {name}: {reason}")
    elapsed = time.monotonic() - started
    status("ok", finished_at=_utc_now(), games=len(result["games"]), skipped=result["skipped"], seconds=elapsed)
    log(f"{len(result['games'])} games indexed, {len(result['skipped'])} skipped, "
        f"{elapsed:.1f} s; wrote {os.path.join(args.output, 'index.json')}")
    return 1 if args.strict and result["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
