"""Save snapshot: the files of a save, its hash, a tar.gz of it and the restore.

The hash is the sha256 of the sorted list of `[path, size, sha256]` of every regular file,
paths relative to the game folder with `/` (docs/remote-library.md#save-hash). Symlinks are
never followed nor stored. The archive holds `insertplay-save.json` (format, roots, files,
hash) and `files/<path>` for each file.
"""
import hashlib
import io
import json
import os
import shutil
import tarfile
import time

from .strategies import SaveSpec, StrategyError, _through_link, check_rel, excluded, expand, matches

ARCHIVE_FORMAT = 1
MANIFEST = "insertplay-save.json"
STAGING = ".insertplay-saves.partial"
CHUNK = 1024 * 1024


class SnapshotError(Exception):
    pass


class Snapshot:
    def __init__(self, roots: list, files: list):
        self.roots = roots  # concrete save roots found, relative to the game folder
        self.files = files  # sorted [(path, size, sha256, mtime)]
        listing = [[p, s, h] for p, s, h, _ in files]
        self.hash = list_hash(listing)
        self.newest = max((m for *_, m in files), default=0.0)
        self.size = sum(s for _, s, _, _ in files)

    @property
    def empty(self) -> bool:
        return not self.files


def list_hash(listing: list) -> str:
    data = json.dumps(sorted(listing), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def concrete_roots(root: str, spec: SaveSpec) -> list:
    """Existing save roots of `spec` in `root`, without one nested in another."""
    found = sorted({rel for pattern in spec.patterns for rel in expand(root, pattern)})
    out = []
    for rel in found:
        if not any(rel.startswith(parent + "/") for parent in out):
            out.append(rel)
    return out


def _walk_root(root: str, rel_root: str, exclude: list):
    """(path, full path) of the regular files under one save root, excluded paths and
    symlinks left out."""
    full_root = os.path.join(root, rel_root)
    if os.path.isfile(full_root):
        yield rel_root, full_root
        return
    for dirpath, dirnames, filenames in os.walk(full_root):
        inner = os.path.relpath(dirpath, full_root).replace(os.sep, "/")
        inner = "" if inner == "." else inner
        keep = []
        for name in sorted(dirnames):
            sub = f"{inner}/{name}" if inner else name
            if not os.path.islink(os.path.join(dirpath, name)) and not excluded(sub, exclude):
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            sub = f"{inner}/{name}" if inner else name
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or excluded(sub, exclude) or not os.path.isfile(full):
                continue
            yield f"{rel_root}/{sub}", full


def list_files(root: str, spec: SaveSpec) -> list:
    """Paths of the save's files, without hashing."""
    return [rel for rel_root in concrete_roots(root, spec) for rel, _ in _walk_root(root, rel_root, spec.exclude)]


def _blocked(root: str, rel: str):
    """A parent of `rel` on the device that is not a folder (a file, a symlink): the restore
    could not put the file there. None when the way is clear."""
    cur = root
    for part in rel.split("/")[:-1]:
        cur = os.path.join(cur, part)
        if os.path.islink(cur) or (os.path.exists(cur) and not os.path.isdir(cur)):
            return cur
    return None


def scan(root: str, spec: SaveSpec) -> Snapshot:
    roots = concrete_roots(root, spec)
    files = []
    for rel_root in roots:
        for rel, full in _walk_root(root, rel_root, spec.exclude):
            st = os.stat(full)
            files.append((rel, st.st_size, sha256_file(full), st.st_mtime))
    files.sort()
    return Snapshot(roots, files)


class _HashingReader:
    def __init__(self, f):
        self.f = f
        self.h = hashlib.sha256()

    def read(self, n=-1):
        data = self.f.read(n)
        self.h.update(data)
        return data


def write_archive(root: str, snap: Snapshot, dest: str, level: int = 3) -> int:
    """tar.gz of `snap` at `dest`. Fails (SnapshotError) when a file changed since the scan:
    the game is still writing, try again later. Returns the archive size."""
    manifest = {
        "format": ARCHIVE_FORMAT,
        "roots": snap.roots,
        "files": [[p, s, h] for p, s, h, _ in snap.files],
        "hash": snap.hash,
        "newestMtime": snap.newest,
    }
    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    tmp = dest + ".partial"
    try:
        with tarfile.open(tmp, "w:gz", compresslevel=level, format=tarfile.PAX_FORMAT) as tar:
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(raw)
            info.mtime = int(time.time())
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(raw))
            for rel, size, digest, mtime in snap.files:
                info = tarfile.TarInfo("files/" + rel)
                info.size = size
                info.mtime = mtime
                info.mode = 0o644
                with open(os.path.join(root, rel), "rb") as f:
                    reader = _HashingReader(f)
                    tar.addfile(info, reader)
                    if os.fstat(f.fileno()).st_size != size or reader.h.hexdigest() != digest:
                        raise SnapshotError(f"{rel} changed while archiving")
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return os.path.getsize(dest)


def _read_manifest(tar: tarfile.TarFile) -> dict:
    member = tar.next()
    if member is None or member.name != MANIFEST or not member.isfile() or member.size > 64 * 1024 * 1024:
        raise SnapshotError("archive without its manifest")
    data = json.loads(tar.extractfile(member).read().decode("utf-8"))
    if not isinstance(data, dict) or data.get("format") != ARCHIVE_FORMAT:
        raise SnapshotError("unknown archive format")
    return data


def _place(rel: str, spec: SaveSpec):
    """"write" for a path inside the strategy's save roots, "skip" for one this device excludes
    (another device, or an older `saves` block, synced it), None for anything else."""
    for pattern in spec.patterns:
        depth = len(pattern.split("/"))
        if matches(pattern, rel):
            inner = "/".join(rel.split("/")[depth:])
            return "skip" if inner and excluded(inner, spec.exclude) else "write"
    return None


def _remove_tree(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def restore(root: str, spec: SaveSpec, archive: str, expected_hash: str = None) -> Snapshot:
    """Replaces the save in `root` with the archive's. Extracts to a staging folder in the
    game folder, checks every file and the whole hash, then renames each file into place and
    removes the save files the archive doesn't have. Excluded paths and symlinks on the
    device stay as they are. Returns the snapshot of the restored save."""
    staging = os.path.join(root, STAGING)
    _remove_tree(staging)
    os.makedirs(staging)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            manifest = _read_manifest(tar)
            listing = manifest.get("files")
            if not isinstance(listing, list):
                raise SnapshotError("archive manifest without files")
            expected = {}
            skipped = set()
            for item in listing:
                if not (isinstance(item, list) and len(item) == 3):
                    raise SnapshotError("bad archive manifest")
                rel = check_rel(item[0])
                place = _place(rel, spec)
                if place is None:
                    raise SnapshotError(f"{rel} is outside the game's save folders")
                if place == "skip":
                    skipped.add(rel)
                elif _through_link(root, rel):
                    raise SnapshotError(f"{rel} goes through a symlink on this device")
                expected[rel] = (int(item[1]), str(item[2]))
            if list_hash([[p, s, h] for p, (s, h) in expected.items()]) != manifest.get("hash"):
                raise SnapshotError("archive manifest hash mismatch")
            if expected_hash and manifest.get("hash") != expected_hash:
                raise SnapshotError("archive is not the save the server announced")
            seen = {}
            # tar.next(), not `for member in tar`: iterating restarts at the manifest
            while True:
                member = tar.next()
                if member is None:
                    break
                if not member.isfile() or not member.name.startswith("files/"):
                    raise SnapshotError(f"unexpected archive entry {member.name!r}")
                rel = member.name[len("files/"):]
                if rel not in expected or rel in seen:
                    raise SnapshotError(f"unexpected archive file {rel!r}")
                src = tar.extractfile(member)
                h = hashlib.sha256()
                size = 0
                if rel in skipped:
                    # not written here, still checked: a damaged archive is refused whole
                    for chunk in iter(lambda: src.read(CHUNK), b""):
                        h.update(chunk)
                        size += len(chunk)
                    if (size, h.hexdigest()) != expected[rel]:
                        raise SnapshotError(f"{rel} damaged in the archive")
                    seen[rel] = None
                    continue
                dest = os.path.join(staging, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as out:
                    for chunk in iter(lambda: src.read(CHUNK), b""):
                        h.update(chunk)
                        size += len(chunk)
                        out.write(chunk)
                if (size, h.hexdigest()) != expected[rel]:
                    raise SnapshotError(f"{rel} damaged in the archive")
                os.utime(dest, (member.mtime, member.mtime))
                seen[rel] = member.mtime
            if set(seen) != set(expected):
                raise SnapshotError("archive is missing files")
        # check every destination before touching anything: a failure halfway would leave a
        # save mixed from two devices
        written = {rel: mtime for rel, mtime in seen.items() if rel not in skipped}
        for rel in written:
            blocked = _blocked(root, rel)
            if blocked:
                raise SnapshotError(f"{blocked} is in the way of {rel}")
        old = list_files(root, spec)
        # into place: the archive's files first, then the save files it doesn't have
        for rel in sorted(written):
            dest = os.path.join(root, rel)
            parent = os.path.dirname(dest)
            if os.path.islink(dest) or os.path.isdir(dest):
                _remove_tree(dest)
            os.makedirs(parent, exist_ok=True)
            os.replace(os.path.join(staging, rel), dest)
        for rel in old:
            if rel not in written:
                try:
                    os.remove(os.path.join(root, rel))
                except OSError:
                    pass
    except (tarfile.TarError, OSError, ValueError, EOFError, StrategyError) as exc:
        raise SnapshotError(str(exc)) from exc
    finally:
        _remove_tree(staging)
    roots = manifest.get("roots") if isinstance(manifest.get("roots"), list) else []
    return Snapshot(
        roots, sorted((rel, size, digest, written[rel]) for rel, (size, digest) in expected.items() if rel in written)
    )
