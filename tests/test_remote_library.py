"""Remote library download engine (issue #47) against a local HTTP server with Range and
Bearer, built like the reference server (docs/remote-library.md)."""
import hashlib
import http.server
import json
import os
import shutil
import socket
import stat
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from insertplay_decky import remote_library as rl  # noqa: E402

TOKEN = "test-token-0123456789abcdef"
GID = "fa41e0e7-0133-4f21-b5f4-1a143a2f52ef"
OTHER = "b7898be4-146a-42c2-8dec-a8797a259379"
BIG = 3 * 1024 * 1024 + 123
MANIFEST = {
    "schemaVersion": 1,
    "shortcut": {
        "appName": "Jogo Teste",
        "launch": {"exe": "/bin/bash", "args": '"{SD_ROOT}/insertplay-start.sh"', "startDir": "{SD_ROOT}"},
        "artwork": {"capsule": "steam-artwork/capsule.png"},
    },
    "game": {"platform": "ps1", "emulator": "duckstation"},
}


# ---------------------------------------------------------------- server


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True  # like Caddy (Go sets TCP_NODELAY); else ~40 ms per small file

    def log_message(self, *args):
        pass

    def do_GET(self):
        srv = self.server
        srv.connections.add(self.client_address)
        if self.headers.get("Authorization") != f"Bearer {srv.token}":
            return self._send(401, b"unauthorized")
        path = self.path.split("?", 1)[0]
        srv.requests.append((path, self.headers.get("Range")))
        if path in srv.status:
            return self._send(srv.status[path], b"nope")
        local = os.path.join(srv.root, *[p for p in __import__("urllib.parse").parse.unquote(path).split("/") if p])
        if ".." in path or not os.path.isfile(local):
            return self._send(404, b"not found")
        with open(local, "rb") as f:
            data = f.read()
        if srv.corrupt.get(path, 0) > 0:
            srv.corrupt[path] -= 1
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                return self._send(416, b"")
        body = data[start:]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        drop = srv.drop_after.pop(path, None)
        if drop is not None:
            self.wfile.write(body[:drop])
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            return
        step = 64 * 1024
        for i in range(0, len(body), step):
            self.wfile.write(body[i:i + step])
            if srv.slow:
                time.sleep(srv.slow)

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    srv.root = str(tmp_path / "server")
    srv.token = TOKEN
    srv.requests, srv.connections = [], set()
    srv.status, srv.corrupt, srv.drop_after = {}, {}, {}
    srv.slow = 0
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}/"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


# ---------------------------------------------------------------- library builder (indexer stand-in)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def game_files():
    big = os.urandom(BIG)
    return {
        "steam-shortcut.json": json.dumps(MANIFEST).encode(),
        "shortcut-identity.json": json.dumps({"guid": GID}).encode(),
        "insertplay-start.sh": b"#!/bin/bash\necho start\n",
        "bin/tool": b"\x7fELF" + os.urandom(1000),
        "data/big.bin": big,
        "data/empty.txt": b"",
        "steam-artwork/capsule.png": b"\x89PNG fake capsule",
    }


def publish(srv, files, gid=GID, dirs=("saves",), links=({"path": "data/link.bin", "target": "big.bin"},),
            edit_list=None, edit_index=None, folder="jogo-teste"):
    """Writes the game folder, its file list and index.json the way the indexer does."""
    lib = os.path.join(srv.root, "library", folder)
    for rel, data in files.items():
        path = os.path.join(lib, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    entries = [
        {"path": p, "size": len(d), "sha256": _sha(d), "executable": d[:4] == b"\x7fELF" or d[:2] == b"#!"}
        for p, d in sorted(files.items())
    ]
    dirs, links = list(dirs), [dict(l) for l in links]
    version = _sha(json.dumps({"files": entries, "dirs": dirs, "links": links}, sort_keys=True,
                              separators=(",", ":"), ensure_ascii=False).encode())[:16]
    listing = {"schemaVersion": 1, "id": gid, "version": version, "files": entries, "dirs": dirs, "links": links}
    if edit_list:
        edit_list(listing)
    raw = json.dumps(listing).encode()
    os.makedirs(os.path.join(srv.root, "lists"), exist_ok=True)
    with open(os.path.join(srv.root, "lists", f"{gid}.json"), "wb") as f:
        f.write(raw)
    capsule = files.get("steam-artwork/capsule.png")
    index = {
        "schemaVersion": 1,
        "generatedAt": "2026-11-13T21:00:00Z",
        "library": "library/",
        "games": [
            {
                "id": gid,
                "version": version,
                "path": folder,
                "name": "Jogo Teste",
                "totalSize": sum(e["size"] for e in listing["files"]),
                "fileCount": len(listing["files"]),
                "files": f"lists/{gid}.json",
                "filesSha256": _sha(raw),
                "manifest": MANIFEST,
                "artwork": {"capsule": {"path": "steam-artwork/capsule.png", "size": len(capsule), "sha256": _sha(capsule)}}
                if capsule else {},
            }
        ],
    }
    if edit_index:
        edit_index(index)
    with open(os.path.join(srv.root, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f)
    return listing


# ---------------------------------------------------------------- engine helpers


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.games_root = str(tmp_path / "home" / "Games" / "InsertPlay")
        self.data_dir = str(tmp_path / "data" / "library")
        self.config_path = str(tmp_path / "settings" / "remote_library.json")
        self.logs, self.events = [], []

    def make(self, start=True):
        lib = rl.RemoteLibrary(self.games_root, self.data_dir, self.config_path,
                               log=self.logs.append, on_change=self.events.append)
        lib.backoff = (0.01, 0.01, 0.01)
        if start:
            lib.start()
        return lib


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    for lib in getattr(e, "_libs", []):
        lib.stop()


def status(lib, gid=GID):
    return next(g for g in lib.games()["games"] if g["id"] == gid)


def wait_state(lib, states, gid=GID, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = status(lib, gid)
        if st["state"] in states:
            return st
        time.sleep(0.02)
    raise AssertionError(f"state stuck at {status(lib, gid)}")


def ready(env, server, start=True):
    lib = env.make(start)
    assert lib.set_config(server.url, TOKEN)["ok"]
    result = lib.refresh()
    assert result["ok"], result
    return lib


def file_requests(server, rel):
    return [r for r in server.requests if r[0].endswith("/" + rel)]


# ---------------------------------------------------------------- tests


def test_install_downloads_verifies_and_installs(env, server):
    files = game_files()
    publish(server, files)
    lib = ready(env, server)
    try:
        st = status(lib)
        assert st["state"] == "not_installed" and st["inIndex"] and st["bytesTotal"] == sum(map(len, files.values()))
        root = os.path.join(env.games_root, GID)
        assert st["shortcut"]["launchOptions"] == f'"{root}/insertplay-start.sh" --guid {GID}'
        assert st["shortcut"]["startDir"] == root
        assert st["shortcut"]["artwork"]["capsule"].endswith(f"artwork/{GID}/capsule.png")
        assert st["shortcut"]["game"]["platform"] == "ps1"
        assert lib.artwork_base64(GID, "capsule")
        assert lib.artwork_base64("../x", "capsule") == "" and lib.artwork_base64(GID, "evil") == ""

        result = lib.install(GID)
        assert result["ok"] and result["game"]["state"] in ("queued", "downloading")
        assert lib.install(GID)["ok"]  # a second press is harmless
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "installed", st
        assert st["percent"] == 100 and st["installedVersion"] == st["version"] and not st["updateAvailable"]

        for rel, data in files.items():
            path = os.path.join(root, *rel.split("/"))
            with open(path, "rb") as f:
                assert f.read() == data, rel
            mode = stat.S_IMODE(os.lstat(path).st_mode)
            expect = 0o755 if rel in ("insertplay-start.sh", "bin/tool") else 0o644
            assert mode == expect, (rel, oct(mode))
        assert os.path.isdir(os.path.join(root, "saves"))
        link = os.path.join(root, "data", "link.bin")
        assert os.path.islink(link) and os.readlink(link) == "big.bin"
        assert not os.path.exists(os.path.join(env.games_root, ".incoming", GID))
        assert not any(name.endswith(".part") for _, _, names in os.walk(root) for name in names)
        # one persistent connection for all the files (plus the refresh's own)
        assert len(server.connections) <= 3
        assert any(e["games"] == [] or e["active"] is None for e in env.events)
        assert lib.install(GID)["code"] == "invalid_state"
    finally:
        lib.stop()


def test_resumes_a_part_file_with_range(env, server):
    files = game_files()
    publish(server, files)
    lib = ready(env, server, start=False)
    part = os.path.join(env.games_root, ".incoming", GID, "data", "big.bin.part")
    os.makedirs(os.path.dirname(part))
    with open(part, "wb") as f:
        f.write(files["data/big.bin"][:1048581])
    lib.start()
    try:
        assert lib.install(GID)["ok"]
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        assert file_requests(server, "data/big.bin") == [("/library/jogo-teste/data/big.bin", "bytes=1048581-")]
    finally:
        lib.stop()


def test_dropped_connection_resumes_where_it_stopped(env, server):
    files = game_files()
    publish(server, files)
    server.drop_after["/library/jogo-teste/data/big.bin"] = 1500000
    lib = ready(env, server)
    try:
        lib.install(GID)
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        reqs = file_requests(server, "data/big.bin")
        assert reqs[0][1] is None and reqs[-1][1] is not None and len(reqs) == 2
        with open(os.path.join(env.games_root, GID, "data", "big.bin"), "rb") as f:
            assert f.read() == files["data/big.bin"]
    finally:
        lib.stop()


def test_wrong_hash_downloads_again_once(env, server):
    publish(server, game_files())
    server.corrupt["/library/jogo-teste/data/big.bin"] = 1
    lib = ready(env, server)
    try:
        lib.install(GID)
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        assert len(file_requests(server, "data/big.bin")) == 2
    finally:
        lib.stop()


def test_second_hash_mismatch_is_an_error_and_never_installs(env, server):
    publish(server, game_files())
    server.corrupt["/library/jogo-teste/data/big.bin"] = 99
    lib = ready(env, server)
    try:
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "error" and st["error"]["code"] == "hash_mismatch"
        assert not os.path.exists(os.path.join(env.games_root, GID))
        # verified files are kept for a retry
        assert os.path.isfile(os.path.join(env.games_root, ".incoming", GID, "bin", "tool"))
        assert lib.cancel(GID)["game"]["state"] == "not_installed"
        assert not os.path.exists(os.path.join(env.games_root, ".incoming", GID))
    finally:
        lib.stop()


def test_insufficient_space_downloads_nothing(env, server, monkeypatch):
    publish(server, game_files())
    lib = ready(env, server)
    before = len(server.requests)  # the refresh fetched the index and the artwork
    try:
        monkeypatch.setattr(rl, "free_bytes", lambda path: 1000)
        result = lib.install(GID)
        assert result["ok"] is False and result["code"] == "no_space"
        assert result["free"] == 1000 and result["needed"] > rl.SPACE_MARGIN
        assert "GB" in result["message"]
        time.sleep(0.2)
        assert status(lib)["state"] == "not_installed"
        assert server.requests[before:] == []
    finally:
        lib.stop()


def _bad_file(path):
    def edit(listing):
        listing["files"][0]["path"] = path
    return edit


@pytest.mark.parametrize(
    "edit",
    [
        _bad_file("../evil"),
        _bad_file("/tmp/evil"),
        _bad_file("a\\b"),
        _bad_file("a\x00b"),
        _bad_file("a/./b"),
        _bad_file("a//b"),
        _bad_file("data/evil.part"),
        _bad_file("x" * 256),
        lambda l: l["dirs"].append("../outside"),
        lambda l: l["links"].append({"path": "esc", "target": "../../../outside"}),
        lambda l: l["links"].append({"path": "abs", "target": "/etc"}),
        lambda l: l["links"].append({"path": "bin/tool/x", "target": "y"}),  # under a file
        lambda l: l["dirs"].append("data/big.bin"),  # duplicate path
    ],
)
def test_malicious_paths_refuse_the_whole_game(env, server, edit):
    publish(server, game_files(), edit_list=edit)
    lib = ready(env, server)
    before = len(server.requests)
    try:
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "error" and st["error"]["code"] == "bad_path", st
        assert not os.path.exists(os.path.join(env.games_root, GID))
        assert not any(r[0].startswith("/library/") for r in server.requests[before:])
        assert not (env.tmp / "evil").exists() and not (env.tmp / "outside").exists()
    finally:
        lib.stop()


def test_link_that_escapes_through_another_link_is_refused(env, server):
    # x -> . and sub/y -> ../x/.. both normalize inside the folder, but y resolves to its parent
    links = ({"path": "x", "target": "."}, {"path": "sub/y", "target": "../x/.."})
    publish(server, game_files(), links=links)
    lib = ready(env, server)
    try:
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "error" and st["error"]["code"] == "bad_path"
        assert not os.path.exists(os.path.join(env.games_root, GID))
    finally:
        lib.stop()


@pytest.mark.parametrize(
    "edit, dropped",
    [
        (lambda i: i.update(schemaVersion=2), None),
        (lambda i: i.update(library="http://evil.example/library/"), None),
        (lambda i: i.update(library="../library/"), None),
        (lambda i: i["games"][0].update(files="//evil.example/x.json"), None),
        (lambda i: i["games"][0].update(files="lists/%2e%2e/x.json"), None),
        (lambda i: i["games"][0].update(id="../../etc"), True),
        (lambda i: i["games"][0].update(id=GID.upper()), True),
        (lambda i: i["games"][0].update(id="00000000-0000-0000-0000-000000000000"), True),
        (lambda i: i["games"][0].update(path="a/b"), True),
        (lambda i: i["games"].append(dict(i["games"][0])), True),  # duplicate id
        (lambda i: i["games"][0].pop("files"), True),
        (lambda i: i["games"][0].update(id=["not", "hashable"]), True),
    ],
)
def test_bad_index(env, server, edit, dropped):
    publish(server, game_files(), edit_index=edit)
    lib = env.make()
    try:
        lib.set_config(server.url, TOKEN)
        result = lib.refresh()
        if dropped:
            assert result["ok"] and result["games"] == []
            assert any("dropped" in line for line in env.logs)
        else:
            assert result["ok"] is False and result["code"] == "bad_index"
    finally:
        lib.stop()


def test_token_missing_or_wrong(env, server):
    publish(server, game_files())
    lib = env.make()
    try:
        assert lib.refresh()["code"] == "not_configured"
        assert lib.install(GID)["code"] == "not_configured"
        lib.set_config(server.url, "wrong-token")
        result = lib.refresh()
        assert result["ok"] is False and result["code"] == "unauthorized"
        assert lib.games()["games"] == []

        lib.set_config(None, TOKEN)
        assert lib.refresh()["ok"]
        lib.set_config(None, "another-wrong-token")
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "error" and st["error"]["code"] == "unauthorized"
        assert lib.refresh()["code"] == "unauthorized" and lib.games()["stale"] is True
    finally:
        lib.stop()
    everything = json.dumps([env.logs, [str(e) for e in env.events]])
    assert TOKEN not in everything and "wrong-token" not in everything
    for name in ("state.json", "index.json"):
        with open(os.path.join(env.data_dir, name), encoding="utf-8") as f:
            text = f.read()
            assert TOKEN not in text and "wrong-token" not in text


def test_server_errors_retry_then_unreachable(env, server):
    publish(server, game_files())
    lib = ready(env, server)
    try:
        server.status["/library/jogo-teste/data/big.bin"] = 503
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "error" and st["error"]["code"] == "unreachable"
        assert len(file_requests(server, "data/big.bin")) == 4  # 1 + 3 retries (backoff patched)
        server.status.clear()
        assert lib.resume(GID)["ok"]
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
    finally:
        lib.stop()


def test_404_on_a_file_means_the_server_changed(env, server):
    publish(server, game_files())
    lib = ready(env, server)
    try:
        server.status["/library/jogo-teste/bin/tool"] = 404
        lib.install(GID)
        st = wait_state(lib, ("installed", "error"))
        assert st["error"]["code"] == "server_changed"
    finally:
        lib.stop()


def test_uninstall_deletes_only_the_game_folder(env, server):
    publish(server, game_files())
    lib = ready(env, server)
    try:
        assert lib.uninstall(GID)["code"] == "unknown_game"
        lib.install(GID)
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        sibling = os.path.join(env.games_root, "keep-me")
        os.makedirs(sibling)
        result = lib.uninstall(GID)
        assert result["ok"] and result["game"]["state"] == "not_installed"
        assert not os.path.exists(os.path.join(env.games_root, GID))
        assert os.path.isdir(sibling)
        assert not os.path.exists(os.path.join(env.data_dir, "lists", f"{GID}.json"))
        assert lib.artwork_base64(GID, "capsule")  # still in the index: artwork stays
        assert lib.uninstall("../keep-me")["code"] == "unknown_game"
    finally:
        lib.stop()


def test_uninstall_refuses_a_symlinked_game_folder(env, server, tmp_path):
    publish(server, game_files())
    lib = ready(env, server)
    try:
        lib.install(GID)
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        real = os.path.join(env.games_root, GID)
        outside = tmp_path / "outside"
        os.rename(real, outside)
        os.symlink(outside, real)
        result = lib.uninstall(GID)
        assert result["ok"] is False and result["code"] == "io_error"
        assert (outside / "insertplay-start.sh").is_file()
    finally:
        lib.stop()


def test_existing_folder_not_installed_by_the_plugin_is_never_touched(env, server):
    publish(server, game_files())
    lib = ready(env, server)
    try:
        mine = os.path.join(env.games_root, GID)
        os.makedirs(mine)
        open(os.path.join(mine, "user-file"), "w").close()
        result = lib.install(GID)
        assert result["code"] == "io_error"
        assert os.path.isfile(os.path.join(mine, "user-file"))
    finally:
        lib.stop()


def test_state_reload_resumes_queued_and_keeps_paused(env, server):
    publish(server, game_files())
    first = ready(env, server, start=False)  # worker not running: the game stays queued
    assert first.install(GID)["game"]["state"] == "queued"
    with open(os.path.join(env.data_dir, "state.json"), encoding="utf-8") as f:
        assert json.load(f)["queue"] == [GID]

    second = env.make()
    try:
        assert wait_state(second, ("installed", "error"))["state"] == "installed"
    finally:
        second.stop()

    # a paused game stays paused after a reload
    third = env.make(start=False)
    third._load()
    third._games[OTHER] = {**third._games[GID], "state": "paused", "installedVersion": None}
    third._save(force=True)
    fourth = env.make()
    try:
        time.sleep(0.2)
        assert fourth._games[OTHER]["state"] == "paused"
        assert fourth.progress()["active"] is None
    finally:
        fourth.stop()


def test_pause_resume_and_cancel_mid_download(env, server):
    publish(server, game_files())
    server.slow = 0.03
    lib = ready(env, server)
    try:
        lib.install(GID)
        deadline = time.time() + 10
        while status(lib)["bytesDone"] < 200000 and time.time() < deadline:
            time.sleep(0.01)
        paused = lib.pause(GID)
        assert paused["ok"] and paused["game"]["state"] == "paused"
        time.sleep(0.5)
        st = status(lib)
        assert st["state"] == "paused" and 0 < st["percent"] < 100 and st["speed"] == 0
        assert lib.progress()["active"] is None
        assert lib.pause(GID)["code"] == "invalid_state"

        server.slow = 0
        assert lib.resume(GID)["ok"]
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        assert any(r[1] for r in file_requests(server, "data/big.bin"))  # resumed with Range
        assert lib.uninstall(GID)["ok"]

        server.slow = 0.03
        lib.install(GID)
        while status(lib)["bytesDone"] < 200000 and time.time() < deadline + 10:
            time.sleep(0.01)
        cancelled = lib.cancel(GID)
        assert cancelled["ok"] and cancelled["game"]["state"] == "not_installed"
        assert not os.path.exists(os.path.join(env.games_root, ".incoming", GID))
        assert lib.progress() == {"active": None, "queue": [], "games": []}
    finally:
        lib.stop()


def test_path_rules():
    assert rl._path_ok("a/b.txt") and rl._path_ok("ç/ã.bin")
    for bad in ("", "/a", "a/", "a//b", ".", "a/..", "a\\b", "a\x1fb", "a\x7f", "x.part", "x" * 256, "a/" * 2049,
                "\udcff"):
        assert not rl._path_ok(bad), bad
    assert not rl._path_ok("a/b", single=True)
    assert rl._target_ok("a/b", "../c") and rl._target_ok("a/b", "c")
    assert not rl._target_ok("a/b", "../../c") and not rl._target_ok("a", "/c") and not rl._target_ok("a", "")
    # empty segments: allowed by the contract (character rules only) and kept by the indexer
    assert rl._target_ok("a/b", "c//d") and rl._target_ok("a/b", "c/") and rl._target_ok("a/b", "./c")
    assert not rl._target_ok("a", "x//..//..")


def test_config_validation_and_privacy(env):
    lib = env.make(start=False)
    for url in ("ftp://host/", "http://user:pw@host/", "http://host/?a=1", "http://host/#x", "host:8080",
                "http://ho st/"):
        assert lib.set_config(url, TOKEN)["code"] == "bad_config", url
    assert lib.set_config("http://host:8080/x", "bad token")["code"] == "bad_config"
    out = lib.set_config("  http://192.168.1.83:8080  ", TOKEN)
    assert out == {"ok": True, "url": "http://192.168.1.83:8080/", "tokenSet": True}
    assert stat.S_IMODE(os.stat(env.config_path).st_mode) == 0o600
    assert lib.get_config() == {"url": "http://192.168.1.83:8080/", "tokenSet": True}
    assert lib.set_config("http://192.168.1.83:8080/", None)["tokenSet"] is True  # null keeps
    assert lib.set_config("http://192.168.1.83:8080/", "")["tokenSet"] is False
    assert lib.set_config("", None) == {"ok": True, "url": None, "tokenSet": False}


def test_leftover_incoming_folder_unknown_to_the_state_is_never_reused(env, server):
    """A final name in .incoming counts as verified. Files left there by an install the
    state no longer knows (state lost, a cancel that timed out) must not skip the hash."""
    files = game_files()
    publish(server, files)
    lib = ready(env, server, start=False)
    stale = os.path.join(env.games_root, ".incoming", GID, "data", "big.bin")
    os.makedirs(os.path.dirname(stale))
    with open(stale, "wb") as f:
        f.write(b"z" * len(files["data/big.bin"]))  # same size, other content
    lib.start()
    try:
        assert lib.install(GID)["ok"]
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
        with open(os.path.join(env.games_root, GID, "data", "big.bin"), "rb") as f:
            assert f.read() == files["data/big.bin"]
    finally:
        lib.stop()


INDEXER = os.path.join(os.path.dirname(__file__), "..", "server", "indexer", "remote_library_index.py")


@pytest.mark.skipif(not os.path.isfile(INDEXER), reason="server/indexer not in this checkout")
def test_indexer_output_installs_with_the_engine(env, server, tmp_path):
    """Contract check between #44 and #47: the real indexer builds index.json and the file
    list from a library folder, the test server serves them, the engine installs the game."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("remote_library_index", INDEXER)
    indexer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(indexer)

    files = game_files()
    files["data/sub dir/ação #1 [x] 100%.txt"] = "ção".encode()
    files["data/%2e"] = b"literal name"
    game = os.path.join(server.root, "library", "jogo-teste")
    for rel, data in files.items():
        path = os.path.join(game, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    os.makedirs(os.path.join(game, "saves", "memcards"))
    os.symlink("big.bin", os.path.join(game, "data", "link.bin"))
    os.symlink("data//", os.path.join(game, "data-dir"))
    out = str(tmp_path / "index-out")  # the indexer refuses an output folder around the library
    result = indexer.build_index(os.path.join(server.root, "library"), out, use_cache=False)
    assert [r["entry"]["id"] for r in result["games"]] == [GID] and result["skipped"] == []
    shutil.copy(os.path.join(out, "index.json"), server.root)
    shutil.copytree(os.path.join(out, "lists"), os.path.join(server.root, "lists"))

    lib = ready(env, server)
    try:
        st = status(lib)
        assert st["bytesTotal"] == sum(map(len, files.values())) and st["fileCount"] == len(files)
        assert lib.artwork_base64(GID, "capsule")
        assert lib.install(GID)["ok"]
        st = wait_state(lib, ("installed", "error"))
        assert st["state"] == "installed", st
        root = os.path.join(env.games_root, GID)
        for rel, data in files.items():
            path = os.path.join(root, *rel.split("/"))
            with open(path, "rb") as f:
                assert f.read() == data, rel
            expect = 0o755 if rel in ("insertplay-start.sh", "bin/tool") else 0o644
            assert stat.S_IMODE(os.lstat(path).st_mode) == expect, rel
        assert os.path.isdir(os.path.join(root, "saves", "memcards"))
        assert os.readlink(os.path.join(root, "data", "link.bin")) == "big.bin"
        assert os.readlink(os.path.join(root, "data-dir")) == "data//"
    finally:
        lib.stop()


def test_stop_before_start_keeps_the_saved_state(env, server):
    """Decky can unload the plugin before _main started the engine: stop() must not save
    an empty state over the installed games."""
    publish(server, game_files())
    lib = ready(env, server)
    try:
        assert lib.install(GID)["ok"]
        assert wait_state(lib, ("installed", "error"))["state"] == "installed"
    finally:
        lib.stop()
    with open(os.path.join(env.data_dir, "state.json"), encoding="utf-8") as f:
        state = json.load(f)
    state["queue"] = 7  # a damaged queue doesn't stop the load either
    with open(os.path.join(env.data_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f)
    env.make(start=False).stop()
    again = env.make()
    try:
        assert status(again)["state"] == "installed"
    finally:
        again.stop()
