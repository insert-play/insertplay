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
CACHE_NAME = "hash-cache.json"
CHUNK = 1024 * 1024


class SkipGame(Exception):
    """The game folder can't be indexed; the message says why."""


class FatalError(Exception):
    """Nothing can be written."""


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


def index_game(library: str, name: str, old_cache: dict, new_cache: dict) -> dict:
    """Index entry and file list of one game folder. Raises SkipGame."""
    problem = path_problem(name)
    if problem:
        raise SkipGame(f"folder name: {problem}")
    game_dir = os.path.join(library, name)
    manifest = read_manifest(game_dir)
    guid = read_guid(game_dir)
    files, dirs, links = walk_game(game_dir, name, old_cache, new_cache)
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
        write_atomic(os.path.join(lists_dir, f"{r['entry']['id']}.json"), r["list_bytes"])
    write_atomic(os.path.join(output, "index.json"), index_bytes)

    keep = {f"{r['entry']['id']}.json" for r in games}
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
    try:
        result = build_index(args.library, args.output, use_cache=not args.no_cache)
    except FatalError as exc:
        log(f"error: {exc}")
        return 2
    for r in result["games"]:
        e = r["entry"]
        log(f"indexed {e['path']}: {e['name']!r} id={e['id']} version={e['version']} "
            f"{e['fileCount']} files, {r['dirs']} empty dirs, {r['links']} links, "
            f"{e['totalSize']} bytes, artwork={','.join(e['artwork']) or '-'} ({r['seconds']:.1f} s)")
    for name, reason in result["skipped"]:
        log(f"skipped {name}: {reason}")
    log(f"{len(result['games'])} games indexed, {len(result['skipped'])} skipped, "
        f"{time.monotonic() - started:.1f} s; wrote {os.path.join(args.output, 'index.json')}")
    return 1 if args.strict and result["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
