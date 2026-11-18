#!/usr/bin/env python3
"""Packs an InsertPlay game folder into tar parts for the remote library (issue #61).

Reads the loose game folder (read-only) and writes a new folder with the metadata the
indexer needs left loose (`steam-shortcut.json`, `shortcut-identity.json`, `steam-artwork/`),
the game in `pack/NNN.tar[.zst]` and `pack.json`. Swap the folders yourself once it is done.
Contract: docs/remote-library.md ("Packed games").

Every part is an independent tar (and, with zstd, an independent frame): the client downloads,
verifies and extracts one part at a time and resumes at the first unfinished part. Members are
normalized (owner 0, mode 0755 for ELF files and shebang scripts, 0644 otherwise), the same rule
the indexer applies to loose files, so the result doesn't depend on the disk's file modes.

Standard library plus the `zstd` command (Python 3.9+, Debian 11 ships both).

    python3 pack_game.py <game folder> <new folder> [--part-mb 1024] [--compression zstd|none] [--level 3]
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile

from remote_library_index import (
    ARTWORK_DIR, IDENTITY_NAME, MANIFEST_NAME, PACK_COMPRESSIONS, PACK_NAME, SCHEMA_VERSION,
    SkipGame, is_executable, link_target_problem, log, path_problem, read_guid, read_manifest,
)

PACK_DIR = "pack"
LOOSE = (MANIFEST_NAME, IDENTITY_NAME)


class PackError(Exception):
    pass


def collect(game: str):
    """Sorted (files, empty dirs, links) of the game folder; raises PackError on anything the
    indexer or the client would refuse."""
    files, dirs, links = [], [], []

    def visit(abs_dir: str, rel_dir: str) -> None:
        with os.scandir(abs_dir) as it:
            entries = sorted(it, key=lambda e: e.name)
        if not entries and rel_dir:
            dirs.append(rel_dir)
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            problem = path_problem(rel)
            if problem:
                raise PackError(f"path {rel!r}: {problem}")
            st = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(entry.path)
                problem = link_target_problem(rel, target)
                if problem:
                    raise PackError(f"symlink {rel!r} -> {target!r}: {problem}")
                links.append((rel, target))
            elif stat.S_ISDIR(st.st_mode):
                visit(entry.path, rel)
            elif stat.S_ISREG(st.st_mode):
                files.append((rel, entry.path, st.st_size, int(st.st_mtime)))
            else:
                raise PackError(f"{rel!r} is not a regular file, a directory or a symlink")

    visit(game, "")
    return files, dirs, links


def split_parts(files, part_bytes: int):
    """Groups consecutive files into parts of about `part_bytes` (a bigger file gets its own)."""
    parts, current, size = [], [], 0
    for f in files:
        if current and size + f[2] > part_bytes:
            parts.append(current)
            current, size = [], 0
        current.append(f)
        size += f[2]
    if current or not parts:
        parts.append(current)
    return parts


def _info(name: str, mode: int, mtime: int, size: int = 0, kind=tarfile.REGTYPE, linkname: str = ""):
    info = tarfile.TarInfo(name)
    info.type, info.mode, info.mtime, info.size, info.linkname = kind, mode, mtime, size, linkname
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def write_part(out_path: str, members, extras, compression: str, level: int) -> None:
    """One tar into `out_path`. `extras` are (dirs, links) that go in this part."""
    proc = None
    if compression == "zstd":
        sink = open(out_path, "wb")
        proc = subprocess.Popen(["zstd", f"-{level}", "-T0", "-q", "-f", "-c"], stdin=subprocess.PIPE, stdout=sink)
        stream = proc.stdin
    else:
        sink = None
        stream = open(out_path, "wb")
    try:
        with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            dirs, links = extras
            for d in dirs:
                tar.addfile(_info(d, 0o755, 0, kind=tarfile.DIRTYPE))
            for rel, target in links:
                tar.addfile(_info(rel, 0o777, 0, kind=tarfile.SYMTYPE, linkname=target))
            for rel, path, size, mtime in members:
                with open(path, "rb") as f:
                    head = f.read(4)
                    f.seek(0)
                    mode = 0o755 if is_executable(head) else 0o644
                    tar.addfile(_info(rel, mode, mtime, size), f)
    finally:
        stream.close()
        if proc is not None:
            code = proc.wait()
            sink.close()
            if code:
                raise PackError(f"zstd exited with {code}")


def pack(game: str, dest: str, part_mb: int = 1024, compression: str = "zstd", level: int = 3) -> dict:
    if compression not in PACK_COMPRESSIONS:
        raise PackError(f"compression must be one of {', '.join(PACK_COMPRESSIONS)}")
    if compression == "zstd" and not shutil.which("zstd"):
        raise PackError("the zstd command is not installed")
    try:
        read_manifest(game)
        read_guid(game)
    except SkipGame as exc:
        raise PackError(str(exc)) from None
    if os.path.exists(dest):
        raise PackError(f"{dest} already exists")
    if os.path.realpath(dest).startswith(os.path.realpath(game) + os.sep):
        raise PackError("the new folder can't be inside the game folder")

    files, dirs, links = collect(game)
    for rel, _, _, _ in files:
        if rel == PACK_NAME or rel.split("/")[0] == PACK_DIR:
            raise PackError(f"{rel!r}: names pack.json and pack/ are reserved")
    # the metadata files are in the tar too: the installed game needs them, the loose copies are for the indexer
    groups = split_parts(files, part_mb * 1024 * 1024)

    tmp = dest.rstrip(os.sep) + ".packing"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, PACK_DIR))
    try:
        ext = ".tar.zst" if compression == "zstd" else ".tar"
        names = []
        for i, group in enumerate(groups):
            name = f"{PACK_DIR}/{i:03d}{ext}"
            log(f"part {i + 1}/{len(groups)}: {len(group)} file(s), {sum(g[2] for g in group)} bytes")
            write_part(os.path.join(tmp, *name.split("/")), group, (dirs, links) if i == 0 else ([], []), compression, level)
            names.append(name)
        for rel in LOOSE:
            shutil.copyfile(os.path.join(game, rel), os.path.join(tmp, rel))
        if os.path.isdir(os.path.join(game, ARTWORK_DIR)):
            shutil.copytree(os.path.join(game, ARTWORK_DIR), os.path.join(tmp, ARTWORK_DIR))
        meta = {
            "schemaVersion": SCHEMA_VERSION,
            "compression": compression,
            "extractedSize": sum(f[2] for f in files),
            "fileCount": len(files),
            "parts": names,
        }
        with open(os.path.join(tmp, PACK_NAME), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
        os.rename(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return meta


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pack an InsertPlay game folder into tar parts (remote library).")
    parser.add_argument("game", help="loose game folder (read only)")
    parser.add_argument("dest", help="new folder to create (must not exist)")
    parser.add_argument("--part-mb", type=int, default=1024, help="target size of a part, in MiB (default 1024)")
    parser.add_argument("--compression", choices=PACK_COMPRESSIONS, default="zstd")
    parser.add_argument("--level", type=int, default=3, help="zstd level (default 3; use 1 or none for data that doesn't compress)")
    args = parser.parse_args(argv)
    if args.part_mb < 1:
        parser.error("--part-mb must be at least 1")
    try:
        meta = pack(args.game, args.dest, args.part_mb, args.compression, args.level)
    except (PackError, OSError) as exc:
        log(f"error: {exc}")
        return 2
    log(f"packed {meta['fileCount']} files ({meta['extractedSize']} bytes) into {len(meta['parts'])} part(s): {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
