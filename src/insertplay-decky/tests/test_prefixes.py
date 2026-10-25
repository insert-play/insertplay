"""Save portátil (issue #13). Symlink de verdade: só roda em Linux/WSL."""
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from insertplay_decky import prefixes  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "posix", reason="symlink e /proc/mounts são do Linux")

APPID = 12200
NOW = 1_800_000_000.0
STEAMUSER = os.path.join("pfx", "drive_c", "users", "steamuser")


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
    """Prefixo com `pfx/`. `save_mtime` cria um arquivo de usuário (o save) com esse mtime;
    sem ele, o prefixo é como o que o Steam cria num move: nenhum arquivo de usuário."""
    users = os.path.join(path, STEAMUSER)
    os.makedirs(os.path.join(users, "AppData", "Local", "Temp"), exist_ok=True)
    os.symlink("./Documents", os.path.join(users, "My Documents"))  # como o Proton cria
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
    """Só a pasta `steamuser` (como fica no cartão no modo userdata)."""
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


# ---- modo prefixo ----------------------------------------------------------------


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
    assert os.path.islink(os.path.join(dest, STEAMUSER, "My Documents"))  # link interno copiado como link
    assert len(ssd_backups(env)) == 1  # o original fica no SSD, nada apagado


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
    """Depois de um move o Steam cria um prefixo vazio no SSD, mais novo que tudo."""
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 1000)
    make_prefix(ssd(env))  # sem arquivo de usuário
    assert run(env) == {APPID: "linked"}  # sem save do outro lado: não é conflito
    assert os.readlink(ssd(env)) == card
    assert len(ssd_backups(env)) == 1  # mas fica guardado


def test_newest_save_wins_and_card_copy_becomes_backup(env):
    """Jogado depois num aparelho com plugin antigo: o save do SSD é o mais recente."""
    card = make_prefix(prefixes.card_prefix(env["mount"], APPID), save_mtime=NOW - 1000)
    ssd_prefix = make_prefix(ssd(env), save_mtime=NOW - 10)
    assert run(env) == {APPID: "conflict"}
    assert save_of(card) == ssd_prefix  # o cartão agora tem o save do SSD
    [backup] = backups(env)
    assert save_of(os.path.join(env["mount"], ".insertplay", "backups", backup)) == card


def test_prefix_leftover_from_a_move_is_adopted(env):
    """Move SSD → cartão sem link: o Steam leva o prefixo para `steamapps/compatdata` e
    recria um vazio no SSD."""
    moved = make_prefix(leftover(env), save_mtime=NOW - 500)
    make_prefix(ssd(env))
    assert run(env) == {APPID: "migrated"}
    assert save_of(prefixes.card_prefix(env["mount"], APPID)) == moved
    assert not os.path.exists(leftover(env))  # não bloqueia mais o move de volta


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
    assert save_of(card) == card  # cartão intacto


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
    os.symlink(str(tmp_path / "gone" / "x"), ssd(env, 111))  # link alheio quebrado
    os.rename(env["mount"], env["mount"] + "-out")  # cartão "removido"
    assert prefixes.unlink_dangling(env["steam"]) == [APPID]
    assert not os.path.lexists(ssd(env))
    assert os.path.islink(ssd(env, 111))


# ---- modo userdata (só steamuser) -----------------------------------------------


def ssd_user(env, appid=APPID):
    return os.path.join(ssd(env, appid), STEAMUSER)


def test_userdata_moves_only_steamuser(env):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    assert run(env, mode="userdata") == {APPID: "migrated"}
    dest = prefixes.card_userdata(env["mount"], APPID)
    assert os.readlink(ssd_user(env)) == dest
    assert save_in(dest) == original
    assert os.path.isdir(os.path.join(ssd(env), "pfx", "drive_c", "windows"))  # o resto fica no SSD
    assert not os.path.islink(ssd(env))
    users = os.path.dirname(ssd_user(env))
    assert len(ssd_backups(env, users)) == 1  # steamuser original guardado ao lado


def test_userdata_builds_the_skeleton_on_a_device_where_the_game_never_ran(env):
    """Steam Machine com o cartão do Deck: sem prefixo. Medido: o Proton monta o prefixo
    em volta do link e o save aparece já na primeira abertura."""
    dest = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    assert run(env, mode="userdata") == {APPID: "linked"}
    assert os.readlink(ssd_user(env)) == dest


def test_userdata_fresh_steamuser_loses_quietly(env):
    dest = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 1000)
    make_prefix(ssd(env))  # o jogo rodou aqui sem o cartão e não salvou nada
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
    make_prefix(leftover(env), save_mtime=NOW - 100)  # move SSD → cartão copiou o prefixo
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
    assert not os.path.islink(ssd(env))  # o prefixo voltou ao SSD
    assert os.readlink(ssd_user(env)) == prefixes.card_userdata(env["mount"], APPID)
    assert save_in(ssd_user(env)) == original


def test_choosing_the_active_mode_again_changes_nothing(env):
    """Painel desatualizado manda o mesmo modo duas vezes (visto no Deck): não pode copiar
    de volta, refazer o link nem avisar conflito."""
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
    shutil.copytree(card, ssd_user(env), symlinks=True)  # mesma data: cópia do mesmo save
    assert run(env, mode="userdata") == {APPID: "linked"}
    assert len(ssd_backups(env, os.path.dirname(ssd_user(env)))) == 1


def test_userdata_unlink_dangling(env):
    make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    run(env, mode="userdata")
    os.rename(env["mount"], env["mount"] + "-out")
    assert prefixes.unlink_dangling(env["steam"]) == [APPID]
    assert not os.path.lexists(ssd_user(env))


# ---- cartão --------------------------------------------------------------------


def test_mode_lives_on_the_card(env):
    assert prefixes.get_mode(env["mount"]) == "off"
    set_mode(env, "userdata", appids=())
    assert prefixes.get_mode(env["mount"]) == "userdata"
    with open(prefixes.config_path(env["mount"])) as f:
        assert json.load(f) == {"mode": "userdata", "minVersion": "0.5.0"}
    with pytest.raises(ValueError):
        set_mode(env, "tudo")


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


# ---- achados da revisão ---------------------------------------------------------


def test_off_on_another_device_releases_this_devices_links(env):
    """Desligado no outro aparelho: o link que este ainda tem vira cópia, senão o save
    sumiria daqui quando o cartão saísse."""
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 100)
    make_prefix(ssd(env))
    run(env, mode="userdata")
    prefixes._write_mode(env["mount"], "off", "0.5.0")  # o que o outro aparelho gravou
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
    assert prefixes.get_mode(env["mount"]) == "userdata"  # tenta de novo depois
    assert os.path.islink(ssd_user(env))  # o link ficou: o jogo continua com o save


def test_copy_is_discarded_when_the_game_saves_during_it(env, monkeypatch):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    real_copy = prefixes._copy

    def slow_copy(src, dest):
        real_copy(src, dest)
        save = os.path.join(src, STEAMUSER, "Documents", "save.dat")
        os.utime(save, (NOW - 5, NOW - 5))  # o jogo abriu e salvou no meio da cópia

    monkeypatch.setattr(prefixes, "_copy", slow_copy)
    assert run(env) == {APPID: "changed"}
    assert not os.path.islink(ssd(env))  # SSD intacto
    assert save_of(ssd(env)) == original
    assert not os.path.exists(prefixes.card_prefix(env["mount"], APPID))


def test_invalid_card_destination_goes_to_backups(env):
    original = make_prefix(ssd(env), save_mtime=NOW - 100)
    broken = prefixes.card_prefix(env["mount"], APPID)
    os.makedirs(broken)  # sem `pfx`: só `version` gravado pelo link, por exemplo
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
    """Metro: o cache do DXVK é regravado a cada execução. Sem save, o aparelho que rodou
    por último não pode vencer quem tem save."""
    card = make_userdata(prefixes.card_userdata(env["mount"], APPID), NOW - 1000)
    make_prefix(ssd(env))
    cache = os.path.join(ssd_user(env), "AppData", "Local", "dxvk", "game.dxvk.bin")
    os.makedirs(os.path.dirname(cache))
    with open(cache, "w") as f:
        f.write("cache")
    os.utime(cache, (NOW, NOW))
    assert run(env, mode="userdata") == {APPID: "linked"}
    assert save_in(card) == card
