"""Save sync client (issue #63) against the real save server (server/saves) in process.
Two "devices" = two homes, each with its own copy of the game folder."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))
sys.path.insert(0, os.path.join(HERE, "..", "server", "saves"))

import saves_server as ss  # noqa: E402
from insertplay_decky.saves import install, launch, snapshot, strategies, sync  # noqa: E402
from insertplay_decky.saves import client as http  # noqa: E402

TOKEN = "s" * 40
KEY = "b093444f-c500-4c96-9050-9176acd31dbb"
STEAMUSER = "prefix/pfx/drive_c/users/steamuser"
linux = pytest.mark.skipif(sys.platform == "win32", reason="flock, symlinks and bash need Linux")


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def server(tmp_path):
    store = ss.Store(str(tmp_path / "server"), backup_limit=50_000, max_size=10_000_000)
    os.makedirs(store.root)
    srv = ss.Server(("127.0.0.1", 0), TOKEN, store)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}/"
    yield srv
    srv.shutdown()
    srv.server_close()


def game(folder, manifest=None, guid=KEY):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "shortcut-identity.json"), "w") as f:
        json.dump({"guid": guid}, f)
    data = {"schemaVersion": 1, "shortcut": {"appName": "Jogo", "launch": {"exe": "/bin/bash"}}}
    data.update(manifest or {})
    with open(os.path.join(folder, "steam-shortcut.json"), "w") as f:
        json.dump(data, f)
    return str(folder)


def write(root, rel, data, mtime=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def read(root, rel):
    with open(os.path.join(root, rel), "rb") as f:
        return f.read()


class Device:
    def __init__(self, tmp_path, name, url, manifest=None, sync_on=True, backup=True):
        self.name = name
        self.paths = sync.Paths(str(tmp_path / name / "home"))
        self.root = game(tmp_path / name / "game", manifest)
        sync.write_config(self.paths, url, TOKEN, sync_on, backup, name)

    def pull(self):
        return sync.Sync(self.paths, KEY, self.root).pull()

    def push(self):
        return sync.Sync(self.paths, KEY, self.root).push()

    def state(self):
        return sync.load_state(self.paths, KEY)


CLAW = {"saves": {"strategy": "custom-path", "paths": ["game/CLAW.USR"]}}
TD6 = {"saves": {"strategy": "duckstation"}}


# ---------------------------------------------------------------- strategies


def test_strategy_defaults_and_overrides():
    spec = strategies.from_block({"strategy": "duckstation"}, "manifest")
    assert spec.patterns == ["duckstation/memcards"] and spec.exclude == []
    spec = strategies.from_block({"Strategy": "PCSX2", "Paths": ["PCSX2/memcards"]}, "manifest")
    assert spec.patterns == ["PCSX2/memcards"]
    spec = strategies.from_block({"strategy": "proton-default", "exclude": ["Documents/My Games/DiRT2/hardwaresettings"]}, "m")
    assert "AppData/Local/Temp" in spec.exclude and spec.exclude[-1] == "Documents/My Games/DiRT2/hardwaresettings"
    assert strategies.from_block({"strategy": "dolphin"}, "m").patterns == [
        "dolphin/User/GC",
        "dolphin/User/Wii/title/00010000/*/data",
    ]


@pytest.mark.parametrize(
    "block",
    [
        {"strategy": "custom-path"},
        {"strategy": "nope"},
        {"strategy": "custom-path", "paths": ["../escape"]},
        {"strategy": "custom-path", "paths": ["/abs"]},
        {"strategy": "custom-path", "paths": "game/x"},
        {"strategy": "duckstation", "exclude": "x"},
        "proton-default",
    ],
)
def test_bad_blocks(block):
    with pytest.raises(strategies.StrategyError):
        strategies.from_block(block, "manifest")


def test_infer_emulator_and_proton(tmp_path):
    root = game(tmp_path / "g", {"game": {"platform": "ps1", "emulator": "duckstation"}})
    with pytest.raises(strategies.StrategyError):
        strategies.infer(root, strategies.read_manifest(root))  # no memory card yet
    write(root, "duckstation/memcards/a.mcd", "x")
    assert strategies.infer(root, strategies.read_manifest(root)).strategy == "duckstation"

    aw2 = game(tmp_path / "aw2")
    os.makedirs(os.path.join(aw2, STEAMUSER))
    with pytest.raises(strategies.StrategyError):  # empty prefix: no confidence
        strategies.infer(aw2, strategies.read_manifest(aw2))
    write(aw2, f"{STEAMUSER}/AppData/Local/Temp/t.tmp", "x")
    with pytest.raises(strategies.StrategyError):  # only excluded files: still none
        strategies.infer(aw2, strategies.read_manifest(aw2))
    write(aw2, f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/1638/slot/save.dat", "x")
    spec = strategies.infer(aw2, strategies.read_manifest(aw2))
    assert (spec.strategy, spec.source) == ("proton-default", "inferred")

    with_game = game(tmp_path / "pc", {"game": {"platform": "pc"}})
    write(with_game, f"{STEAMUSER}/Documents/x", "x")
    with pytest.raises(strategies.StrategyError):  # a `game` block without a known emulator
        strategies.infer(with_game, strategies.read_manifest(with_game))


# ---------------------------------------------------------------- snapshot


@linux
def test_scan_skips_symlinks_and_exclusions(tmp_path):
    root = game(tmp_path / "g")
    spec = strategies.from_block({"strategy": "proton-default", "exclude": ["AppData/Local/Remedy/AlanWake2/cache"]}, "m")
    write(root, f"{STEAMUSER}/Documents/save.dat", "s")
    write(root, f"{STEAMUSER}/AppData/Local/Temp/x", "t")
    write(root, f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/cache/c.bin", "c")
    write(root, f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/1638/slot/s", "s")
    os.symlink("./Documents", os.path.join(root, STEAMUSER, "My Documents"))
    snap = snapshot.scan(root, spec)
    assert [f[0] for f in snap.files] == [
        f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/1638/slot/s",
        f"{STEAMUSER}/Documents/save.dat",
    ]
    assert snap.hash == snapshot.scan(root, spec).hash


@linux
def test_archive_restore_round_trip_keeps_device_files(tmp_path):
    spec = strategies.from_block({"strategy": "proton-default", "exclude": ["Documents/My Games/DiRT2/hardwaresettings"]}, "m")
    src = game(tmp_path / "a")
    write(src, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/profile", "deck-save", mtime=1000)
    write(src, f"{STEAMUSER}/Documents/My Games/DiRT2/hardwaresettings/hw.xml", "deck-hw")
    snap = snapshot.scan(src, spec)
    archive = str(tmp_path / "s.tgz")
    snapshot.write_archive(src, snap, archive)

    dest = game(tmp_path / "b")
    write(dest, f"{STEAMUSER}/Documents/My Games/DiRT2/hardwaresettings/hw.xml", "machine-hw")
    write(dest, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/old-only-here", "stale")
    os.symlink("./Documents", os.path.join(dest, STEAMUSER, "My Documents"))
    restored = snapshot.restore(dest, spec, archive, snap.hash)
    assert restored.hash == snap.hash
    assert read(dest, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/profile") == b"deck-save"
    assert os.path.getmtime(os.path.join(dest, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/profile")) == 1000
    assert read(dest, f"{STEAMUSER}/Documents/My Games/DiRT2/hardwaresettings/hw.xml") == b"machine-hw"
    assert not os.path.exists(os.path.join(dest, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/old-only-here"))
    assert os.path.islink(os.path.join(dest, STEAMUSER, "My Documents"))
    assert not os.path.exists(os.path.join(dest, snapshot.STAGING))


def test_restore_into_a_missing_prefix(tmp_path):
    spec = strategies.from_block({"strategy": "proton-default"}, "m")
    src = game(tmp_path / "a")
    write(src, f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/1638/aw2-savegame-slot-1/s.dat", "aw2")
    snap = snapshot.scan(src, spec)
    archive = str(tmp_path / "s.tgz")
    snapshot.write_archive(src, snap, archive)
    dest = game(tmp_path / "b")  # no prefix/ at all (Machine install, before the first launch)
    assert snapshot.restore(dest, spec, archive, snap.hash).hash == snap.hash


def _evil_archive(path, rel):
    import hashlib

    digest = hashlib.sha256(b"x").hexdigest()
    raw = json.dumps({"format": 1, "roots": [], "files": [[rel, 1, digest]], "hash": snapshot.list_hash([[rel, 1, digest]])}).encode()
    import io

    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(snapshot.MANIFEST)
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
        info = tarfile.TarInfo("files/" + rel)
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))


@pytest.mark.parametrize("rel", ["insertplay-start.sh", "game/OTHER.EXE", "../outside", "duckstation/settings.ini"])
def test_restore_refuses_paths_outside_the_save(tmp_path, rel):
    root = game(tmp_path / "g")
    archive = str(tmp_path / "evil.tgz")
    _evil_archive(archive, rel)
    spec = strategies.from_block({"strategy": "duckstation"}, "m")
    with pytest.raises((snapshot.SnapshotError, strategies.StrategyError)):
        snapshot.restore(root, spec, archive)
    assert not os.path.exists(os.path.join(root, "insertplay-start.sh"))


def test_restore_never_writes_an_excluded_path(tmp_path):
    root = game(tmp_path / "g")
    archive = str(tmp_path / "evil.tgz")
    _evil_archive(archive, f"{STEAMUSER}/AppData/Local/Temp/x")
    restored = snapshot.restore(root, strategies.from_block({"strategy": "proton-default"}, "m"), archive)
    assert restored.empty
    assert not os.path.exists(os.path.join(root, STEAMUSER, "AppData/Local/Temp/x"))


# ---------------------------------------------------------------- sync


def test_first_device_uploads_second_downloads(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    machine = Device(tmp_path, "machine", server.url, TD6)
    write(deck.root, "duckstation/memcards/td6.mcd", "kmh", mtime=2000)
    assert deck.pull()["result"] == "uploaded"
    r = machine.pull()
    assert r["result"] == "downloaded" and r["fromDevice"] == "deck"
    assert read(machine.root, "duckstation/memcards/td6.mcd") == b"kmh"
    assert machine.state()["lastLocalHash"] == deck.state()["lastLocalHash"] == deck.state()["lastServerHash"]
    assert machine.pull()["result"] == "unchanged"
    # Machine plays and saves; on close it uploads; the Deck gets it next launch
    write(machine.root, "duckstation/memcards/td6.mcd", "kmh+race", mtime=3000)
    assert machine.push()["result"] == "uploaded"
    assert deck.pull()["result"] == "downloaded"
    assert read(deck.root, "duckstation/memcards/td6.mcd") == b"kmh+race"
    # nothing new: the push after the game uploads nothing
    assert deck.push()["result"] == "unchanged"
    assert server.store.meta(KEY)["rev"] == "2"


def test_replaced_local_save_goes_to_backup(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, CLAW)
    machine = Device(tmp_path, "machine", server.url, CLAW)
    write(deck.root, "game/CLAW.USR", "world5", mtime=5000)
    deck.pull()
    machine.pull()
    write(machine.root, "game/CLAW.USR", "world6", mtime=6000)
    machine.push()  # server keeps the Deck's world5 as a backup (backup on)
    backups = server.store.backups(KEY)
    assert len(backups) == 1 and backups[0]["name"].endswith("-deck.tar.gz")


def test_conflict_newest_wins_and_loser_is_kept(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, CLAW, backup=False)
    machine = Device(tmp_path, "machine", server.url, CLAW, backup=False)
    write(machine.root, "game/CLAW.USR", "worlds1-4 (HD)", mtime=1000)
    write(deck.root, "game/CLAW.USR", "world5", mtime=5000)
    assert deck.pull()["result"] == "uploaded"
    # the Machine never synced and has another save: both changed, the Deck's is newer
    r = machine.pull()
    assert r["result"] == "conflict_downloaded" and r["backup"]["stored"] is True
    assert read(machine.root, "game/CLAW.USR") == b"world5"
    names = [b["name"] for b in server.store.backups(KEY)]
    assert len(names) == 1 and names[0].endswith("-machine.tar.gz")  # loser kept even with backups off


def test_conflict_local_newer_uploads_and_server_copy_is_kept(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, CLAW, backup=False)
    machine = Device(tmp_path, "machine", server.url, CLAW, backup=False)
    write(deck.root, "game/CLAW.USR", "old", mtime=1000)
    deck.pull()
    write(machine.root, "game/CLAW.USR", "newer", mtime=9000)
    r = machine.pull()
    assert r["result"] == "conflict_uploaded" and r["backup"]["stored"] is True
    assert deck.pull()["result"] == "downloaded" and read(deck.root, "game/CLAW.USR") == b"newer"


def test_push_after_the_other_device_uploaded(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, CLAW)
    machine = Device(tmp_path, "machine", server.url, CLAW)
    write(deck.root, "game/CLAW.USR", "a", mtime=1000)
    deck.pull()
    machine.pull()
    write(machine.root, "game/CLAW.USR", "machine", mtime=2000)
    machine.push()
    # the Deck played meanwhile (offline launch), newer: its push hits 409 and wins by time
    write(deck.root, "game/CLAW.USR", "deck", mtime=3000)
    r = deck.push()
    assert r["result"] == "conflict_uploaded"
    assert server.store.meta(KEY)["device"] == "deck"


def test_server_down_plays_local_and_uploads_later(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    write(deck.root, "duckstation/memcards/td6.mcd", "v1")
    assert deck.pull()["result"] == "uploaded"
    # a port nobody listens on
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = f"http://127.0.0.1:{s.getsockname()[1]}/"
    s.close()
    sync.write_config(deck.paths, dead, TOKEN, True, True, "deck")
    write(deck.root, "duckstation/memcards/td6.mcd", "v2")
    t = time.monotonic()
    assert deck.pull()["result"] == "offline"
    assert time.monotonic() - t < 15
    assert deck.push()["result"] == "offline"
    assert sync.pending_keys(deck.paths) == [KEY]
    sync.write_config(deck.paths, server.url, TOKEN, True, True, "deck")
    assert deck.push()["result"] == "uploaded"
    assert sync.pending_keys(deck.paths) == []
    assert server.store.meta(KEY)["rev"] == "2"


def test_sync_off_touches_nothing(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6, sync_on=False)
    write(deck.root, "duckstation/memcards/td6.mcd", "v1")
    assert deck.pull()["result"] == "disabled"
    assert deck.push()["result"] == "disabled"
    assert server.store.meta(KEY) is None and not os.listdir(server.store.root)


def test_wrong_card(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    game(deck.root, TD6, guid="11111111-2222-4333-8444-555555555555")
    assert deck.pull()["result"] == "wrong_card"


def test_inferred_strategy_goes_to_the_server_and_serves_the_other_device(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url)  # Alan Wake 2: no saves block
    machine = Device(tmp_path, "machine", server.url)
    write(deck.root, f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/1638/aw2-savegame-slot-1/s.dat", "aw2", mtime=4000)
    os.makedirs(os.path.join(machine.root, "prefix"))  # installed, prefix still empty
    r = deck.pull()
    assert (r["result"], r["strategy"], r["source"]) == ("uploaded", "proton-default", "inferred")
    stored = server.store.strategy(KEY)
    assert stored["strategy"] == "proton-default" and stored["inferred"] is True and stored["device"] == "deck"
    r = machine.pull()
    assert (r["result"], r["source"]) == ("downloaded", "server")
    assert read(machine.root, f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/1638/aw2-savegame-slot-1/s.dat") == b"aw2"
    # the per-game exclude edited on the server (AW2's renderer.ini) is honoured
    server.store.put_strategy(KEY, {**stored, "exclude": stored["exclude"] + ["AppData/Local/Remedy/AlanWake2/renderer.ini"]})
    write(machine.root, f"{STEAMUSER}/AppData/Local/Remedy/AlanWake2/renderer.ini", "4k")
    assert machine.push()["result"] == "unchanged"


def test_no_strategy_anywhere(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, {"game": {"platform": "pc"}})
    r = deck.pull()
    assert r["result"] == "no_strategy" and "no save strategy" in r["message"]


def test_cached_strategy_used_offline(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url)
    write(deck.root, f"{STEAMUSER}/Documents/x", "1")
    deck.pull()
    shutil.rmtree(os.path.join(deck.root, "prefix"))  # nothing left to infer from
    sync.write_config(deck.paths, "http://127.0.0.1:9/", TOKEN, True, True, "deck")
    r = deck.pull()
    assert (r["result"], r["strategy"]) == ("offline", "proton-default")


def test_large_save_without_backup_still_syncs(server, tmp_path):
    server.store.backup_limit = 10  # bytes
    deck = Device(tmp_path, "deck", server.url, CLAW)
    machine = Device(tmp_path, "machine", server.url, CLAW)
    write(deck.root, "game/CLAW.USR", os.urandom(4000), mtime=1000)
    deck.pull()
    write(machine.root, "game/CLAW.USR", os.urandom(4000), mtime=500)  # older: loses
    r = machine.pull()
    assert r["result"] == "conflict_downloaded" and r["backup"]["stored"] is False
    assert read(machine.root, "game/CLAW.USR") == read(deck.root, "game/CLAW.USR")
    assert server.store.backups(KEY) == []


def test_empty_local_save_never_uploads(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    assert deck.pull()["result"] == "empty"
    assert deck.push()["result"] == "empty"
    assert server.store.meta(KEY) is None


def test_unauthorized_is_an_error_not_offline(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    sync.write_config(deck.paths, server.url, "x" * 40, True, True, "deck")
    write(deck.root, "duckstation/memcards/a", "1")
    r = deck.pull()
    assert r["result"] == "error" and "401" in r["message"]


def test_last_result_and_config_are_private(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    write(deck.root, "duckstation/memcards/a", "1")
    deck.pull()
    last = sync.last_result(deck.paths, KEY)
    assert last["result"] == "uploaded" and last["device"] == "deck" and TOKEN not in json.dumps(last)
    if sys.platform != "win32":
        assert os.stat(deck.paths.config).st_mode & 0o077 == 0


# ---------------------------------------------------------------- lock, launch line, wrapper


@linux
def test_push_waits_for_the_game(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    write(deck.root, "duckstation/memcards/a", "1")
    lock = sync.Lock(deck.paths, KEY)
    assert lock.acquire()
    assert sync.push(deck.paths, KEY, deck.root, wait=0)["result"] == "busy"
    lock.release()
    assert sync.push(deck.paths, KEY, deck.root, wait=0)["result"] == "uploaded"


def test_wrap_launch_line():
    exe, options = launch.wrap(
        "/bin/bash",
        '"/run/media/deck/Alan Wake 2/insertplay-start.sh" --guid ' + KEY,
        KEY,
        "/run/media/deck/Alan Wake 2",
        "/home/deck/.local/share/insertplay/bin/insertplay-run",
    )
    assert exe == "/bin/bash"
    assert options == (
        f'"/home/deck/.local/share/insertplay/bin/insertplay-run" {KEY} "/run/media/deck/Alan Wake 2" -- '
        f'"/bin/bash" "/run/media/deck/Alan Wake 2/insertplay-start.sh" --guid {KEY}'
    )
    assert launch.wrap("/opt/emu/duckstation", "x", KEY, "/r", "/w") is None
    assert launch.wrap("/bin/bash", "x", KEY, '/r"oops', "/w") is None


def test_install_is_idempotent(tmp_path):
    home = str(tmp_path / "home")
    assert install.install(home) is True
    assert install.install(home) is False
    lib = os.path.join(home, "lib", install.PACKAGE)
    assert {"sync.py", "cli.py", "snapshot.py"} <= set(os.listdir(lib))
    assert os.path.isfile(install.wrapper_path(home))
    os.remove(install.wrapper_path(home))
    assert install.install(home) is True


def _bash():
    return shutil.which("bash") and shutil.which("flock") and os.path.exists("/usr/bin/python3")


@linux
@pytest.mark.skipif(not _bash(), reason="needs bash, flock and /usr/bin/python3")
def test_wrapper_end_to_end(server, tmp_path):
    home = str(tmp_path / "home")
    install.install(home)
    paths = sync.Paths(home)
    root = game(tmp_path / "game", TD6)
    write(root, "duckstation/memcards/a.mcd", "before")
    sync.write_config(paths, server.url, TOKEN, True, True, "deck")
    # server has a newer save from the Machine
    other = Device(tmp_path, "machine", server.url, TD6)
    write(other.root, "duckstation/memcards/a.mcd", "from-machine", mtime=time.time() + 100)
    other.pull()
    game_cmd = f'test "$(cat "{root}/duckstation/memcards/a.mcd")" = from-machine && printf played > "{root}/duckstation/memcards/a.mcd"'
    env = {**os.environ, "INSERTPLAY_HOME": home}
    done = subprocess.run(
        ["/bin/bash", install.wrapper_path(home), KEY, root, "--", "/bin/bash", "-c", game_cmd],
        env=env, timeout=60,
    )
    assert done.returncode == 0  # the game saw the Machine's save before it started
    assert read(root, "duckstation/memcards/a.mcd") == b"played"
    assert server.store.meta(KEY)["device"] == "deck"  # uploaded after the game
    assert sync.last_result(paths, KEY)["op"] == "push"


@linux
@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_wrapper_without_module_runs_the_game(tmp_path):
    home = str(tmp_path / "home")
    install.install(home)
    os.remove(os.path.join(home, "lib", install.ENTRY_NAME))
    marker = tmp_path / "ran"
    done = subprocess.run(
        ["/bin/bash", install.wrapper_path(home), KEY, str(tmp_path), "--", "/bin/bash", "-c", f"touch '{marker}'; exit 7"],
        env={**os.environ, "INSERTPLAY_HOME": home}, timeout=30,
    )
    assert done.returncode == 7 and marker.exists()


@linux
@pytest.mark.skipif(not _bash(), reason="needs bash, flock and /usr/bin/python3")
def test_wrapper_with_sync_off_runs_the_game_directly(tmp_path):
    home = str(tmp_path / "home")
    install.install(home)
    sync.write_config(sync.Paths(home), None, None, False, True, "deck")
    root = game(tmp_path / "game", TD6)
    done = subprocess.run(
        ["/bin/bash", install.wrapper_path(home), KEY, root, "--", "/bin/bash", "-c", "exit 0"],
        env={**os.environ, "INSERTPLAY_HOME": home}, timeout=30,
    )
    assert done.returncode == 0
    assert sync.last_result(sync.Paths(home), KEY)["result"] == "disabled"


def test_server_errors_while_resolving_become_records(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url)  # no saves block: asks the server first
    write(deck.root, f"{STEAMUSER}/Documents/x", "1")
    sync.write_config(deck.paths, server.url, "x" * 40, True, True, "deck")
    for op in (deck.pull, deck.push):
        r = op()
        assert r["result"] == "error" and "401" in r["message"]
    assert sync.last_result(deck.paths, KEY)["result"] == "error"


@linux
def test_unreadable_save_file_is_an_error_record(server, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    deck = Device(tmp_path, "deck", server.url, TD6)
    write(deck.root, "duckstation/memcards/a", "1")
    os.chmod(os.path.join(deck.root, "duckstation/memcards/a"), 0)
    try:
        r = deck.pull()
    finally:
        os.chmod(os.path.join(deck.root, "duckstation/memcards/a"), 0o644)
    assert r["result"] == "error" and "PermissionError" in r["message"]


def test_restore_checks_the_way_before_moving_anything(tmp_path):
    spec = strategies.from_block({"strategy": "custom-path", "paths": ["saves"]}, "m")
    src = game(tmp_path / "a")
    write(src, "saves/a.dat", "new-a")
    write(src, "saves/b/c.dat", "new-c")
    snap = snapshot.scan(src, spec)
    archive = str(tmp_path / "s.tgz")
    snapshot.write_archive(src, snap, archive)
    dest = game(tmp_path / "b")
    write(dest, "saves/a.dat", "old-a")
    write(dest, "saves/b", "a file where the archive needs a folder")
    with pytest.raises(snapshot.SnapshotError):
        snapshot.restore(dest, spec, archive, snap.hash)
    assert read(dest, "saves/a.dat") == b"old-a"  # nothing moved


@linux
def test_busy_push_keeps_the_last_real_result(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    write(deck.root, "duckstation/memcards/a", "1")
    deck.pull()
    lock = sync.Lock(deck.paths, KEY)
    assert lock.acquire()
    try:
        assert sync.push(deck.paths, KEY, deck.root)["result"] == "busy"
    finally:
        lock.release()
    assert sync.last_result(deck.paths, KEY)["result"] == "uploaded"


def test_restore_returns_the_archive_snapshot(tmp_path):
    spec = strategies.from_block({"strategy": "duckstation"}, "m")
    src = game(tmp_path / "a")
    write(src, "duckstation/memcards/a.mcd", "x", mtime=1234)
    snap = snapshot.scan(src, spec)
    archive = str(tmp_path / "s.tgz")
    snapshot.write_archive(src, snap, archive)
    dest = game(tmp_path / "b")
    restored = snapshot.restore(dest, spec, archive, snap.hash)
    assert (restored.hash, restored.newest, restored.files) == (snap.hash, snap.newest, snap.files)
    assert snapshot.scan(dest, spec).hash == snap.hash


@linux
@pytest.mark.skipif(not _bash(), reason="needs bash, flock and /usr/bin/python3")
def test_wrapper_game_keeps_sigint_and_stdin(tmp_path):
    home = str(tmp_path / "home")
    install.install(home)
    sync.write_config(sync.Paths(home), "http://127.0.0.1:9/", TOKEN, True, True, "deck")
    root = game(tmp_path / "game", TD6)
    out = tmp_path / "status"
    cmd = f"grep SigIgn /proc/self/status > '{out}'; read line; echo \"$line\" >> '{out}'"
    done = subprocess.run(
        ["/bin/bash", install.wrapper_path(home), KEY, root, "--", "/bin/bash", "-c", cmd],
        env={**os.environ, "INSERTPLAY_HOME": home}, input=b"from-stdin\n", timeout=60,
    )
    assert done.returncode == 0
    ignored, line = out.read_text().splitlines()
    assert int(ignored.split()[1], 16) & 0b110 == 0  # SIGINT (2) and SIGQUIT (3) not ignored
    assert line == "from-stdin"


def test_unchanged_push_that_clears_pending_is_recorded(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    write(deck.root, "duckstation/memcards/a", "v1")
    deck.pull()
    sync.write_config(deck.paths, "http://127.0.0.1:9/", TOKEN, True, True, "deck")
    write(deck.root, "duckstation/memcards/a", "v2")
    assert deck.push()["result"] == "offline" and deck.state()["pending"] is True
    write(deck.root, "duckstation/memcards/a", "v1")  # back to what the server has
    sync.write_config(deck.paths, server.url, TOKEN, True, True, "deck")
    assert deck.push()["result"] == "unchanged"
    last = sync.last_result(deck.paths, KEY)
    assert (last["result"], last["pending"]) == ("unchanged", False)


DIRT2_OLD = {"saves": {"strategy": "proton-default"}}
DIRT2_FIXED = {"strategy": "proton-default", "exclude": ["Documents/My Games/DiRT2/hardwaresettings"]}


def test_library_hint_wins_over_the_installed_manifest(server, tmp_path):
    # the Deck installed DiRT 2 before the exclude was added on the server (#63 validation)
    deck = Device(tmp_path, "deck", server.url, DIRT2_OLD)
    write(deck.root, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/p", "save")
    write(deck.root, f"{STEAMUSER}/Documents/My Games/DiRT2/hardwaresettings/hw.xml", "deck-hw")
    sync.write_hints(deck.paths, {KEY: DIRT2_FIXED})
    r = deck.pull()
    assert (r["result"], r["source"]) == ("uploaded", "library")
    with tarfile.open(os.path.join(server.store.folder(KEY), "current.tar.gz"), "r:gz") as tar:
        uploaded = [f[0] for f in json.load(tar.extractfile(tar.next()))["files"]]
    assert uploaded == [f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/p"]
    # the library dropped the block: the installed (old) block must not come back
    sync.write_hints(deck.paths, {KEY: None})
    assert json.load(open(deck.paths.hint(KEY))) == {"saves": None}
    r = deck.push()
    assert r["source"] == "inferred"
    # out of the index: no hint, the installed manifest decides again
    sync.write_hints(deck.paths, {})
    assert not os.path.exists(deck.paths.hint(KEY))


def test_excluded_entries_already_on_the_server_are_skipped(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, DIRT2_OLD)  # syncs hardwaresettings (old block)
    machine = Device(tmp_path, "machine", server.url, DIRT2_OLD)
    sync.write_hints(machine.paths, {KEY: DIRT2_FIXED})  # the Machine knows the fixed block
    write(deck.root, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/p", "save", mtime=1000)
    write(deck.root, f"{STEAMUSER}/Documents/My Games/DiRT2/hardwaresettings/hw.xml", "deck-hw", mtime=1000)
    write(machine.root, f"{STEAMUSER}/Documents/My Games/DiRT2/hardwaresettings/hw.xml", "machine-hw")
    assert deck.pull()["result"] == "uploaded"
    r = machine.pull()
    assert r["result"] == "downloaded"
    assert read(machine.root, f"{STEAMUSER}/Documents/My Games/DiRT2/savegame/p") == b"save"
    assert read(machine.root, f"{STEAMUSER}/Documents/My Games/DiRT2/hardwaresettings/hw.xml") == b"machine-hw"
    # no download loop: the server still has the Deck's hw.xml, this device never takes it
    assert machine.pull()["result"] == "unchanged"
    assert machine.push()["result"] == "unchanged"


def test_old_single_hash_state_still_works(server, tmp_path):
    deck = Device(tmp_path, "deck", server.url, TD6)
    write(deck.root, "duckstation/memcards/a", "1")
    deck.pull()
    state = deck.state()
    sync.save_state(deck.paths, KEY, {"lastSyncedHash": state["lastLocalHash"], "lastRev": state["lastRev"]})
    assert deck.pull()["result"] == "unchanged"
    write(deck.root, "duckstation/memcards/a", "2")
    assert deck.push()["result"] == "uploaded"


def test_deleted_local_save_is_downloaded_again(server, tmp_path):
    # God of War II on the Machine (#63): memory cards deleted after a sync, then the game
    # opened. The pull must bring the server's save back, not call it "unchanged" (the game
    # would then create blank cards and upload them over the good save).
    deck = Device(tmp_path, "deck", server.url, CLAW)
    machine = Device(tmp_path, "machine", server.url, CLAW)
    write(deck.root, "game/CLAW.USR", "world5", mtime=1000)
    deck.pull()
    assert machine.pull()["result"] == "downloaded"
    os.remove(os.path.join(machine.root, "game/CLAW.USR"))
    r = machine.pull()
    assert r["result"] == "downloaded"
    assert read(machine.root, "game/CLAW.USR") == b"world5"
