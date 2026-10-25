"""RPCs do main.py com um módulo `decky` falso: a espera pela primeira varredura e os
fallbacks que não podem parecer "vazio"."""
import asyncio
import importlib
import logging
import os
import sys
import types

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture
def main(tmp_path, monkeypatch):
    fake = types.ModuleType("decky")
    fake.DECKY_PLUGIN_SETTINGS_DIR = str(tmp_path / "settings")
    fake.DECKY_PLUGIN_RUNTIME_DIR = str(tmp_path / "data")
    fake.logger = logging.getLogger("fake-decky")

    async def emit(*args):
        pass

    fake.emit = emit
    monkeypatch.setitem(sys.modules, "decky", fake)
    monkeypatch.syspath_prepend(ROOT)
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    # nenhuma varredura de verdade em /run/media durante o teste
    monkeypatch.setattr(module.cards, "default_media_roots", lambda: [str(tmp_path / "media")])
    yield module
    sys.modules.pop("main", None)


def test_rpc_waits_for_the_initial_scan(main):
    async def scenario():
        plugin = main.Plugin()
        early = asyncio.create_task(plugin.current_cards())
        await asyncio.sleep(0.2)
        assert not early.done()  # sem varredura, não responde "nenhum cartão"
        await plugin._main()
        result = await early
        await plugin._unload()
        return result

    assert asyncio.run(scenario()) == []


def test_unknown_state_is_none_not_empty(main, monkeypatch):
    monkeypatch.setattr(main, "READY_TIMEOUT_SECONDS", 0.2)

    async def scenario():
        plugin = main.Plugin()  # _main nunca roda
        return await plugin.current_cards(), await plugin.registry_all(), await plugin.registry_put("g", {})

    assert asyncio.run(scenario()) == (None, None, None)


def test_registry_round_trip_after_ready(main):
    async def scenario():
        plugin = main.Plugin()
        await plugin._main()
        await plugin.registry_put("g", {"appid": 7})
        result = await plugin.registry_all()
        await plugin._unload()
        return result

    assert asyncio.run(scenario())["g"]["appid"] == 7


def _plugin_dir(root, version):
    plugin = root / "installed" / "InsertPlay"
    (plugin / "dist").mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name": "InsertPlay", "flags": []}', encoding="utf-8")
    (plugin / "package.json").write_text('{"version": "%s"}' % version, encoding="utf-8")
    (plugin / "main.py").write_text("", encoding="utf-8")
    (plugin / "dist" / "index.js").write_text("", encoding="utf-8")
    return plugin


def test_write_package_to_card_and_status(main, tmp_path):
    main.decky.DECKY_PLUGIN_DIR = str(_plugin_dir(tmp_path, "0.2.0"))
    main.decky.DECKY_PLUGIN_VERSION = "0.2.0"
    card = tmp_path / "media" / "SD1"
    card.mkdir(parents=True)
    (card / "steam-shortcut.json").write_text("{}", encoding="utf-8")

    async def scenario():
        plugin = main.Plugin()
        await plugin._main()
        before = await plugin.package_status()
        written = await plugin.write_package_to_card(str(card))
        after = await plugin.package_status()
        outside = await plugin.write_package_to_card(str(tmp_path))
        inside = await plugin.write_package_to_card(str(card / "insertplay"))
        await plugin._unload()
        return before, written, after, outside, inside

    before, written, after, outside, inside = asyncio.run(scenario())
    assert before["installed"] == "0.2.0"
    assert [c["relation"] for c in before["cards"]] == ["missing"]
    assert written["relation"] == "same" and written["version"] == "0.2.0"
    assert after["cards"][0]["zipPath"].endswith(os.path.join("insertplay", "decky", "InsertPlay.zip"))
    # fora de um cartão, ou numa subpasta dele, é recusado (fallback None)
    assert outside is None and inside is None


def test_portable_rpcs_refuse_paths_outside_mounted_media(main, tmp_path):
    async def scenario():
        plugin = main.Plugin()
        await plugin._main()
        args = (str(tmp_path / "home" / "deck"), "userdata", [], [1], [], [])
        result = await plugin.portable_set(*args), await plugin.portable_status(args[0])
        await plugin._unload()
        return result

    assert asyncio.run(scenario()) == (None, None)


@pytest.mark.skipif(os.name != "posix", reason="symlink")
def test_portable_enable_then_disable_round_trip(main, tmp_path, monkeypatch):
    mount = tmp_path / "media" / "deck" / "Bully"
    (mount / "steamapps").mkdir(parents=True)
    ssd = tmp_path / "home" / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / "12200"
    (ssd / "pfx" / "drive_c" / "users" / "steamuser").mkdir(parents=True)
    (ssd / "pfx" / "drive_c" / "users" / "steamuser" / "save.dat").write_text("fase 3", encoding="utf-8")
    main.decky.DECKY_USER_HOME = str(tmp_path / "home")
    main.decky.DECKY_PLUGIN_VERSION = "0.5.0"
    real = os.path.realpath(mount)
    monkeypatch.setattr(main.cards, "library_card", lambda path, roots: {"mount": real} if os.path.realpath(path) == real else None)
    monkeypatch.setattr(main.prefixes, "fs_type", lambda m, mounts_file="/proc/mounts": "ext4")

    async def scenario():
        plugin = main.Plugin()
        await plugin._main()
        on = await plugin.portable_set(str(mount), "prefix", [str(mount)], [12200], [], [])
        linked = os.path.islink(ssd)
        off = await plugin.portable_set(str(mount), "off", [str(mount)], [12200], [], [])
        await plugin._unload()
        return on, linked, off

    on, linked, off = asyncio.run(scenario())
    assert on["results"] == {12200: "migrated"} and on["status"]["mode"] == "prefix"
    assert linked
    assert off["results"] == {12200: "restored"} and off["status"]["mode"] == "off"
    assert (ssd / "pfx" / "drive_c" / "users" / "steamuser" / "save.dat").read_text(encoding="utf-8") == "fase 3"
    assert (mount / ".insertplay" / "prefixes" / "12200" / "pfx").is_dir()  # cartão intacto
