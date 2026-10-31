import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from insertplay_decky import plugin_package as pp  # noqa: E402
from insertplay_decky.plugin_package import PackageError  # noqa: E402


def make_plugin_dir(root, version="0.2.0", flags=(), name="InsertPlay"):
    plugin = root / "plugin"
    (plugin / "dist").mkdir(parents=True)
    (plugin / "py_modules" / "insertplay_decky" / "__pycache__").mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps({"name": name, "flags": list(flags)}), encoding="utf-8")
    (plugin / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (plugin / "main.py").write_text("# main\n", encoding="utf-8")
    (plugin / "NOTICE").write_text("notice\n", encoding="utf-8")
    (plugin / "dist" / "index.js").write_text("// js\n", encoding="utf-8")
    (plugin / "py_modules" / "insertplay_decky" / "cards.py").write_text("# cards\n", encoding="utf-8")
    (plugin / "py_modules" / "insertplay_decky" / "__pycache__" / "cards.cpython-312.pyc").write_bytes(b"x")
    (plugin / "stray.txt").write_text("outside the package\n", encoding="utf-8")
    return plugin


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def valid_entries(version="0.2.0", **plugin_json):
    meta = {"name": "InsertPlay", "flags": [], **plugin_json}
    return {
        "InsertPlay/plugin.json": json.dumps(meta),
        "InsertPlay/package.json": json.dumps({"version": version}),
        "InsertPlay/main.py": "",
        "InsertPlay/dist/index.js": "",
    }


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("0.2.0", "0.1.10", 1),
        ("0.1.10", "0.2.0", -1),
        ("1.0.0", "1.0.0", 0),
        ("1.0.0-beta", "1.0.0", -1),
        ("1.0.0", "1.0.0-beta", 1),
        ("1.0.0-beta.2", "1.0.0-beta.10", -1),
        ("1.0.0-alpha", "1.0.0-beta", -1),
        ("1.0.0-1", "1.0.0-alpha", -1),
        ("1.0.0+build.5", "1.0.0", 0),
    ],
)
def test_compare_versions(a, b, expected):
    assert pp.compare_versions(a, b) == expected


def test_invalid_version_is_rejected():
    with pytest.raises(PackageError):
        pp.parse_version("dev")


def test_build_then_read_round_trip(tmp_path):
    plugin = make_plugin_dir(tmp_path)
    dest = tmp_path / "card" / "insertplay" / "decky" / "InsertPlay.zip"
    info = pp.build_zip_from_dir(str(plugin), str(dest))
    assert info["version"] == "0.2.0" and len(info["sha256"]) == 64
    names = set(zipfile.ZipFile(dest).namelist())
    assert "InsertPlay/py_modules/insertplay_decky/cards.py" in names
    assert "InsertPlay/NOTICE" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert "InsertPlay/stray.txt" not in names
    assert not os.path.exists(str(dest) + ".tmp")


def test_build_keeps_old_package_when_new_one_is_invalid(tmp_path):
    good = make_plugin_dir(tmp_path)
    dest = tmp_path / "InsertPlay.zip"
    pp.build_zip_from_dir(str(good), str(dest))
    before = dest.read_bytes()
    (good / "package.json").write_text(json.dumps({"version": "dev"}), encoding="utf-8")
    with pytest.raises(PackageError):
        pp.build_zip_from_dir(str(good), str(dest))
    assert dest.read_bytes() == before
    assert not os.path.exists(str(dest) + ".tmp")


@pytest.mark.parametrize(
    "entries,message",
    [
        ({**valid_entries(), "InsertPlay/../evil.py": ""}, "unsafe"),
        ({**valid_entries(), "/etc/evil": ""}, "unsafe"),
        ({**valid_entries(), "Other/plugin.json": "{}"}, "single InsertPlay/"),
        (valid_entries(flags=["root"]), "root"),
        (valid_entries(name="Other"), "not the InsertPlay"),
        ({k: v for k, v in valid_entries().items() if not k.endswith("index.js")}, "dist/index.js"),
        (valid_entries(version="banana"), "invalid version"),
        # extracted over the validated plugin.json (PR #6 review)
        ({**valid_entries(), "InsertPlay/./plugin.json": '{"name": "InsertPlay", "flags": ["root"]}'}, "unsafe"),
        ({**valid_entries(), "InsertPlay//plugin.json": '{"name": "InsertPlay", "flags": ["root"]}'}, "unsafe"),
        ({**valid_entries(), "C:/evil": ""}, "unsafe"),
    ],
)
def test_read_package_rejects_bad_zips(tmp_path, entries, message):
    path = write_zip(tmp_path / "p.zip", entries)
    with pytest.raises(PackageError, match=message):
        pp.read_package(str(path))


def test_read_package_rejects_symlinks(tmp_path):
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in valid_entries().items():
            archive.writestr(name, data)
        link = zipfile.ZipInfo("InsertPlay/link")
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(PackageError, match="symlink"):
        pp.read_package(str(path))


def test_read_package_rejects_garbage(tmp_path):
    path = tmp_path / "p.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(PackageError):
        pp.read_package(str(path))


def test_card_status(tmp_path):
    mount = tmp_path / "SD1"
    mount.mkdir()
    assert pp.card_status(str(mount), "0.1.0")["relation"] == "missing"

    zip_path = mount / "insertplay" / "decky" / "InsertPlay.zip"
    zip_path.parent.mkdir(parents=True)
    write_zip(zip_path, valid_entries("0.2.0"))
    assert pp.card_status(str(mount), "0.1.0")["relation"] == "newer"
    assert pp.card_status(str(mount), "0.2.0")["relation"] == "same"
    assert pp.card_status(str(mount), "0.3.0")["relation"] == "older"

    zip_path.write_bytes(b"broken")
    status = pp.card_status(str(mount), "0.1.0")
    assert status["relation"] == "invalid" and status["error"]


def test_corrupt_entry_data_is_invalid_not_a_crash(tmp_path):
    """Good central directory, one entry's data corrupted: zlib.error becomes PackageError."""
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in valid_entries().items():
            archive.writestr(name, data + " " * 200)
    raw = bytearray(path.read_bytes())
    info = zipfile.ZipFile(path).getinfo("InsertPlay/plugin.json")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    raw[start:start + 8] = bytes([0xFF]) * 8
    path.write_bytes(bytes(raw))
    with pytest.raises(PackageError, match="invalid zip"):
        pp.read_package(str(path))
    mount = tmp_path / "SD1"
    (mount / "insertplay" / "decky").mkdir(parents=True)
    (mount / "insertplay" / "decky" / "InsertPlay.zip").write_bytes(bytes(raw))
    assert pp.card_status(str(mount), "0.1.0")["relation"] == "invalid"


def test_oversized_file_is_rejected_without_reading(tmp_path, monkeypatch):
    path = write_zip(tmp_path / "p.zip", valid_entries())
    monkeypatch.setattr(pp, "MAX_ZIP_BYTES", 10)
    with pytest.raises(PackageError, match="too large"):
        pp.read_package(str(path))


def test_status_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    mount = tmp_path / "SD1"
    zip_path = mount / "insertplay" / "decky" / "InsertPlay.zip"
    zip_path.parent.mkdir(parents=True)
    write_zip(zip_path, valid_entries("0.2.0"))
    reads = []
    real = pp.read_package
    monkeypatch.setattr(pp, "read_package", lambda p: reads.append(p) or real(p))
    pp.card_status(str(mount), "0.1.0")
    pp.card_status(str(mount), "0.1.0")
    assert len(reads) == 1
    write_zip(zip_path, valid_entries("0.3.0"))
    os.utime(zip_path, ns=(1, 1))  # ensures a different mtime even on a low-resolution FS
    assert pp.card_status(str(mount), "0.1.0")["version"] == "0.3.0"
    assert len(reads) == 2


def test_write_does_not_reread_for_status(tmp_path, monkeypatch):
    plugin = make_plugin_dir(tmp_path)
    mount = tmp_path / "SD1"
    mount.mkdir()
    pp.build_zip_from_dir(str(plugin), pp.card_package_path(str(mount)))
    monkeypatch.setattr(pp, "read_package", lambda p: pytest.fail("status re-read the zip"))
    assert pp.card_status(str(mount), "0.2.0")["relation"] == "same"


POSIX = pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks")


def test_duplicate_entries_are_rejected(tmp_path):
    path = tmp_path / "p.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in valid_entries().items():
                archive.writestr(name, data)
            archive.writestr("InsertPlay/plugin.json", '{"name": "InsertPlay", "flags": ["root"]}')
    with pytest.raises(PackageError, match="duplicate"):
        pp.read_package(str(path))


def test_backslash_names_are_rejected(tmp_path):
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in valid_entries().items():
            archive.writestr(name, data)
        # ZipInfo("a\\b") becomes "a/b" on Windows; the raw name is what a hostile zip would carry
        info = zipfile.ZipInfo("placeholder")
        info.filename = "InsertPlay\\..\\evil.py"
        archive.writestr(info, "")
    with pytest.raises(PackageError, match="unsafe"):
        pp.read_package(str(path))


def test_directory_entries_are_allowed(tmp_path):
    entries = {"InsertPlay/": "", "InsertPlay/dist/": "", **valid_entries()}
    path = write_zip(tmp_path / "p.zip", entries)
    assert pp.read_package(str(path))["version"] == "0.2.0"


def test_corrupt_payload_member_is_rejected(tmp_path):
    """Bad CRC in main.py: only the JSON was read and the zip passed (PR #6 review)."""
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name, data in {**valid_entries(), "InsertPlay/main.py": "print('ok')"}.items():
            archive.writestr(name, data)
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b"print('ok')", b"print('XX')", 1))
    with pytest.raises(PackageError, match="corrupt file in zip: 'InsertPlay/main.py'"):
        pp.read_package(str(path))


def test_copy_package_to_card_never_downgrades(tmp_path):
    mount = tmp_path / "SD1"
    mount.mkdir()
    old = write_zip(tmp_path / "old.zip", valid_entries("0.1.0"))
    new = write_zip(tmp_path / "new.zip", valid_entries("0.2.0"))
    info, copied = pp.copy_package_to_card(str(new), str(mount))
    assert copied and info["version"] == "0.2.0"
    info, copied = pp.copy_package_to_card(str(old), str(mount))
    assert not copied and info["version"] == "0.2.0"
    assert pp.read_package(pp.card_package_path(str(mount)))["version"] == "0.2.0"


def test_copy_package_to_card_replaces_invalid_package(tmp_path):
    mount = tmp_path / "SD1"
    dest = mount / "insertplay" / "decky" / "InsertPlay.zip"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"broken")
    src = write_zip(tmp_path / "new.zip", valid_entries("0.1.0"))
    assert pp.copy_package_to_card(str(src), str(mount))[1]


def test_write_card_package_refuses_a_file_where_the_folder_should_be(tmp_path):
    mount = tmp_path / "SD1"
    (mount / "insertplay").mkdir(parents=True)
    (mount / "insertplay" / "decky").write_text("not a dir", encoding="utf-8")
    with pytest.raises(PackageError, match="not a directory"):
        pp.write_card_package(str(make_plugin_dir(tmp_path)), str(mount))


@POSIX
@pytest.mark.parametrize("linked", ["insertplay", "insertplay/decky"])
def test_write_card_package_refuses_symlinked_folders(tmp_path, linked):
    """`insertplay` or `decky` as a symlink would send the write outside the card."""
    mount = tmp_path / "SD1"
    outside = tmp_path / "outside"
    outside.mkdir()
    link = mount / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, link)
    with pytest.raises(PackageError, match="symlink"):
        pp.write_card_package(str(make_plugin_dir(tmp_path)), str(mount))
    assert os.listdir(outside) == []


@POSIX
def test_write_card_package_does_not_follow_links_in_the_folder(tmp_path):
    """A fixed-name `.tmp` or the zip itself as a symlink must not truncate the target."""
    plugin = make_plugin_dir(tmp_path)
    mount = tmp_path / "SD1"
    folder = mount / "insertplay" / "decky"
    folder.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    os.symlink(victim, folder / "InsertPlay.zip.tmp")
    os.symlink(victim, folder / "InsertPlay.zip")
    info = pp.write_card_package(str(plugin), str(mount))
    assert victim.read_text(encoding="utf-8") == "keep me"
    assert not os.path.islink(folder / "InsertPlay.zip")
    assert os.stat(folder / "InsertPlay.zip").st_mode & 0o777 == 0o644
    assert pp.read_package(str(folder / "InsertPlay.zip"))["sha256"] == info["sha256"]
