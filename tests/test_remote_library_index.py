"""Indexer of the remote library (server/indexer), contract in docs/remote-library.md."""
import datetime
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "indexer"))

import remote_library_index as idx  # noqa: E402

GUID_A = "0F8FAD5B-D9CB-469F-A165-70867728950E"
GUID_B = "b7898be4-146a-42c2-8dec-a8797a259379"
NOW = datetime.datetime(2026, 11, 13, 21, 0, 0, tzinfo=datetime.timezone.utc)

needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks need a POSIX filesystem")


def manifest(name, artwork=None, **extra):
    data = {
        "schemaVersion": 1,
        "shortcut": {
            "appName": name,
            "launch": {"exe": "/bin/bash", "args": '"{SD_ROOT}/insertplay-start.sh"', "startDir": "{SD_ROOT}"},
        },
    }
    if artwork is not None:
        data["shortcut"]["artwork"] = artwork
    data.update(extra)
    return data


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as f:
        f.write(content)


def make_game(library, folder, guid, name="Game", artwork=None, files=None, **extra):
    root = os.path.join(library, folder)
    write(os.path.join(root, "steam-shortcut.json"), json.dumps(manifest(name, artwork, **extra)))
    write(os.path.join(root, "shortcut-identity.json"), json.dumps({"guid": guid}))
    for rel, content in (files or {}).items():
        write(os.path.join(root, rel), content)
    return root


@pytest.fixture
def lib(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    return str(library)


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "config" / "index")


def run(lib, out, **kw):
    return idx.build_index(lib, out, now=NOW, **kw)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_index_and_file_list(lib, out):
    make_game(lib, "test-drive-6", GUID_A, "Test Drive 6",
              artwork={"capsule": "{SD_ROOT}/art/cover.png", "hero": "/abs/hero.png", "logo": "../x.png"},
              game={"platform": "ps1", "emulator": "duckstation"},
              files={
                  "insertplay-start.sh": "#!/bin/bash\necho hi\n",
                  "duckstation/DuckStation.AppImage": b"\x7fELF\x02\x01" + b"\0" * 100,
                  "game.bin": b"\x00" * 5000,
                  "game.exe": b"MZ\x90\x00",
                  "art/cover.png": b"\x89PNGcover",
                  "steam-artwork/hero.png": b"\x89PNGhero",
                  "steam-artwork/icon.png": b"\x89PNGicon",
              })
    os.makedirs(os.path.join(lib, "test-drive-6", "duckstation", "memcards"))

    result = run(lib, out)
    assert result["skipped"] == []
    index = read_json(os.path.join(out, "index.json"))
    assert index["schemaVersion"] == 1
    assert index["generatedAt"] == "2026-11-13T21:00:00Z"
    assert index["library"] == "library/"
    [game] = index["games"]
    guid = GUID_A.lower()
    assert game["id"] == guid
    assert game["path"] == "test-drive-6"
    assert game["name"] == "Test Drive 6"
    assert game["files"] == f"lists/{guid}.json"
    assert game["manifest"] == manifest("Test Drive 6", game["manifest"]["shortcut"]["artwork"],
                                        game={"platform": "ps1", "emulator": "duckstation"})
    assert game["manifest"]["shortcut"]["artwork"]["capsule"] == "{SD_ROOT}/art/cover.png"

    list_path = os.path.join(out, "lists", f"{guid}.json")
    with open(list_path, "rb") as f:
        list_bytes = f.read()
    assert game["filesSha256"] == hashlib.sha256(list_bytes).hexdigest()
    file_list = json.loads(list_bytes)
    assert file_list["id"] == guid and file_list["version"] == game["version"]
    assert file_list["schemaVersion"] == 1
    paths = [f["path"] for f in file_list["files"]]
    assert paths == sorted(paths)
    by_path = {f["path"]: f for f in file_list["files"]}
    assert by_path["insertplay-start.sh"]["executable"] is True
    assert by_path["duckstation/DuckStation.AppImage"]["executable"] is True
    assert by_path["game.bin"]["executable"] is False
    assert by_path["game.exe"]["executable"] is False
    assert by_path["game.bin"]["size"] == 5000
    assert by_path["game.bin"]["sha256"] == hashlib.sha256(b"\x00" * 5000).hexdigest()
    assert file_list["dirs"] == ["duckstation/memcards"]
    assert "links" not in file_list
    assert game["fileCount"] == len(file_list["files"])
    assert game["totalSize"] == sum(f["size"] for f in file_list["files"])
    assert game["version"] == idx.compute_version(file_list["files"], file_list["dirs"], [])

    # manifest path with {SD_ROOT}/ removed; absolute and '..' paths fall back to steam-artwork/
    art = game["artwork"]
    assert art["capsule"]["path"] == "art/cover.png"
    assert art["capsule"]["sha256"] == hashlib.sha256(b"\x89PNGcover").hexdigest()
    assert art["hero"]["path"] == "steam-artwork/hero.png"
    assert art["icon"]["path"] == "steam-artwork/icon.png"
    assert "logo" not in art and "horizontal" not in art


def test_version_formula_matches_contract():
    files = [{"path": "a", "size": 1, "sha256": "0" * 64, "executable": False}]
    expected = hashlib.sha256(json.dumps({"files": files, "dirs": [], "links": []}, sort_keys=True,
                                         separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:16]
    assert idx.compute_version(files, [], []) == expected


def test_version_changes_with_content(lib, out):
    root = make_game(lib, "g", GUID_B, files={"data.bin": b"one"})
    run(lib, out)
    v1 = read_json(os.path.join(out, "index.json"))["games"][0]["version"]
    write(os.path.join(root, "data.bin"), b"two")
    run(lib, out, use_cache=False)
    v2 = read_json(os.path.join(out, "index.json"))["games"][0]["version"]
    assert v1 != v2


def test_skips_bad_games_and_reports_why(lib, out):
    make_game(lib, "good", GUID_B, "Good")
    os.makedirs(os.path.join(lib, "no-manifest"))
    write(os.path.join(lib, "bad-json", "steam-shortcut.json"), "{not json")
    write(os.path.join(lib, "bad-json", "shortcut-identity.json"), json.dumps({"guid": GUID_A}))
    write(os.path.join(lib, "no-name", "steam-shortcut.json"), json.dumps({"shortcut": {"launch": {"exe": "x"}}}))
    write(os.path.join(lib, "no-name", "shortcut-identity.json"), json.dumps({"guid": GUID_A}))
    make_game(lib, "template", "00000000-0000-0000-0000-000000000000")
    make_game(lib, "bad-guid", "not-a-guid")
    write(os.path.join(lib, "no-guid", "steam-shortcut.json"), json.dumps(manifest("x")))
    write(os.path.join(lib, "no-guid", "shortcut-identity.json"), json.dumps({}))
    make_game(lib, "partial", "11111111-2222-3333-4444-555555555555", files={"big.bin.part": b"x"})
    write(os.path.join(lib, "loose-file.txt"), "not a game")

    result = run(lib, out)
    reasons = dict(result["skipped"])
    assert set(reasons) == {"no-manifest", "bad-json", "no-name", "template", "bad-guid", "no-guid", "partial"}
    assert "no steam-shortcut.json" in reasons["no-manifest"]
    assert "could not read steam-shortcut.json" in reasons["bad-json"]
    assert "appName" in reasons["no-name"]
    assert "template" in reasons["template"]
    assert "invalid guid" in reasons["bad-guid"]
    assert "no guid" in reasons["no-guid"]
    assert ".part" in reasons["partial"]
    index = read_json(os.path.join(out, "index.json"))
    assert [g["path"] for g in index["games"]] == ["good"]


def test_duplicate_guid_skips_both(lib, out):
    make_game(lib, "one", GUID_A)
    make_game(lib, "two", GUID_A.lower())
    make_game(lib, "three", GUID_B)
    result = run(lib, out)
    reasons = dict(result["skipped"])
    assert set(reasons) == {"one", "two"}
    assert "duplicate guid" in reasons["one"]
    assert [g["path"] for g in read_json(os.path.join(out, "index.json"))["games"]] == ["three"]
    assert os.listdir(os.path.join(out, "lists")) == [f"{GUID_B}.json"]


def test_folder_name_must_follow_path_rules(lib, out):
    make_game(lib, "game.part", GUID_A)
    assert dict(run(lib, out)["skipped"])["game.part"].startswith("folder name")


@pytest.mark.parametrize("path,ok", [
    ("a/b.bin", True),
    ("Captain Claw/CLAW.EXE", True),
    ("ção/日本.txt", True),
    ("", False),
    ("/abs", False),
    ("a//b", False),
    ("a/", False),
    ("./a", False),
    ("a/../b", False),
    ("a\\b", False),
    ("a\x01b", False),
    ("a\x7fb", False),
    ("x" * 256, False),
    ("/".join(["x" * 200] * 21), False),
    ("file.part", False),
])
def test_path_rules(path, ok):
    assert (idx.path_problem(path) is None) == ok


@pytest.mark.parametrize("link,target,ok", [
    ("proton/lib/wine/i386/a.dll", "../x86_64/a.dll", True),
    ("a", "b", True),
    ("sub/a", "..", True),            # the game folder itself
    ("a", "..", False),
    ("sub/a", "../../x", False),
    ("a", "/etc/passwd", False),
    ("a", "", False),
    ("a", "b\\c", False),
])
def test_link_target_rules(link, target, ok):
    assert (idx.link_target_problem(link, target) is None) == ok


@needs_symlinks
def test_symlinks_inside_the_game_are_kept(lib, out):
    root = make_game(lib, "claw", GUID_B, files={
        "proton/files/lib/wine/x86_64-windows/example.dll": b"MZ",
        "runtime/lib/libfoo.so.1.2": b"\x7fELF",
    })
    os.makedirs(os.path.join(root, "proton/files/lib/wine/i386-windows"))
    os.symlink("../x86_64-windows/example.dll", os.path.join(root, "proton/files/lib/wine/i386-windows/example.dll"))
    os.symlink("libfoo.so.1.2", os.path.join(root, "runtime/lib/libfoo.so.1"))
    os.symlink("lib", os.path.join(root, "runtime/lib64"))  # a link to a directory is not followed
    os.symlink("missing.so", os.path.join(root, "runtime/lib/dangling.so"))

    result = run(lib, out)
    assert result["skipped"] == []
    file_list = read_json(os.path.join(out, "lists", f"{GUID_B}.json"))
    assert file_list["links"] == [
        {"path": "proton/files/lib/wine/i386-windows/example.dll", "target": "../x86_64-windows/example.dll"},
        {"path": "runtime/lib/dangling.so", "target": "missing.so"},
        {"path": "runtime/lib/libfoo.so.1", "target": "libfoo.so.1.2"},
        {"path": "runtime/lib64", "target": "lib"},
    ]
    assert [f["path"] for f in file_list["files"]] == [
        "proton/files/lib/wine/x86_64-windows/example.dll", "runtime/lib/libfoo.so.1.2",
        "shortcut-identity.json", "steam-shortcut.json"]
    game = read_json(os.path.join(out, "index.json"))["games"][0]
    assert game["version"] == idx.compute_version(file_list["files"], [], file_list["links"])


@needs_symlinks
@pytest.mark.parametrize("make", [
    lambda root: os.symlink("/etc/passwd", os.path.join(root, "abs")),
    lambda root: os.symlink("../other/secret", os.path.join(root, "up")),
    lambda root: (os.symlink(".", os.path.join(root, "x")), os.makedirs(os.path.join(root, "sub")),
                  os.symlink("../x/..", os.path.join(root, "sub", "y"))),
])
def test_symlinks_escaping_the_game_skip_it(lib, out, make):
    root = make_game(lib, "evil", GUID_A)
    make(root)
    make_game(lib, "good", GUID_B)
    reasons = dict(run(lib, out)["skipped"])
    assert list(reasons) == ["evil"]
    assert "symlink" in reasons["evil"]


@needs_symlinks
def test_symlinked_game_folder_is_not_followed(lib, out, tmp_path):
    real = make_game(str(tmp_path), "outside", GUID_A)
    os.symlink(real, os.path.join(lib, "linked"))
    reasons = dict(run(lib, out)["skipped"])
    assert "symlink" in reasons["linked"]


@needs_symlinks
def test_fifo_skips_the_game(lib, out):
    root = make_game(lib, "g", GUID_A)
    os.mkfifo(os.path.join(root, "pipe"))
    assert "not a regular file" in dict(run(lib, out)["skipped"])["g"]


def test_stale_lists_removed_and_no_temp_files_left(lib, out):
    make_game(lib, "a", GUID_A)
    root_b = make_game(lib, "b", GUID_B)
    run(lib, out)
    assert sorted(os.listdir(os.path.join(out, "lists"))) == sorted([f"{GUID_A.lower()}.json", f"{GUID_B}.json"])
    os.remove(os.path.join(root_b, "shortcut-identity.json"))
    run(lib, out)
    assert os.listdir(os.path.join(out, "lists")) == [f"{GUID_A.lower()}.json"]
    assert sorted(os.listdir(out)) == ["hash-cache.json", "index.json", "lists"]


def test_hash_cache_reused_by_size_and_mtime(lib, out, monkeypatch):
    make_game(lib, "g", GUID_A, files={"data.bin": b"abc"})
    run(lib, out)
    calls = []
    real_hash = idx.hash_file
    monkeypatch.setattr(idx, "hash_file", lambda p: calls.append(p) or real_hash(p))
    run(lib, out)
    assert calls == []
    run(lib, out, use_cache=False)
    assert len(calls) == 3


def test_library_is_not_written(lib, out):
    make_game(lib, "g", GUID_A, files={"data.bin": b"abc"})

    def snapshot():
        return sorted((dp, sorted(dn), sorted(fn)) for dp, dn, fn in os.walk(lib))

    before = snapshot()
    run(lib, out)
    assert snapshot() == before


def test_missing_library_writes_nothing(tmp_path, out):
    with pytest.raises(idx.FatalError):
        idx.build_index(str(tmp_path / "not-mounted"), out)
    assert not os.path.exists(out)


def test_output_inside_library_refused(lib):
    with pytest.raises(idx.FatalError):
        idx.build_index(lib, os.path.join(lib, "index"))


def test_main_exit_codes(lib, out, capsys):
    make_game(lib, "good", GUID_A)
    os.makedirs(os.path.join(lib, "bad"))
    assert idx.main([lib, out]) == 0
    assert idx.main([lib, out, "--strict"]) == 1
    err = capsys.readouterr().err
    assert "indexed good" in err and "skipped bad" in err
    assert idx.main([lib + "-missing", out]) == 2


def test_case_insensitive_manifest_keys_and_bom(lib, out):
    root = os.path.join(lib, "legacy")
    data = {"Shortcut": {"AppName": "Legacy", "Launch": {"Exe": "/bin/bash"}, "Artwork": {"Capsule": "c.png"}}}
    write(os.path.join(root, "steam-shortcut.json"), "﻿" + json.dumps(data))
    write(os.path.join(root, "shortcut-identity.json"), json.dumps({"GUID": GUID_A}))
    write(os.path.join(root, "c.png"), b"png")
    run(lib, out)
    [game] = read_json(os.path.join(out, "index.json"))["games"]
    assert game["name"] == "Legacy"
    assert game["manifest"] == data
    assert game["artwork"]["capsule"]["path"] == "c.png"
