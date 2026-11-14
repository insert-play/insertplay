"""Save server (issue #63): server/saves/saves_server.py in process."""
import http.client
import json
import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "saves"))

import saves_server as ss  # noqa: E402

TOKEN = "t" * 40
KEY = "fa41e0e7-0133-4f21-b5f4-1a143a2f52ef"
H1 = "a" * 64
H2 = "b" * 64


@pytest.fixture
def server(tmp_path):
    store = ss.Store(str(tmp_path), backup_limit=1000, max_size=5000)
    srv = ss.Server(("127.0.0.1", 0), TOKEN, store)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=b"", headers=None, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    h = {"Authorization": f"Bearer {token}"} if token else {}
    h.update(headers or {})
    if method in ("PUT", "POST"):
        h.setdefault("Content-Length", str(len(body)))
    conn.request(method, path, body=body if method in ("PUT", "POST") else None, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        parsed = json.loads(data) if data else None
    except ValueError:
        parsed = data
    return resp.status, parsed, data


def put(srv, body, rev=None, digest=H1, device="deck", backup=True, mtime="100.5"):
    headers = {
        "X-Save-Hash": digest,
        "X-Save-Device": device,
        "X-Save-Newest-Mtime": mtime,
        "X-Save-Backup": "1" if backup else "0",
    }
    if rev is None:
        headers["If-None-Match"] = "*"
    else:
        headers["If-Match"] = rev
    return call(srv, "PUT", f"/saves/{KEY}/current.tar.gz", body, headers)


def test_token_checked_first(server):
    assert call(server, "GET", f"/saves/{KEY}/meta", token=None)[0] == 401
    assert call(server, "GET", f"/saves/{KEY}/meta", token="x" * 40)[0] == 401
    assert call(server, "GET", "/saves/not-a-key/meta", token=None)[0] == 401


def test_no_save_yet(server):
    assert call(server, "GET", f"/saves/{KEY}/meta")[0] == 404
    assert call(server, "GET", f"/saves/{KEY}/current.tar.gz")[0] == 404
    assert call(server, "GET", f"/saves/{KEY}/strategy.json")[0] == 404
    status, body, _ = call(server, "GET", f"/saves/{KEY}/backups")
    assert status == 200 and body == {"backups": [], "limit": 1000}


@pytest.mark.parametrize(
    "path",
    [
        "/saves/FA41E0E7-0133-4F21-B5F4-1A143A2F52EF/meta",
        f"/saves/{KEY}/../{KEY}/meta",
        f"/saves/{KEY}/%2e%2e/meta",
        f"/saves/{KEY}/meta?x=1",
        f"/saves/{KEY}//meta",
        f"/saves/{KEY}/other",
        f"/saves/{KEY}/backups/../meta.json",
        f"/saves/{KEY}/backups/meta.json",
        "/saves/steam-/meta",
        "/saves/",
        "/index.json",
    ],
)
def test_bad_paths(server, path):
    assert call(server, "GET", path)[0] == 404


def test_steam_key_accepted(server):
    assert call(server, "GET", "/saves/steam-12200/meta")[0] == 404  # valid key, no save
    status, body, _ = call(
        server,
        "PUT",
        "/saves/steam-12200/current.tar.gz",
        b"x",
        {"If-None-Match": "*", "X-Save-Hash": H1, "X-Save-Device": "deck", "X-Save-Newest-Mtime": "1"},
    )
    assert status == 201 and body["meta"]["rev"] == "1"


def test_first_put_then_get(server, tmp_path):
    status, body, _ = put(server, b"save-1")
    assert status == 201
    meta = body["meta"]
    assert meta["rev"] == "1" and meta["hash"] == H1 and meta["device"] == "deck"
    assert meta["newestMtime"] == 100.5 and meta["size"] == 6
    assert body["backup"] is None
    assert call(server, "GET", f"/saves/{KEY}/meta")[1] == meta
    assert call(server, "GET", f"/saves/{KEY}/current.tar.gz")[2] == b"save-1"
    # nothing half-written left behind
    assert sorted(os.listdir(tmp_path / KEY)) == ["current.tar.gz", "meta.json"]


def test_put_needs_precondition(server):
    headers = {"X-Save-Hash": H1, "X-Save-Device": "deck", "X-Save-Newest-Mtime": "1"}
    assert call(server, "PUT", f"/saves/{KEY}/current.tar.gz", b"x", headers)[0] == 428


def test_rev_conflict(server):
    put(server, b"save-1")
    # a second "first upload" from the other device
    status, body, _ = put(server, b"other", device="machine")
    assert status == 409 and body["meta"]["rev"] == "1"
    # a stale rev
    put(server, b"save-2", rev="1", digest=H2)
    status, body, _ = put(server, b"stale", rev="1", device="machine")
    assert status == 409 and body["meta"]["rev"] == "2" and body["meta"]["hash"] == H2
    assert call(server, "GET", f"/saves/{KEY}/current.tar.gz")[2] == b"save-2"
    # If-Match on a key without a save
    status, body, _ = call(
        server,
        "PUT",
        "/saves/steam-1/current.tar.gz",
        b"x",
        {"If-Match": "1", "X-Save-Hash": H1, "X-Save-Device": "deck", "X-Save-Newest-Mtime": "1"},
    )
    assert status == 409 and body["meta"] is None


def test_replaced_save_becomes_backup(server):
    put(server, b"from-deck", device="deck")
    status, body, _ = put(server, b"from-machine", rev="1", digest=H2, device="machine")
    assert status == 200 and body["meta"]["rev"] == "2"
    assert body["backup"]["stored"] is True
    # the backup is named after the device that made the replaced save
    assert body["backup"]["name"].endswith("-deck.tar.gz")
    listing = call(server, "GET", f"/saves/{KEY}/backups")[1]["backups"]
    assert [b["name"] for b in listing] == [body["backup"]["name"]]
    assert call(server, "GET", f"/saves/{KEY}/backups/{listing[0]['name']}")[2] == b"from-deck"


def test_backup_off(server):
    put(server, b"one")
    status, body, _ = put(server, b"two", rev="1", backup=False)
    assert status == 200 and body["backup"] is None
    assert call(server, "GET", f"/saves/{KEY}/backups")[1]["backups"] == []


def test_backup_limit_trims_oldest(server):
    rev = None
    for i in range(5):
        status, body, _ = put(server, bytes([65 + i]) * 400, rev=rev, device=f"d{i}")
        rev = body["meta"]["rev"]
    backups = call(server, "GET", f"/saves/{KEY}/backups")[1]["backups"]
    # limit 1000 bytes, 400 each: the two newest stay (d3, d2)
    assert [b["name"].rsplit("-", 1)[1] for b in backups] == ["d3.tar.gz", "d2.tar.gz"]
    assert sum(b["size"] for b in backups) <= 1000


def test_save_larger_than_backup_limit_still_syncs(server):
    put(server, b"x" * 1500)
    status, body, _ = put(server, b"y" * 1500, rev="1", digest=H2)
    assert status == 200 and body["meta"]["rev"] == "2"
    assert body["backup"] == {"stored": False, "reason": "limit", "size": 1500}
    assert call(server, "GET", f"/saves/{KEY}/current.tar.gz")[2] == b"y" * 1500
    assert call(server, "GET", f"/saves/{KEY}/backups")[1]["backups"] == []


def test_post_backup(server):
    status, body, _ = call(server, "POST", f"/saves/{KEY}/backups", b"lost", {"X-Save-Device": "machine"})
    assert status == 200 and body["stored"] is True and body["name"].endswith("-machine.tar.gz")
    status, body, _ = call(server, "POST", f"/saves/{KEY}/backups", b"z" * 1001, {"X-Save-Device": "machine"})
    assert status == 200 and body == {"stored": False, "reason": "limit", "size": 1001}
    assert call(server, "POST", f"/saves/{KEY}/backups", b"x", {"X-Save-Device": "../x"})[0] == 400
    assert call(server, "POST", f"/saves/{KEY}/meta", b"x", {"X-Save-Device": "deck"})[0] == 405


def test_too_large(server):
    status, body, _ = put(server, b"x" * 5001)
    assert status == 413
    assert call(server, "GET", f"/saves/{KEY}/meta")[0] == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Save-Hash": "nothex", "X-Save-Device": "deck", "X-Save-Newest-Mtime": "1"},
        {"X-Save-Hash": H1, "X-Save-Device": "a/b", "X-Save-Newest-Mtime": "1"},
        {"X-Save-Hash": H1, "X-Save-Device": "deck", "X-Save-Newest-Mtime": "soon"},
        {"X-Save-Hash": H1, "X-Save-Device": "deck"},
    ],
)
def test_bad_upload_headers(server, headers):
    headers = {"If-None-Match": "*", **headers}
    assert call(server, "PUT", f"/saves/{KEY}/current.tar.gz", b"x", headers)[0] == 400


def test_incomplete_body(server, tmp_path):
    sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5)
    sock.sendall(
        (
            f"PUT /saves/{KEY}/current.tar.gz HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
            f"If-None-Match: *\r\nX-Save-Hash: {H1}\r\nX-Save-Device: deck\r\nX-Save-Newest-Mtime: 1\r\n"
            "Content-Length: 100\r\n\r\nonly-some"
        ).encode()
    )
    sock.shutdown(socket.SHUT_WR)
    reply = sock.recv(4096)
    sock.close()
    assert b" 400 " in reply.split(b"\r\n", 1)[0]
    assert call(server, "GET", f"/saves/{KEY}/meta")[0] == 404
    assert not [n for n in os.listdir(tmp_path / KEY) if n.endswith(".partial")]


def test_strategy(server):
    data = {"strategy": "proton-default", "exclude": ["AppData/Local/Remedy/AlanWake2/cache"], "inferred": True}
    status, body, _ = call(server, "PUT", f"/saves/{KEY}/strategy.json", json.dumps(data).encode())
    assert status == 200 and body == data
    assert call(server, "GET", f"/saves/{KEY}/strategy.json")[1] == data
    assert call(server, "PUT", f"/saves/{KEY}/strategy.json", b"[1]")[0] == 400
    assert call(server, "PUT", f"/saves/{KEY}/strategy.json", b"{nope")[0] == 400
    assert call(server, "PUT", f"/saves/{KEY}/strategy.json", b"{}")[0] == 400
    assert call(server, "PUT", f"/saves/{KEY}/strategy.json", b" " * (ss.STRATEGY_LIMIT + 1))[0] == 413


def test_head_meta(server):
    put(server, b"save")
    status, _, data = call(server, "HEAD", f"/saves/{KEY}/current.tar.gz")
    assert status == 200 and data == b""


def test_main_refuses_short_token(monkeypatch, tmp_path):
    monkeypatch.setenv("INSERTPLAY_TOKEN", "short")
    monkeypatch.setenv("INSERTPLAY_SAVES_DIR", str(tmp_path))
    assert ss.main() == 1
    monkeypatch.setenv("INSERTPLAY_TOKEN", TOKEN)
    monkeypatch.setenv("INSERTPLAY_SAVES_DIR", str(tmp_path / "missing"))
    assert ss.main() == 1
