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
