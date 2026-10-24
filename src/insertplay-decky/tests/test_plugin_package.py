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
    (plugin / "stray.txt").write_text("fora do pacote\n", encoding="utf-8")
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
    [("0.2.0", "0.1.10", 1), ("0.1.10", "0.2.0", -1), ("1.0.0", "1.0.0", 0), ("1.0.0-beta", "1.0.0", 0)],
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
