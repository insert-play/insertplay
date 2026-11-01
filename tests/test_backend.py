import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from insertplay_decky import cards  # noqa: E402
from insertplay_decky.manifest import CardError, load_card  # noqa: E402
from insertplay_decky.store import EventLog, Registry, Settings  # noqa: E402

GUID = "0F8FAD5B-D9CB-469F-A165-70867728950E"

MANIFEST = {
    "schemaVersion": 1,
    "shortcut": {
        "appName": "New Super Mario Bros. U",
        "visibility": {"hidden": True},
        "launch": {
            "exe": "/bin/bash",
            "args": '"{SD_ROOT}/insertplay-start.sh"',
            "startDir": "{SD_ROOT}",
        },
        "artwork": {
            "capsule": "steam-artwork/capsule.png",
            "hero": "steam-artwork/hero.png",
            "logo": "missing/logo.png",
        },
    },
}


def make_card(root, name="SD1", manifest=MANIFEST, guid=GUID, legacy=False, art=("capsule", "hero", "logo")):
    mount = root / name
    base = mount / "insertplay" if legacy else mount
    base.mkdir(parents=True)
    (base / "steam-shortcut.json").write_text(json.dumps(manifest), encoding="utf-8")
    if guid is not None:
        (base / "shortcut-identity.json").write_text(json.dumps({"guid": guid}), encoding="utf-8")
    (mount / "steam-artwork").mkdir()
    for kind in art:
        (mount / "steam-artwork" / f"{kind}.png").write_bytes(b"png")
    return mount


def test_find_cards_ignores_mounts_without_manifest(tmp_path):
    make_card(tmp_path / "deck")
    (tmp_path / "deck" / "USB").mkdir()
    found = cards.find_cards([str(tmp_path)])
    assert [c["label"] for c in found] == ["SD1"]


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks")
def test_find_cards_dedupes_steamos_symlink_and_keeps_real_mount(tmp_path):
    real = make_card(tmp_path / "deck")
    os.symlink(real, tmp_path / "SD1")
    found = cards.find_cards([str(tmp_path)])
    assert len(found) == 1
    assert found[0]["mount"] == str(real)


def test_find_cards_accepts_legacy_layout(tmp_path):
    make_card(tmp_path, legacy=True)
    assert len(cards.find_cards([str(tmp_path)])) == 1


def test_diff_reports_inserted_and_removed():
    a, b, c = ({"real": k} for k in "abc")
    inserted, removed = cards.diff({"a": a, "b": b}, {"b": b, "c": c})
    assert inserted == [c] and removed == [a]


def test_library_card_finds_the_mount_above_a_steam_library(tmp_path):
    media = tmp_path / "media"
    mount = media / "deck" / "Games"
    (mount / "SteamLibrary").mkdir(parents=True)
    found = cards.library_card(
        str(mount / "SteamLibrary"), [str(media)], ismount=lambda p: p == str(mount), removable=lambda p: True
    )
    assert found == {"mount": str(mount), "label": "Games", "insertplay": False, "removable": True}


def test_library_card_flags_insertplay_cards(tmp_path):
    media = tmp_path / "media"
    mount = media / "deck" / "NFS"
    (mount / "insertplay").mkdir(parents=True)
    (mount / "insertplay" / cards.MANIFEST_NAME).write_text("{}")
    found = cards.library_card(str(mount), [str(media)], ismount=lambda p: p == str(mount))
    assert found["insertplay"] is True


@pytest.mark.skipif(not hasattr(os, "major"), reason="needs os.major (POSIX)")
def test_disk_is_removable_reads_the_parent_disk_flag(tmp_path):
    # /sys/dev/block/<maj>:<min> points to the partition; `removable` is on the parent disk
    disk = tmp_path / "sys" / "devices" / "usb" / "block" / "sda"
    (disk / "sda1").mkdir(parents=True)
    (disk / "removable").write_text("1\n")
    assert cards.disk_is_removable(str(tmp_path), str(tmp_path / "sys"), resolve=lambda p: str(disk / "sda1"))
    (disk / "removable").write_text("0\n")
    assert not cards.disk_is_removable(str(tmp_path), str(tmp_path / "sys"), resolve=lambda p: str(disk / "sda1"))


def test_disk_is_removable_without_sysfs_is_false(tmp_path):
    assert cards.disk_is_removable(str(tmp_path), str(tmp_path / "nosys")) is False


def test_library_card_outside_media_or_unmounted_is_none(tmp_path):
    media = tmp_path / "media"
    (media / "deck" / "Gone").mkdir(parents=True)
    assert cards.library_card(str(tmp_path / "home"), [str(media)], ismount=lambda p: True) is None
    assert cards.library_card(str(media / "deck" / "Gone"), [str(media)], ismount=lambda p: False) is None


def test_load_card_resolves_placeholders_and_appends_guid(tmp_path):
    mount = make_card(tmp_path)
    card = load_card(str(mount))
    assert card["guid"] == GUID.lower()
    assert card["exe"] == "/bin/bash"
    assert card["startDir"] == str(mount)
    assert card["launchOptions"] == f'"{mount}/insertplay-start.sh" --guid {GUID.lower()}'


def test_load_card_artwork_falls_back_to_default_location(tmp_path):
    mount = make_card(tmp_path)
    art = load_card(str(mount))["artwork"]
    # `logo` points to a nonexistent file in the manifest → falls back to steam-artwork/logo.png
    assert art["logo"] == os.path.normpath(str(mount / "steam-artwork" / "logo.png"))
    assert set(art) == {"capsule", "hero", "logo"}


def test_load_card_icon_from_manifest_or_default(tmp_path):
    mount = make_card(tmp_path, art=("capsule", "icon"))
    card = load_card(str(mount))
    assert card["icon"] == os.path.normpath(str(mount / "steam-artwork" / "icon.png"))
    assert "icon" not in card["artwork"]  # doesn't go through SetCustomArtworkForApp

    manifest = json.loads(json.dumps(MANIFEST))
    manifest["shortcut"]["artwork"]["icon"] = "{SD_ROOT}/art/game.ico"
    mount = make_card(tmp_path, name="SD2", manifest=manifest, art=())
    (mount / "art").mkdir()
    (mount / "art" / "game.ico").write_bytes(b"ico")
    assert load_card(str(mount))["icon"] == os.path.normpath(str(mount / "art" / "game.ico"))
    assert load_card(str(make_card(tmp_path, name="SD3", art=())))["icon"] is None


def test_load_card_without_identity_is_refused(tmp_path):
    mount = make_card(tmp_path, guid=None)
    with pytest.raises(CardError, match="shortcut-identity.json"):
        load_card(str(mount))


def test_load_card_with_invalid_guid_is_refused(tmp_path):
    mount = make_card(tmp_path, guid="not-a-guid")
    with pytest.raises(CardError, match="invalid guid"):
        load_card(str(mount))


def test_load_card_without_game_block(tmp_path):
    assert load_card(str(make_card(tmp_path)))["game"] is None


def test_load_card_reads_game_block(tmp_path):
    manifest = {**MANIFEST, "game": {"platform": "PS2", "emulator": "pcsx2", "retroAchievements": {"gameId": 5613}}}
    game = load_card(str(make_card(tmp_path, manifest=manifest)))["game"]
    assert game == {"platform": "ps2", "emulator": "pcsx2", "retroAchievements": {"gameId": 5613}}


def test_load_card_game_block_keys_are_case_insensitive(tmp_path):
    manifest = {**MANIFEST, "Game": {"Platform": "ps1", "RetroAchievements": {"GameID": 11361}}}
    game = load_card(str(make_card(tmp_path, manifest=manifest)))["game"]
    assert game == {"platform": "ps1", "emulator": None, "retroAchievements": {"gameId": 11361}}


@pytest.mark.parametrize("game_id", [0, -3, "5613", True, 12.0, None])
def test_load_card_invalid_game_id_has_no_achievements(tmp_path, game_id):
    manifest = {**MANIFEST, "game": {"platform": "ps2", "retroAchievements": {"gameId": game_id}}}
    card = load_card(str(make_card(tmp_path, manifest=manifest)))
    assert card["game"]["retroAchievements"] is None
    assert card["appName"] == "New Super Mario Bros. U"  # the card itself still works


@pytest.mark.parametrize("game", ["ps2", [], {"platform": 7, "emulator": "a b", "retroAchievements": 5613}])
def test_load_card_invalid_game_values_become_none(tmp_path, game):
    card = load_card(str(make_card(tmp_path, manifest={**MANIFEST, "game": game})))
    if isinstance(game, dict):
        assert card["game"] == {"platform": None, "emulator": None, "retroAchievements": None}
    else:
        assert card["game"] is None


def test_load_card_without_args_uses_only_guid(tmp_path):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["shortcut"]["launch"]["args"] = ""
    mount = make_card(tmp_path, manifest=manifest)
    assert load_card(str(mount))["launchOptions"] == f"--guid {GUID.lower()}"


def test_load_card_accepts_pascal_case_keys(tmp_path):
    manifest = {"Shortcut": {"AppName": "Game", "Launch": {"Exe": "/bin/bash", "Args": "", "StartDir": ""}}}
    mount = make_card(tmp_path, manifest=manifest)
    card = load_card(str(mount))
    assert card["appName"] == "Game" and card["startDir"] == str(mount)


def test_registry_round_trip_and_merge(tmp_path):
    reg = Registry(str(tmp_path / "s" / "registry.json"))
    reg.put("g", {"appid": 1, "appName": "A"})
    reg.put("g", {"artworkApplied": True})
    assert reg.get("g")["appid"] == 1 and reg.get("g")["artworkApplied"] is True
    reg.forget("g")
    assert reg.all() == {}


def test_settings_reject_unknown_key_and_wrong_type(tmp_path):
    s = Settings(str(tmp_path / "settings.json"))
    assert s.all() == {"onInsert": "launch", "hideRemoved": True}
    for choice in ("none", "page", "launch"):
        assert s.set("onInsert", choice)["onInsert"] == choice
    assert s.set("hideRemoved", False)["hideRemoved"] is False
    for bad in ("open", "", True, None, 1):
        with pytest.raises(ValueError):
            s.set("onInsert", bad)
    with pytest.raises(ValueError):
        s.set("hideRemoved", "no")
    with pytest.raises(ValueError):
        s.set("other", True)
    with pytest.raises(ValueError):
        s.set("autoLaunch", True)
    assert s.all() == {"onInsert": "launch", "hideRemoved": False}


@pytest.mark.parametrize(
    "stored, expected",
    [({"autoLaunch": True}, "launch"), ({"autoLaunch": False}, "none"), ({}, "launch")],
)
def test_settings_migrate_auto_launch(tmp_path, stored, expected):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({**stored, "hideRemoved": False}), encoding="utf-8")
    s = Settings(str(path))
    assert s.all() == {"onInsert": expected, "hideRemoved": False}
    s.set("hideRemoved", True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"onInsert": expected, "hideRemoved": True}


def test_settings_new_key_wins_over_old_and_bad_values_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"autoLaunch": True, "onInsert": "page"}), encoding="utf-8")
    assert Settings(str(path)).all()["onInsert"] == "page"
    path.write_text(json.dumps({"onInsert": "open", "hideRemoved": "no"}), encoding="utf-8")
    assert Settings(str(path)).all() == {"onInsert": "launch", "hideRemoved": True}


def test_event_log_tail_is_newest_first_and_trimmed(tmp_path):
    log = EventLog(str(tmp_path / "events.jsonl"), limit=5, keep=3)
    for i in range(7):
        log.add("k", str(i))
    # past 5 lines (on the 6th) trims to 3; the 7th goes in after the trim
    assert [e["message"] for e in log.tail(10)] == ["6", "5", "4", "3"]
