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
