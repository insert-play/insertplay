"""Packed games (issue #61): pack_game.py -> indexer -> download engine, end to end."""
import io
import json
import os
import shutil
import stat
import sys
import tarfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "indexer"))

import pack_game  # noqa: E402
import remote_library_index as idx  # noqa: E402
from test_remote_library import (  # noqa: E402,F401
    GID, MANIFEST, env, game_files, ready, server, status, wait_state, _sha,
)
from insertplay_decky import remote_library as rl  # noqa: E402

FOLDER = "jogo-teste"


def _zstd_ok() -> bool:
    try:
        from compression import zstd  # noqa: F401
    except ImportError:
        return False
    return True


needs_zstd = pytest.mark.skipif(not _zstd_ok(), reason="needs Python 3.14 compression.zstd")
needs_zstd_cli = pytest.mark.skipif(shutil.which("zstd") is None or not _zstd_ok(), reason="needs the zstd command")
needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks need a POSIX filesystem")


def recompress(dest):
    """Turns the tar parts of a packed folder into zstd frames without the zstd command."""
    from compression import zstd
    with open(os.path.join(dest, "pack.json")) as f:
        meta = json.load(f)
    names = []
    for part in meta["parts"]:
        path = os.path.join(dest, *part.split("/"))
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        with open(path + ".zst", "wb") as f:
            f.write(zstd.compress(data))
        names.append(part + ".zst")
    meta.update({"compression": "zstd", "parts": names})
    with open(os.path.join(dest, "pack.json"), "w") as f:
        json.dump(meta, f)
    return meta


def make_source(tmp_path, files, link=True):
    src = tmp_path / "src" / FOLDER
    for rel, data in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (src / "saves").mkdir()
    if link:
        os.symlink("big.bin", src / "data" / "link.bin")
    return str(src)


def publish_packed(srv, tmp_path, files, compression, part_mb=1, link=True):
    src = make_source(tmp_path, files, link)
    dest = os.path.join(srv.root, "library", FOLDER)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    meta = pack_game.pack(src, dest, part_mb, "none" if compression == "zstd-module" else compression)
    if compression == "zstd-module":
        meta = recompress(dest)
    reindex(srv, tmp_path)
    return meta


def reindex(srv, tmp_path):
    out = str(tmp_path / "idx")
    result = idx.build_index(os.path.join(srv.root, "library"), out, use_cache=False)
    assert not result["skipped"], result["skipped"]
    shutil.copyfile(os.path.join(out, "index.json"), os.path.join(srv.root, "index.json"))
    return result


def assert_installed(env, files, link=True):
    root = os.path.join(env.games_root, GID)
    for rel, data in files.items():
        path = os.path.join(root, *rel.split("/"))
        with open(path, "rb") as f:
            assert f.read() == data, rel
        expect = 0o755 if rel in ("insertplay-start.sh", "bin/tool") else 0o644
        assert stat.S_IMODE(os.lstat(path).st_mode) == expect, rel
    assert os.path.isdir(os.path.join(root, "saves"))
    if link:
        assert os.readlink(os.path.join(root, "data", "link.bin")) == "big.bin"
    assert not os.path.exists(os.path.join(env.games_root, ".incoming", GID))


@needs_symlinks
@pytest.mark.parametrize("compression", ["none", pytest.param("zstd-module", marks=needs_zstd),
                                         pytest.param("zstd", marks=needs_zstd_cli)])
def test_packed_game_installs_part_by_part(env, server, tmp_path, compression):
    files = game_files()
    meta = publish_packed(server, tmp_path, files, compression)
    assert len(meta["parts"]) >= 2  # data/big.bin alone is over 3 MB: more than one 1 MiB part
    lib = ready(env, server)
    try:
        st = status(lib)
        assert st["state"] == "not_installed" and st["bytesTotal"] == meta["extractedSize"]
        assert lib.install(GID)["ok"]
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "installed", st
        assert st["percent"] == 100 and st["installedVersion"] == st["version"]
        assert_installed(env, files)
        for part in meta["parts"]:
            assert len([r for r in server.requests if r[0].endswith("/" + part)]) == 1
        assert not [r for r in server.requests if "/lists/" in r[0]]  # no file list for a packed game
        assert not [r for r in server.requests if r[0].endswith("/data/big.bin")]  # loose files are not downloaded
    finally:
        lib.stop()


@needs_symlinks
def test_interrupted_part_restarts_and_finished_parts_stay(env, server, tmp_path):
    files = game_files()
    meta = publish_packed(server, tmp_path, files, "none")
    last = meta["parts"][-1]
    server.drop_after[f"/library/{FOLDER}/{last}"] = 1000
    lib = ready(env, server)
    try:
        lib.install(GID)
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        assert len([r for r in server.requests if r[0].endswith("/" + last)]) == 2
        for part in meta["parts"][:-1]:
            assert len([r for r in server.requests if r[0].endswith("/" + part)]) == 1
        assert any("restarting the part" in line for line in env.logs)
        assert_installed(env, files)
    finally:
        lib.stop()


@needs_symlinks
def test_a_restarted_engine_skips_finished_parts(env, server, tmp_path):
    files = game_files()
    meta = publish_packed(server, tmp_path, files, "none")
    first, rest = meta["parts"][0], meta["parts"][1:]
    server.status[f"/library/{FOLDER}/{rest[0]}"] = 404  # the second part fails: the first stays done
    lib = ready(env, server)
    try:
        lib.install(GID)
        assert wait_state(lib, ("error",))["error"]["code"] == "server_changed"
        lib.stop()
        del server.status[f"/library/{FOLDER}/{rest[0]}"]
        lib2 = env.make()
        try:
            assert lib2.resume(GID)["ok"]
            assert wait_state(lib2, ("installed", "error"))["state"] == "installed"
            assert len([r for r in server.requests if r[0].endswith("/" + first)]) == 1
            assert_installed(env, files)
        finally:
            lib2.stop()
    finally:
        lib.stop()


@needs_symlinks
def test_corrupt_part_is_downloaded_again_once(env, server, tmp_path):
    files = game_files()
    meta = publish_packed(server, tmp_path, files, "none")
    path = f"/library/{FOLDER}/{meta['parts'][-1]}"
    server.corrupt[path] = 1
    lib = ready(env, server)
    try:
        lib.install(GID)
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        assert len([r for r in server.requests if r[0].endswith(path)]) == 2
        assert_installed(env, files)
    finally:
        lib.stop()


@needs_symlinks
def test_corrupt_part_twice_is_hash_mismatch(env, server, tmp_path):
    meta = publish_packed(server, tmp_path, game_files(), "none")
    server.corrupt[f"/library/{FOLDER}/{meta['parts'][-1]}"] = 2
    lib = ready(env, server)
    try:
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "error" and st["error"]["code"] == "hash_mismatch"
        assert not os.path.exists(os.path.join(env.games_root, GID))
    finally:
        lib.stop()


# ---------------------------------------------------------------- hostile tars


def publish_tar(srv, members):
    """One uncompressed part made of (TarInfo, bytes or None) pairs, indexed by hand."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    raw = buf.getvalue()
    lib = os.path.join(srv.root, "library", FOLDER, "pack")
    os.makedirs(lib, exist_ok=True)
    with open(os.path.join(lib, "000.tar"), "wb") as f:
        f.write(raw)
    index = {
        "schemaVersion": 1, "generatedAt": "2026-11-17T00:00:00Z", "library": "library/",
        "games": [{
            "id": GID, "version": "abc123", "path": FOLDER, "name": "Jogo Teste", "totalSize": 10, "fileCount": 1,
            "manifest": MANIFEST,
            "pack": {"compression": "none", "parts": [{"path": "pack/000.tar", "size": len(raw), "sha256": _sha(raw)}]},
        }],
    }
    with open(os.path.join(srv.root, "index.json"), "w") as f:
        json.dump(index, f)


def info(name, kind=tarfile.REGTYPE, size=0, link=""):
    ti = tarfile.TarInfo(name)
    ti.type, ti.size, ti.linkname, ti.mode = kind, size, link, 0o644
    return ti


@pytest.mark.parametrize("members", [
    [(info("../evil", size=1), b"x")],
    [(info("/abs", size=1), b"x")],
    [(info("ok/link", tarfile.SYMTYPE, link="../../outside"), None)],
    [(info("ok/link", tarfile.SYMTYPE, link="/etc"), None)],
    [(info("hard", tarfile.LNKTYPE, link="other"), None)],
    [(info("dev", tarfile.CHRTYPE), None)],
    [(info("a", tarfile.SYMTYPE, link="."), None), (info("a/x", size=1), b"x")],
    [(info("name.part", size=1), b"x")],
])
def test_hostile_tar_is_refused(env, server, members):
    publish_tar(server, members)
    lib = ready(env, server)
    try:
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "error" and st["error"]["code"] == "bad_path", st
        assert not os.path.exists(os.path.join(env.games_root, GID))
        outside = [n for n in os.listdir(env.games_root) if n not in (".incoming",)]
        assert outside == []
    finally:
        lib.stop()


def test_index_with_a_bad_pack_drops_the_game(env, server):
    publish_tar(server, [(info("f", size=1), b"x")])
    with open(os.path.join(server.root, "index.json")) as f:
        index = json.load(f)
    index["games"][0]["pack"]["parts"][0]["sha256"] = "zz"
    with open(os.path.join(server.root, "index.json"), "w") as f:
        json.dump(index, f)
    lib = ready(env, server)
    assert lib.games()["games"] == []


# ---------------------------------------------------------------- indexer and pack tool


@needs_symlinks
def test_indexer_entry_for_a_packed_game(server, tmp_path):
    files = game_files()
    meta = publish_packed(server, tmp_path, files, "none")
    with open(os.path.join(server.root, "index.json")) as f:
        entry = json.load(f)["games"][0]
    assert entry["id"] == GID and entry["path"] == FOLDER
    assert "files" not in entry and "filesSha256" not in entry
    assert entry["totalSize"] == meta["extractedSize"] == sum(map(len, files.values()))
    assert entry["fileCount"] == len(files)
    assert [p["path"] for p in entry["pack"]["parts"]] == meta["parts"]
    for part in entry["pack"]["parts"]:
        with open(os.path.join(server.root, "library", FOLDER, *part["path"].split("/")), "rb") as f:
            data = f.read()
        assert part["size"] == len(data) and part["sha256"] == _sha(data)
    assert entry["artwork"]["capsule"]["path"] == "steam-artwork/capsule.png"
    assert not os.listdir(tmp_path / "idx" / "lists")


@needs_symlinks
def test_version_changes_with_the_parts(server, tmp_path):
    publish_packed(server, tmp_path, game_files(), "none")
    with open(os.path.join(server.root, "index.json")) as f:
        first = json.load(f)["games"][0]["version"]
    reindex(server, tmp_path)
    with open(os.path.join(server.root, "index.json")) as f:
        assert json.load(f)["games"][0]["version"] == first
    part = os.path.join(server.root, "library", FOLDER, "pack", "000.tar")
    with open(part, "ab") as f:
        f.write(b"\0" * 512)
    reindex(server, tmp_path)
    with open(os.path.join(server.root, "index.json")) as f:
        assert json.load(f)["games"][0]["version"] != first


def test_indexer_skips_a_packed_folder_with_loose_game_files(tmp_path):
    lib = tmp_path / "lib"
    src = make_source(tmp_path, game_files(), link=False)
    pack_game.pack(src, str(lib / FOLDER), 1, "none")
    (lib / FOLDER / "extra.bin").write_bytes(b"x")
    result = idx.build_index(str(lib), str(tmp_path / "out"), use_cache=False)
    assert result["games"] == [] and "outside the packed layout" in result["skipped"][0][1]


def test_indexer_skips_a_missing_part(tmp_path):
    lib = tmp_path / "lib"
    src = make_source(tmp_path, game_files(), link=False)
    pack_game.pack(src, str(lib / FOLDER), 1, "none")
    os.unlink(lib / FOLDER / "pack" / "000.tar")
    result = idx.build_index(str(lib), str(tmp_path / "out"), use_cache=False)
    assert result["games"] == [] and "missing" in result["skipped"][0][1]


@needs_symlinks
def test_pack_tool_normalizes_modes_and_splits(tmp_path):
    src = make_source(tmp_path, game_files())
    os.chmod(os.path.join(src, "data", "big.bin"), 0o755)  # NTFS-like: every file looks executable
    dest = str(tmp_path / "out" / FOLDER)
    meta = pack_game.pack(src, dest, 1, "none")
    assert meta["fileCount"] == len(game_files())
    modes, names = {}, []
    for part in meta["parts"]:
        with tarfile.open(os.path.join(dest, *part.split("/"))) as tar:
            for m in tar:
                names.append(m.name)
                modes[m.name] = m.mode
                assert m.uid == m.gid == 0
    assert modes["data/big.bin"] == 0o644 and modes["bin/tool"] == 0o755 and modes["insertplay-start.sh"] == 0o755
    assert {"saves", "data/link.bin"} <= set(names)
    assert os.path.isfile(os.path.join(dest, "steam-shortcut.json"))
    assert os.path.isfile(os.path.join(dest, "steam-artwork", "capsule.png"))


def test_pack_tool_refuses_an_existing_destination(tmp_path):
    src = make_source(tmp_path, game_files(), link=False)
    dest = tmp_path / "there"
    dest.mkdir()
    with pytest.raises(pack_game.PackError):
        pack_game.pack(src, str(dest), 1, "none")


class Src:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n=-1):
        n = len(self.data) - self.pos if n is None or n < 0 else n
        out = self.data[self.pos:self.pos + n]
        self.pos += len(out)
        return out


def tar_bytes(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for ti, data in members:
            tar.addfile(ti, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


@needs_zstd
def test_zstd_stream_extracts_a_part(env, tmp_path, monkeypatch):
    monkeypatch.setattr(os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    from compression import zstd
    lib = env.make(start=False)
    raw = tar_bytes([(info("d", tarfile.DIRTYPE), None), (info("d/f.bin", size=5), b"hello")])
    folder = str(tmp_path / "out")
    os.mkdir(folder)
    reader = rl._ZstdStream(Src(zstd.compress(raw) * 1))
    count, extracted = lib._extract(folder, reader)
    while reader.read(1 << 20):
        pass
    assert (count, extracted) == (1, 5)
    with open(os.path.join(folder, "d", "f.bin"), "rb") as f:
        assert f.read() == b"hello"


@needs_zstd
@pytest.mark.parametrize("damage", ["trailing", "truncated", "garbage"])
def test_zstd_stream_rejects_a_damaged_part(env, tmp_path, damage, monkeypatch):
    monkeypatch.setattr(os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    from compression import zstd
    lib = env.make(start=False)
    frame = zstd.compress(tar_bytes([(info("f", size=5), b"hello")]))
    data = {"trailing": frame + b"x", "truncated": frame[:-6], "garbage": b"not zstd at all"}[damage]
    folder = str(tmp_path / "out")
    os.mkdir(folder)
    reader = rl._ZstdStream(Src(data))
    with pytest.raises(rl._BadPart):
        lib._extract(folder, reader)
        while reader.read(1 << 20):
            pass
