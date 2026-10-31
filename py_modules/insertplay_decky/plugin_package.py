"""The plugin's own package stored on the card (`insertplay/decky/InsertPlay.zip`).

The card carries the plugin: Decky installs it via "Install Plugin from ZIP File" and the
installed plugin compares versions, updates itself from the card or writes the installed
version to it. No `decky` import: everything here runs and is tested off the Deck.

The zip has the same layout as scripts/package.sh: a single `InsertPlay/` folder at the root.
The name matters: Decky extracts the zip as-is into ~/homebrew/plugins, so another
root folder would create a second plugin instead of replacing this one.
"""
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile

PLUGIN_NAME = "InsertPlay"
CARD_PACKAGE = os.path.join("insertplay", "decky", f"{PLUGIN_NAME}.zip")
# what goes into the package, from the project folder (package.sh) or the installed one (plugin);
# `upload` in scripts/deck.mjs sends the same list
PACKAGE_ENTRIES = ("plugin.json", "package.json", "main.py", "NOTICE", "py_modules", "dist")
REQUIRED = ("plugin.json", "package.json", "main.py", "dist/index.js")
# semver: major.minor.patch[-prerelease][+build]
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
# a real package is a few tens of KB; this stops zip bombs and huge files with
# the right name without limiting the plugin
MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED = 64 * 1024 * 1024

# zip_path -> ((mtime_ns, size), result or PackageError). The panel asks for the status
# every 5 s; without this each request reread and rehashed the zip on the card.
_cache: dict = {}


class PackageError(Exception):
    pass


def card_package_path(mount: str) -> str:
    return os.path.join(mount, CARD_PACKAGE)


def parse_version(value) -> tuple:
    """Sort key with semver precedence: `1.0.0-beta < 1.0.0`, and numeric
    prerelease identifiers sort as numbers. `+build` doesn't count."""
    match = VERSION_RE.match(str(value or "").strip())
    if not match:
        raise PackageError(f"invalid version: {value!r}")
    core = tuple(int(part) for part in match.group(1, 2, 3))
    pre = match.group(4)
    if not pre:
        return core + ((1,),)
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return core + ((0,) + ids,)


def compare_versions(a: str, b: str) -> int:
    """-1, 0 or 1."""
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)


def _check_plugin_json(data) -> None:
    if not isinstance(data, dict) or data.get("name") != PLUGIN_NAME:
        raise PackageError(f"plugin.json is not the {PLUGIN_NAME} plugin")
    flags = data.get("flags", [])
    # same rule as deploy-plugin.sh: the backend never runs as root
    if not isinstance(flags, list) or "root" in flags:
        raise PackageError("plugin.json asks for the root flag")


def read_package(zip_path: str) -> dict:
    """Validates the card's zip and returns `{version, sha256, size}`.

    This is the validation Decky doesn't do: the zip will be extracted as root into
    ~/homebrew/plugins, so we only accept the `InsertPlay/` folder, with no absolute
    paths, no `..`, no symlinks and no two names for the same file
    (`InsertPlay/./plugin.json` is extracted over the validated `plugin.json`). Every
    file is decompressed and CRC-checked: Decky deletes the installed version
    before extracting, and a zip that fails halfway would leave the user without the plugin.

    The file is read only once, so that the returned hash (checked by Decky on
    install) is of the validated bytes."""
    try:
        if os.path.getsize(zip_path) > MAX_ZIP_BYTES:
            raise PackageError(f"{zip_path} is too large")
        with open(zip_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise PackageError(f"could not read {zip_path}: {exc}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            total = 0
            seen = set()
            for info in infos:
                name = info.filename
                parts = name.split("/")
                # a folder ends in "/": only the last component may be empty
                inner = parts[:-1] if info.is_dir() else parts
                if (
                    "\\" in name
                    or ":" in name
                    or any(part in ("", ".", "..") for part in inner)
                ):
                    raise PackageError(f"unsafe path in zip: {name!r}")
                if name in seen:
                    raise PackageError(f"duplicate entry in zip: {name!r}")
                seen.add(name)
                if parts[0] != PLUGIN_NAME:
                    raise PackageError(f"zip must have a single {PLUGIN_NAME}/ folder, found {name!r}")
                # type bits in external_attr (Unix): 0o120000 = symlink
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise PackageError(f"symlink in zip: {name!r}")
                total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise PackageError("zip is too large")
            names = {info.filename for info in infos}
            missing = [r for r in REQUIRED if f"{PLUGIN_NAME}/{r}" not in names]
            if missing:
                raise PackageError(f"zip is missing {', '.join(missing)}")
            # decompresses everything and checks the CRC; a decompression error also lands below
            bad = archive.testzip()
            if bad is not None:
                raise PackageError(f"corrupt file in zip: {bad!r}")
            _check_plugin_json(json.loads(archive.read(f"{PLUGIN_NAME}/plugin.json")))
            package = json.loads(archive.read(f"{PLUGIN_NAME}/package.json"))
    except PackageError:
        raise
    except Exception as exc:
        # a corrupt zip fails in many ways (BadZipFile, zlib.error, EOFError,
        # NotImplementedError, RuntimeError for an encrypted entry...); all are "invalid"
        raise PackageError(f"invalid zip {zip_path}: {type(exc).__name__}: {exc}") from exc
    version = str(package.get("version") or "") if isinstance(package, dict) else ""
    parse_version(version)
    return {"version": version, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def read_package_cached(zip_path: str) -> dict:
    """`read_package`, redone only when the file changes (mtime or size)."""
    st = os.stat(zip_path)
    key = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(zip_path)
    if not hit or hit[0] != key:
        try:
            hit = (key, read_package(zip_path))
        except PackageError as exc:
            hit = (key, exc)
        _cache[zip_path] = hit
    if isinstance(hit[1], PackageError):
        raise hit[1]
    return dict(hit[1])


def _package_files(plugin_dir: str):
    for entry in PACKAGE_ENTRIES:
        path = os.path.join(plugin_dir, entry)
        if os.path.isfile(path):
            yield path, entry
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for name in sorted(files):
                    if name.endswith((".pyc", ".map")):
                        continue
                    full = os.path.join(root, name)
                    yield full, os.path.relpath(full, plugin_dir).replace(os.sep, "/")


def _card_package_dir(mount: str) -> str:
    """`<card>/insertplay/decky`, created if missing, guaranteed to be inside the card.

    The card is third-party content: an `insertplay` or `decky` that is a symlink
    would send the write outside it."""
    path = os.path.realpath(mount)
    for part in os.path.dirname(CARD_PACKAGE).split(os.sep):
        path = os.path.join(path, part)
        if os.path.islink(path):
            raise PackageError(f"{path} is a symlink")
        if not os.path.exists(path):
            os.mkdir(path)
        elif not os.path.isdir(path):
            raise PackageError(f"{path} is not a directory")
    return path


def _write_atomic(dest_zip: str, fill) -> dict:
    """Writes the zip with `fill(file)`, validates it and only then swaps `dest_zip`.

    The temp file is created with O_EXCL and a random name (mkstemp), so it never follows a
    symlink already on the card. fsync before the rename: a card removed mid-write
    leaves the old zip intact (or an orphan temp file), never a half-written
    zip. `os.replace` swaps `dest_zip` itself, even if it is a symlink."""
    folder = os.path.dirname(dest_zip)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{PLUGIN_NAME}.", suffix=".zip.tmp")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o644)  # mkstemp creates 0600; the package isn't secret
        with os.fdopen(fd, "wb") as f:
            fill(f)
            f.flush()
            os.fsync(f.fileno())
        # validate before replacing: never swap a good package for a broken one
        info = read_package(tmp)
        os.replace(tmp, dest_zip)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(folder)
    # os.replace keeps the validated bytes: cache the result instead of rereading the card
    st = os.stat(dest_zip)
    _cache[dest_zip] = ((st.st_mtime_ns, st.st_size), info)
    return dict(info)


def _zip_dir(plugin_dir: str):
    def fill(f):
        with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as archive:
            for full, rel in _package_files(plugin_dir):
                archive.write(full, f"{PLUGIN_NAME}/{rel}")

    return fill


def build_zip_from_dir(plugin_dir: str, dest_zip: str) -> dict:
    """Packs the plugin folder into `dest_zip` (a developer folder, like
    package.sh's `out/`) and returns its `read_package`. For the card, use
    `write_card_package`."""
    os.makedirs(os.path.dirname(dest_zip) or ".", exist_ok=True)
    return _write_atomic(dest_zip, _zip_dir(plugin_dir))


def write_card_package(plugin_dir: str, mount: str) -> dict:
    """Packs the plugin folder straight into the card's package."""
    dest = os.path.join(_card_package_dir(mount), os.path.basename(CARD_PACKAGE))
    return _write_atomic(dest, _zip_dir(plugin_dir))


def copy_package_to_card(src_zip: str, mount: str) -> tuple:
    """Copies a ready zip (out/InsertPlay.zip) to the card, never downgrading:
    if the card already has a newer valid package, it stays. Returns
    `(info of the package on the card, copied?)`."""
    new = read_package(src_zip)
    dest = os.path.join(_card_package_dir(mount), os.path.basename(CARD_PACKAGE))
    if os.path.isfile(dest):
        try:
            old = read_package(dest)
        except PackageError:
            old = None
        if old and compare_versions(old["version"], new["version"]) > 0:
            return old, False

    def fill(f):
        with open(src_zip, "rb") as src:
            while chunk := src.read(1 << 20):
                f.write(chunk)

    return _write_atomic(dest, fill), True


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return  # Windows can't open a directory; on the Deck this always works
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def card_status(mount: str, installed_version: str) -> dict:
    """State of a card's package relative to the installed plugin.

    `relation`: `missing` (no zip), `invalid`, `newer` (the card carries a newer
    version), `older` or `same`."""
    path = card_package_path(mount)
    out = {"zipPath": path, "version": None, "sha256": None, "relation": "missing", "error": None}
    if not os.path.isfile(path):
        return out
    try:
        out.update(read_package_cached(path))
    except (PackageError, OSError) as exc:
        out.update(relation="invalid", error=str(exc))
        return out
    try:
        cmp = compare_versions(out["version"], installed_version)
    except PackageError:
        cmp = 1  # unreadable installed version: any valid package on the card will do
    out["relation"] = {1: "newer", 0: "same", -1: "older"}[cmp]
    return out
