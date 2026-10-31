"""Portable saves (issue #13). Real symlinks: only runs on Linux/WSL."""
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from insertplay_decky import prefixes  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "posix", reason="symlink and /proc/mounts are Linux-only")

APPID = 12200
NOW = 1_800_000_000.0
STEAMUSER = os.path.join("pfx", "drive_c", "users", "steamuser")


@pytest.fixture(autouse=True)
def no_live_games(monkeypatch):
    """No game running in the test's /proc, unless the test says otherwise."""
    monkeypatch.setattr(prefixes, "LIVE_APPIDS", lambda: set())


@pytest.fixture
def env(tmp_path):
    mount = tmp_path / "media" / "Bully"
    steam = tmp_path / "Steam"
    (mount / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "compatdata").mkdir(parents=True)
    return {"mount": str(mount), "libraries": [str(mount)], "steam": str(steam)}


def ssd(env, appid=APPID):
    return os.path.join(prefixes.compatdata(env["steam"]), str(appid))


def leftover(env, appid=APPID):
    return os.path.join(env["libraries"][0], "steamapps", "compatdata", str(appid))


def make_prefix(path, save_mtime=None):
    """Prefix with `pfx/`. `save_mtime` creates a user file (the save) with that mtime;
    without it, the prefix is like the one Steam creates on a move: no user file."""
    users = os.path.join(path, STEAMUSER)
    os.makedirs(os.path.join(users, "AppData", "Local", "Temp"), exist_ok=True)
    os.symlink("./Documents", os.path.join(users, "My Documents"))  # as Proton creates it
    os.makedirs(os.path.join(path, "pfx", "drive_c", "windows", "system32"))
    with open(os.path.join(path, "config_info"), "w") as f:
        f.write("11.0-100\n")
    if save_mtime is not None:
        save = os.path.join(users, "Documents", "save.dat")
        os.makedirs(os.path.dirname(save), exist_ok=True)
        with open(save, "w") as f:
            f.write(path)
        os.utime(save, (save_mtime, save_mtime))
    return path


def make_userdata(path, save_mtime):
    """Only the `steamuser` folder (as it sits on the card in userdata mode)."""
    save = os.path.join(path, "Documents", "save.dat")
    os.makedirs(os.path.dirname(save))
    with open(save, "w") as f:
        f.write(path)
    os.utime(save, (save_mtime, save_mtime))
    return path


def save_in(users):
    with open(os.path.join(users, "Documents", "save.dat")) as f:
        return f.read()


def save_of(prefix):
    return save_in(os.path.join(prefix, STEAMUSER))


def run(env, mode="prefix", appids=(APPID,), running=(), elsewhere=()):
    if prefixes.get_mode(env["mount"]) != mode:
        prefixes._write_mode(env["mount"], mode, "0.5.0")
    return prefixes.sync(env["mount"], env["libraries"], list(appids), list(running), list(elsewhere), env["steam"], now=NOW)


def set_mode(env, mode, running=(), appids=(APPID,)):
    return prefixes.set_mode(env["mount"], mode, env["libraries"], list(appids), list(running), [], env["steam"], "0.5.0", now=NOW)


def backups(env):
    folder = os.path.join(env["mount"], ".insertplay", "backups")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def ssd_backups(env, parent=None):
    folder = parent or prefixes.compatdata(env["steam"])
    return [n for n in os.listdir(folder) if ".insertplay-backup-" in n]


# ---- prefix mode -----------------------------------------------------------------


def test_off_does_nothing(env):
    make_prefix(ssd(env), save_mtime=NOW - 100)
    assert prefixes.sync(env["mount"], env["libraries"], [APPID], [], [], env["steam"]) == {}
    assert not os.path.islink(ssd(env))


def test_prefix_migrates_ssd_prefix_to_card_and_links(env):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    assert run(env) == {APPID: "migrated"}
    dest = prefixes.card_prefix(env["mount"], APPID)
    assert os.readlink(ssd(env)) == dest
    assert save_of(dest) == original
    assert os.path.islink(os.path.join(dest, STEAMUSER, "My Documents"))  # inner link copied as a link
    assert len(ssd_backups(env)) == 1  # the original stays on the SSD, nothing deleted


def test_second_pass_is_a_no_op(env):
    make_prefix(ssd(env), save_mtime=NOW - 100)
    run(env)
    assert run(env) == {APPID: "ok"}


def test_prefix_links_existing_card_prefix(env):
    dest = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    assert run(env) == {APPID: "linked"}
    assert os.readlink(ssd(env)) == dest


def test_nothing_to_do_without_any_prefix(env):
    assert run(env) == {APPID: "none"}
    assert not os.path.lexists(ssd(env))


def test_running_game_is_not_touched(env):
    make_prefix(ssd(env), save_mtime=NOW - 100)
    assert run(env, running=[APPID]) == {APPID: "running"}
    assert not os.path.islink(ssd(env))


def test_fresh_prefix_never_beats_a_save_and_is_kept_quietly(env):
    """After a move Steam creates an empty prefix on the SSD, newer than everything."""
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 1000)
    make_prefix(ssd(env))  # no user file
    assert run(env) == {APPID: "linked"}  # no save on the other side: not a conflict
    assert os.readlink(ssd(env)) == card
    assert len(ssd_backups(env)) == 1  # but it is kept


def test_newest_save_wins_and_card_copy_becomes_backup(env):
    """Played later on a device with an old plugin: the SSD save is the newest."""
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 1000)
    ssd_prefix = make_prefix(ssd(env), save_mtime=NOW - 10)
    assert run(env) == {APPID: "conflict"}
    assert save_of(card) == ssd_prefix  # the card now has the SSD save
    [backup] = backups(env)
    assert save_of(os.path.join(env["mount"], ".insertplay", "backups", backup)) == card


def test_prefix_leftover_from_a_move_is_adopted(env):
    """SSD → card move without a link: Steam takes the prefix to `steamapps/compatdata` and
    recreates an empty one on the SSD."""
    moved = make_prefix(leftover(env), save_mtime=NOW - 500)
    make_prefix(ssd(env))
    assert run(env) == {APPID: "migrated"}
    assert save_of(prefixes.card_prefix(env["mount"], APPID)) == moved
    assert not os.path.exists(leftover(env))  # no longer blocks the move back


def test_copy_left_by_a_move_through_the_link_goes_to_backups(env):
    make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    run(env)
    make_prefix(leftover(env), save_mtime=NOW - 100)
    assert run(env) == {APPID: "leftover"}
    assert not os.path.exists(leftover(env))
    assert len(backups(env)) == 1


def test_ssd_folder_without_pfx_is_kept_not_deleted(env):
    make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    os.makedirs(ssd(env))
    with open(os.path.join(ssd(env), "config_info"), "w") as f:
        f.write("x")
    assert run(env) == {APPID: "linked"}
    assert os.path.islink(ssd(env))
    assert len(ssd_backups(env)) == 1


def test_foreign_symlink_is_left_alone(env, tmp_path):
    elsewhere = make_prefix(str(tmp_path / "other"), save_mtime=NOW)
    os.symlink(elsewhere, ssd(env))
    assert run(env) == {APPID: "foreign"}
    assert run(env, mode="userdata") == {APPID: "foreign"}
    assert os.readlink(ssd(env)) == elsewhere
    assert not os.path.islink(os.path.join(elsewhere, STEAMUSER))


def test_interrupted_copy_is_redone(env):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    partial = prefixes.card_prefix(env["mount"], APPID) + ".partial"
    os.makedirs(partial)
    assert run(env) == {APPID: "migrated"}
    assert save_of(prefixes.card_prefix(env["mount"], APPID)) == original
    assert not os.path.exists(partial)


def test_game_moved_off_the_card_gets_a_copy_on_the_ssd(env):
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    run(env)
    assert run(env, appids=[], elsewhere=[APPID]) == {APPID: "restored"}
    assert not os.path.islink(ssd(env))
    assert save_of(ssd(env)) == card
    assert save_of(card) == card  # card intact


def test_uninstalled_game_only_loses_the_link(env):
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    run(env)
    assert run(env, appids=[]) == {APPID: "unlinked"}
    assert not os.path.lexists(ssd(env))
    assert save_of(card) == card


def test_turning_off_copies_back_and_keeps_the_card(env):
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    run(env)
    assert set_mode(env, "off") == {APPID: "restored"}
    assert prefixes.get_mode(env["mount"]) == "off"
    assert not os.path.islink(ssd(env))
    assert save_of(ssd(env)) == card
    assert save_of(card) == card


def test_turning_off_refuses_while_a_card_game_runs(env):
    make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    run(env)
    assert set_mode(env, "off", running=[APPID]) == {APPID: "running"}
    assert prefixes.get_mode(env["mount"]) == "prefix"
    assert os.path.islink(ssd(env))


def test_unlink_dangling_only_removes_our_broken_links(env, tmp_path):
    make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 100)
    run(env)
    os.symlink(str(tmp_path / "gone" / "x"), ssd(env, 111))  # broken foreign link
    os.rename(env["mount"], env["mount"] + "-out")  # card "removed"
    assert prefixes.unlink_dangling(env["steam"]) == [APPID]
    assert not os.path.lexists(ssd(env))
    assert os.path.islink(ssd(env, 111))


# ---- userdata mode (steamuser only) ---------------------------------------------


def ssd_user(env, appid=APPID):
    return os.path.join(ssd(env, appid), STEAMUSER)


def test_userdata_moves_only_steamuser(env):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    assert run(env, mode="userdata") == {APPID: "migrated"}
    dest = prefixes.card_userdata(env["mount"], APPID)
    assert os.readlink(ssd_user(env)) == dest
    assert save_in(dest) == original
    assert os.path.isdir(os.path.join(ssd(env), "pfx", "drive_c", "windows"))  # the rest stays on the SSD
    assert not os.path.islink(ssd(env))
    users = os.path.dirname(ssd_user(env))
    assert len(ssd_backups(env, users)) == 1  # original steamuser kept alongside


def test_userdata_builds_the_skeleton_on_a_device_where_the_game_never_ran(env):
    """Steam Machine with the Deck's card: no prefix. Measured: Proton builds the prefix
    around the link and the save shows up on the very first launch."""
    dest = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    assert run(env, mode="userdata") == {APPID: "linked"}
    assert os.readlink(ssd_user(env)) == dest


def test_userdata_fresh_steamuser_loses_quietly(env):
    dest = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 1000)
    make_prefix(ssd(env))  # the game ran here without the card and saved nothing
    assert run(env, mode="userdata") == {APPID: "linked"}
    assert os.readlink(ssd_user(env)) == dest


def test_userdata_newest_save_wins(env):
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 1000)
    ssd_prefix = make_prefix(ssd(env), save_mtime=NOW - 10)
    assert run(env, mode="userdata") == {APPID: "conflict"}
    assert save_in(card) == ssd_prefix
    assert len(backups(env)) == 1


def test_userdata_leftover_from_a_move_goes_to_backups(env):
    make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    run(env, mode="userdata")
    make_prefix(leftover(env), save_mtime=NOW - 100)  # SSD → card move copied the prefix
    assert run(env, mode="userdata") == {APPID: "leftover"}
    assert not os.path.exists(leftover(env))


def test_userdata_turning_off_copies_steamuser_back(env):
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    run(env, mode="userdata")
    assert set_mode(env, "off") == {APPID: "restored"}
    assert not os.path.islink(ssd_user(env))
    assert save_in(ssd_user(env)) == card
    assert save_in(card) == card


def test_switching_prefix_to_userdata_copies_back_first(env):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    set_mode(env, "prefix")
    assert os.path.islink(ssd(env))
    assert set_mode(env, "userdata") == {APPID: "migrated"}
    assert not os.path.islink(ssd(env))  # the prefix went back to the SSD
    assert os.readlink(ssd_user(env)) == prefixes.card_userdata(env["mount"], APPID)
    assert save_in(ssd_user(env)) == original


def test_choosing_the_active_mode_again_changes_nothing(env):
    """A stale panel sends the same mode twice (seen on the Deck): must not copy
    back, redo the link or warn of a conflict."""
    make_prefix(ssd(env), save_mtime=NOW - 100)
    set_mode(env, "userdata")
    link = os.readlink(ssd_user(env))
    assert set_mode(env, "userdata") == {APPID: "ok"}
    assert os.readlink(ssd_user(env)) == link
    assert backups(env) == []


def test_identical_copies_are_not_a_conflict(env):
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    shutil.rmtree(ssd_user(env))
    shutil.copytree(card, ssd_user(env), symlinks=True)  # same date: a copy of the same save
    assert run(env, mode="userdata") == {APPID: "linked"}
    assert len(ssd_backups(env, os.path.dirname(ssd_user(env)))) == 1


def test_userdata_unlink_dangling(env):
    make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    run(env, mode="userdata")
    os.rename(env["mount"], env["mount"] + "-out")
    assert prefixes.unlink_dangling(env["steam"]) == [APPID]
    assert not os.path.lexists(ssd_user(env))


# ---- card ----------------------------------------------------------------------


def test_mode_lives_on_the_card(env):
    assert prefixes.get_mode(env["mount"]) == "off"
    set_mode(env, "userdata", appids=())
    assert prefixes.get_mode(env["mount"]) == "userdata"
    with open(prefixes.config_path(env["mount"])) as f:
        assert json.load(f) == {"mode": "userdata", "minVersion": "0.5.0"}
    with pytest.raises(ValueError):
        set_mode(env, "everything")


def test_fs_type_reads_escaped_mount_points(tmp_path, env):
    mounts = tmp_path / "mounts"
    point = env["mount"].replace(" ", "\\040")
    mounts.write_text(
        f"/dev/nvme0n1p8 / ext4 rw 0 0\n/dev/mmcblk0p1 {point} exfat rw 0 0\n", encoding="utf-8"
    )
    assert prefixes.fs_type(env["mount"], str(mounts)) == "exfat"
    status = prefixes.status(env["mount"], str(mounts))
    assert status["fsOk"] is False and "ext4" in status["reason"]
    mounts.write_text(f"/dev/mmcblk0p1 {point} ext4 rw 0 0\n", encoding="utf-8")
    assert prefixes.status(env["mount"], str(mounts))["fsOk"] is True


def test_fs_type_handles_spaces(tmp_path):
    mount = tmp_path / "Call of Juarez G"
    mount.mkdir()
    mounts = tmp_path / "mounts"
    mounts.write_text(f"/dev/mmcblk0p1 {str(mount).replace(' ', chr(92) + '040')} ext4 rw 0 0\n", encoding="utf-8")
    assert prefixes.fs_type(str(mount), str(mounts)) == "ext4"


# ---- review findings -----------------------------------------------------------


def test_off_on_another_device_releases_this_devices_links(env):
    """Turned off on the other device: the link this one still has becomes a copy, otherwise the save
    would vanish from here when the card left."""
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    run(env, mode="userdata")
    prefixes._write_mode(env["mount"], "off", "0.5.0")  # what the other device wrote
    assert prefixes.sync(env["mount"], env["libraries"], [APPID], [], [], env["steam"]) == {APPID: "restored"}
    assert not os.path.islink(ssd_user(env))
    assert save_in(ssd_user(env)) == card


def test_failed_copy_back_keeps_the_mode(env, monkeypatch):
    make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    run(env, mode="userdata")

    def full_disk(src, dest):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(prefixes, "_copy", full_disk)
    assert set_mode(env, "off")[APPID].startswith("error")
    assert prefixes.get_mode(env["mount"]) == "userdata"  # tries again later
    assert os.path.islink(ssd_user(env))  # the link stayed: the game keeps its save


def test_copy_is_discarded_when_the_game_saves_during_it(env, monkeypatch):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    real_copy = prefixes._copy

    def slow_copy(src, dest):
        real_copy(src, dest)
        save = os.path.join(src, STEAMUSER, "Documents", "save.dat")
        os.utime(save, (NOW - 5, NOW - 5))  # the game opened and saved mid-copy

    monkeypatch.setattr(prefixes, "_copy", slow_copy)
    assert run(env) == {APPID: "changed"}
    assert not os.path.islink(ssd(env))  # SSD intact
    assert save_of(ssd(env)) == original
    assert not os.path.exists(prefixes.card_prefix(env["mount"], APPID))


def test_invalid_card_destination_goes_to_backups(env):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    broken = prefixes.card_prefix(env["mount"], APPID)
    os.makedirs(broken)  # no `pfx`: only `version` written through the link, for example
    with open(os.path.join(broken, "version"), "w") as f:
        f.write("11.0-100\n")
    assert run(env) == {APPID: "migrated"}
    assert save_of(broken) == original
    assert len(backups(env)) == 1


def test_prefix_mode_refuses_a_steamuser_linked_to_another_card(env, tmp_path):
    make_prefix(ssd(env), save_mtime=NOW - 100)
    other = make_userdata(str(tmp_path / "B" / ".insertplay" / "users" / str(APPID) / "steamuser"), NOW)
    shutil.rmtree(ssd_user(env))
    os.symlink(other, ssd_user(env))
    assert run(env) == {APPID: "foreign"}
    assert not os.path.exists(prefixes.card_prefix(env["mount"], APPID))


def test_unescape_is_a_single_pass():
    assert prefixes._unescape("/run/media/deck/A\134040B") == "/run/media/deck/A\040B"
    assert prefixes._unescape("/run/media/deck/Call\040of\040Juarez\040G") == "/run/media/deck/Call of Juarez G"


def test_dxvk_cache_does_not_count_as_user_data(env):
    """Metro: the DXVK cache is rewritten on every run. Without a save, the device that ran
    last must not beat the one with a save."""
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 1000)
    make_prefix(ssd(env))
    cache = os.path.join(ssd_user(env), "AppData", "Local", "dxvk", "game.dxvk.bin")
    os.makedirs(os.path.dirname(cache))
    with open(cache, "w") as f:
        f.write("cache")
    os.utime(cache, (NOW, NOW))
    assert run(env, mode="userdata") == {APPID: "linked"}
    assert save_in(card) == card


def test_read_only_card_is_not_ok(tmp_path, env):
    """Metro's card on the Deck: ext4 with an error, remounted read-only (`emergency_ro`)."""
    mounts = tmp_path / "mounts"
    point = env["mount"].replace(" ", "\\040")
    mounts.write_text(f"/dev/sda1 {point} ext4 rw,nosuid,nodev,noatime,errors=remount-ro,emergency_ro 0 0\n", encoding="utf-8")
    status = prefixes.status(env["mount"], str(mounts))
    assert status["fsOk"] is False and "somente leitura" in status["reason"]
    mounts.write_text(f"/dev/sda1 {point} ext4 ro,nosuid 0 0\n", encoding="utf-8")
    assert prefixes.status(env["mount"], str(mounts))["fsOk"] is False


# ---- Copilot review on PR #14 --------------------------------------------------


@pytest.mark.parametrize("old, new", [("userdata", "prefix"), ("prefix", "userdata")])
def test_mode_switched_on_another_device_replaces_the_old_link(env, old, new):
    """The other device switched the card's mode. The old mode's link here becomes a copy and the
    new mode takes over, instead of `foreign` forever."""
    make_prefix(ssd(env), save_mtime=NOW - 100)
    run(env, mode=old)
    prefixes._write_mode(env["mount"], new, "0.5.0")  # written by the other device
    result = prefixes.sync(env["mount"], env["libraries"], [APPID], [], [], env["steam"], now=NOW)
    assert result[APPID] in ("migrated", "linked", "conflict")
    if new == "prefix":
        assert os.readlink(ssd(env)) == prefixes.card_prefix(env["mount"], APPID)
        assert not os.path.islink(os.path.join(prefixes.card_prefix(env["mount"], APPID), STEAMUSER))
    else:
        assert not os.path.islink(ssd(env))
        assert os.readlink(ssd_user(env)) == prefixes.card_userdata(env["mount"], APPID)


def test_mode_switch_waits_for_a_running_game(env, monkeypatch):
    make_prefix(ssd(env), save_mtime=NOW - 100)
    run(env, mode="userdata")
    prefixes._write_mode(env["mount"], "prefix", "0.5.0")
    monkeypatch.setattr(prefixes, "LIVE_APPIDS", lambda: {APPID})
    assert prefixes.sync(env["mount"], env["libraries"], [APPID], [], [], env["steam"], now=NOW) == {APPID: "running"}
    assert os.path.islink(ssd_user(env))  # the old link stays until the game closes


def test_game_detected_live_is_not_touched(env, monkeypatch):
    """The frontend's list said nothing was open, but /proc has the game."""
    make_prefix(ssd(env), save_mtime=NOW - 100)
    monkeypatch.setattr(prefixes, "LIVE_APPIDS", lambda: {APPID})
    assert run(env) == {APPID: "running"}
    assert not os.path.islink(ssd(env))


def test_migration_is_abandoned_when_the_game_starts_during_the_copy(env, monkeypatch):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    live = set()
    monkeypatch.setattr(prefixes, "LIVE_APPIDS", lambda: set(live))
    real_copy = prefixes._copy

    def copy_then_launch(src, dest):
        real_copy(src, dest)
        live.add(APPID)

    monkeypatch.setattr(prefixes, "_copy", copy_then_launch)
    assert run(env) == {APPID: "running"}
    assert not os.path.islink(ssd(env))
    assert save_of(ssd(env)) == original
    assert not os.path.exists(prefixes.card_prefix(env["mount"], APPID))


def test_restore_keeps_the_link_when_the_save_changes_during_the_copy(env, monkeypatch):
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    run(env, mode="userdata")
    real_copy = prefixes._copy

    def copy_while_saving(src, dest):
        real_copy(src, dest)
        save = os.path.join(card, "Documents", "save.dat")
        os.utime(save, (NOW - 5, NOW - 5))  # the game saved through the link during the copy

    monkeypatch.setattr(prefixes, "_copy", copy_while_saving)
    assert set_mode(env, "off") == {APPID: "changed"}
    assert prefixes.get_mode(env["mount"]) == "userdata"  # tries again later
    assert os.path.islink(ssd_user(env))


def test_interrupted_restore_stage_does_not_block_the_next_one(env):
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    run(env, mode="userdata")
    stale = ssd_user(env) + ".insertplay-restore"
    shutil.copytree(card, stale, symlinks=True)  # copy finished, swap didn't happen
    assert set_mode(env, "off") == {APPID: "restored"}
    assert save_in(ssd_user(env)) == card
    users = os.path.dirname(ssd_user(env))
    assert any(n.startswith("steamuser.insertplay-restore.old") for n in os.listdir(users))


def test_newer_save_left_by_desktop_steam_wins_over_the_card(env):
    """Desktop-mode Steam uses the library's `steamapps/compatdata`: a save made there is
    newer than the card's and must not silently become a backup."""
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 1000)
    run(env)
    desktop = make_prefix(leftover(env), save_mtime=NOW - 10)
    assert run(env) == {APPID: "conflict"}
    assert save_of(prefixes.card_prefix(env["mount"], APPID)) == desktop
    [backup] = backups(env)
    assert save_of(os.path.join(env["mount"], ".insertplay", "backups", backup)) == card


def test_unlink_dangling_keeps_a_running_games_link(env, monkeypatch):
    make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    run(env, mode="userdata")
    os.rename(env["mount"], env["mount"] + "-out")  # card pulled mid-game
    assert prefixes.unlink_dangling(env["steam"], [APPID]) == []
    monkeypatch.setattr(prefixes, "LIVE_APPIDS", lambda: {APPID})
    assert prefixes.unlink_dangling(env["steam"]) == []
    assert os.path.islink(ssd_user(env))


def test_live_appids_reads_steamappid_from_proc(tmp_path):
    for pid, env in (("10", b"HOME=/home/deck\0SteamAppId=12200\0"), ("11", b"PATH=/usr/bin\0"), ("self", b"")):
        os.makedirs(tmp_path / pid)
        (tmp_path / pid / "environ").write_bytes(env)
    os.makedirs(tmp_path / "12")  # no readable environ
    assert prefixes.live_appids(str(tmp_path)) == {12200}
