import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from insertplay_decky import icons  # noqa: E402

GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def vdf(entries):
    """`shortcuts.vdf` binário, no formato que o Steam grava."""

    def string(key, value):
        return b"\x01" + key.encode() + b"\x00" + value.encode() + b"\x00"

    out = b"\x00shortcuts\x00"
    for index, (appid, icon) in enumerate(entries):
        out += b"\x00" + str(index).encode() + b"\x00"
        out += b"\x02appid\x00" + struct.pack("<i", struct.unpack("<i", struct.pack("<I", appid))[0])
        out += string("AppName", f"app {index}") + string("icon", icon)
        out += b"\x00tags\x00\x08"
        out += b"\x07LastPlayTime\x00" + struct.pack("<Q", 0)
        out += b"\x08"
    return out + b"\x08\x08"


def test_account_id_and_vdf_path():
    assert icons.account_id("76561198068701803") == 108436075
    path = icons.shortcuts_vdf("/steam", "76561198068701803")
    assert path == os.path.join("/steam", "userdata", "108436075", "config", "shortcuts.vdf")
    with pytest.raises(ValueError):
        icons.account_id("12")


def test_shortcut_icons_reads_appid_and_icon(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(vdf([(2483608115, ""), (4175769179, "/grid/4175769179_icon.png")]))
    assert icons.shortcut_icons(str(path)) == {2483608115: "", 4175769179: "/grid/4175769179_icon.png"}


def test_target_path_keeps_same_content_and_renames_new_content(tmp_path):
    src = tmp_path / "icon.PNG"
    src.write_bytes(b"one")
    first = icons.target_path("", str(src), GUID, "/icons", now=lambda: 1.0)
    assert os.path.dirname(first) == "/icons" and first.endswith(".png")
    assert os.path.basename(first).startswith(f"{GUID}-{icons.content_hash(str(src))}-")
    # mesmo conteúdo já aplicado: o caminho atual, sem nada a fazer
    assert icons.target_path(first, str(src), GUID, "/icons", now=lambda: 2.0) == first
    # cópia do formato antigo (sem geração) com o mesmo conteúdo também vale
    legacy = f"/icons/{GUID}-{icons.content_hash(str(src))}.png"
    assert icons.target_path(legacy, str(src), GUID, "/icons") == legacy
    src.write_bytes(b"two")
    second = icons.target_path(first, str(src), GUID, "/icons", now=lambda: 2.0)
    assert second != first


def test_target_path_back_to_old_content_gets_a_new_path(tmp_path):
    """A → B → A: o Steam não relê um caminho já carregado na sessão (review do PR #18)."""
    src = tmp_path / "icon.png"
    src.write_bytes(b"A")
    a = icons.target_path("", str(src), GUID, str(tmp_path), now=lambda: 1.0)
    src.write_bytes(b"B")
    b = icons.target_path(a, str(src), GUID, str(tmp_path), now=lambda: 2.0)
    src.write_bytes(b"A")
    again = icons.target_path(b, str(src), GUID, str(tmp_path), now=lambda: 3.0)
    assert len({a, b, again}) == 3


def test_target_path_skips_an_existing_file(tmp_path):
    src = tmp_path / "icon.png"
    src.write_bytes(b"A")
    taken = icons.target_path("", str(src), GUID, str(tmp_path), now=lambda: 1.0)
    open(taken, "wb").close()
    assert icons.target_path("", str(src), GUID, str(tmp_path), now=lambda: 1.0) != taken


@pytest.mark.parametrize(
    "current, expected",
    [
        (None, True),  # atalho recém-criado, ainda fora do shortcuts.vdf
        ("", True),  # sem ícone
        ("/icons/old.png", True),  # versão antiga gravada pelo plugin
        ("/icons/new.png", False),  # já aplicado
        ("/home/deck/.local/share/Steam/userdata/1/config/grid/7_icon.png", False),  # do usuário
        ("/run/media/deck/Card/icon.png", False),
    ],
)
def test_plan_only_replaces_missing_or_own_icons(current, expected):
    assert icons.plan(current, "/icons/new.png", "/icons") is expected


def test_stage_copies_once(tmp_path):
    src = tmp_path / "icon.png"
    src.write_bytes(b"png")
    target = tmp_path / "icons" / "x.png"
    icons.stage(str(src), str(target))
    assert target.read_bytes() == b"png"
    src.write_bytes(b"changed")
    icons.stage(str(src), str(target))
    assert target.read_bytes() == b"png"
    assert os.listdir(tmp_path / "icons") == ["x.png"]


def test_referenced_icons_reads_every_account(tmp_path):
    for account, icon in (("1", "/icons/a.png"), ("2", "/icons/b.png")):
        config = tmp_path / "userdata" / account / "config"
        config.mkdir(parents=True)
        (config / "shortcuts.vdf").write_bytes(vdf([(2483608115, icon), (4175769179, "")]))
    expected = {os.path.normpath("/icons/a.png"), os.path.normpath("/icons/b.png")}
    assert icons.referenced_icons(str(tmp_path)) == expected


def test_referenced_icons_raises_on_a_broken_vdf(tmp_path):
    config = tmp_path / "userdata" / "1" / "config"
    config.mkdir(parents=True)
    (config / "shortcuts.vdf").write_bytes(b"\x09garbage")
    with pytest.raises(Exception):
        icons.referenced_icons(str(tmp_path))


def test_prune_keeps_only_paths_in_use(tmp_path):
    for name in ("a.png", "b.png", "c.png.tmp"):
        (tmp_path / name).write_bytes(b"x")
    removed = icons.prune(str(tmp_path), {str(tmp_path / "a.png")})
    assert sorted(removed) == ["b.png", "c.png.tmp"]
    assert os.listdir(tmp_path) == ["a.png"]


def test_prune_without_folder_is_noop(tmp_path):
    assert icons.prune(str(tmp_path / "missing"), set()) == []
