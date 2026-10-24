"""Camada RPC do plugin. É o único módulo que importa `decky`; a lógica fica em
py_modules/insertplay_decky e é testada sem o Deck."""
import asyncio
import base64
import functools
import os
import sys

import decky

sys.path.append(os.path.join(os.path.dirname(__file__), "py_modules"))

from insertplay_decky import cards, plugin_package  # noqa: E402
from insertplay_decky.manifest import CardError, load_card  # noqa: E402
from insertplay_decky.store import EventLog, Registry, Settings  # noqa: E402

POLL_SECONDS = 1.0
READY_TIMEOUT_SECONDS = 15.0


def guarded(fallback):
    """Exceção numa RPC vira resposta vazia + linha no log, nunca um plugin travado.

    Toda RPC espera o `_main` terminar a primeira varredura: o Decky atende RPCs em
    paralelo com o `_main`, e antes disso `mounted` e o registro não existem. Responder
    "nenhum cartão" ou "registro vazio" nessa janela fazia o frontend ocultar o cartão
    presente ou criar um atalho duplicado (review do PR #2)."""

    def wrap(method):
        @functools.wraps(method)
        async def inner(self, *args, **kwargs):
            try:
                await self._wait_ready()
                return await method(self, *args, **kwargs)
            except Exception as exc:
                decky.logger.exception("InsertPlay: %s failed", method.__name__)
                try:
                    self.log.add("error", f"{method.__name__}: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
                return fallback()

        return inner

    return wrap


def _describe(card: dict) -> dict:
    """Cartão detectado + manifesto lido. Erro de leitura vai no payload em vez de
    derrubar a detecção: o painel precisa mostrar *por que* o cartão não serve."""
    out = {"mount": card["mount"], "label": card["label"], "error": None}
    try:
        out.update(load_card(card["mount"]))
    except CardError as exc:
        out["error"] = str(exc)
    return out


def _on_mounted_card(mounted: dict, path: str) -> str:
    """Caminho real de `path`, desde que esteja dentro de um cartão montado. As RPCs que
    leem ou escrevem arquivos não podem virar acesso arbitrário ao disco."""
    real = os.path.realpath(path)
    if not any(real == root or real.startswith(root.rstrip(os.sep) + os.sep) for root in mounted):
        raise PermissionError(f"{path} is not on a mounted InsertPlay card")
    return real


class Plugin:
    # atributo de classe: existe antes de o `_main` rodar, quando as RPCs já chegam
    ready = False

    async def _wait_ready(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + READY_TIMEOUT_SECONDS
        while not self.ready:
            if loop.time() > deadline:
                raise TimeoutError("initial card scan did not finish")
            await asyncio.sleep(0.1)

    def _setup(self):
        settings_dir = decky.DECKY_PLUGIN_SETTINGS_DIR
        self.registry = Registry(os.path.join(settings_dir, "registry.json"))
        self.settings = Settings(os.path.join(settings_dir, "settings.json"))
        self.log = EventLog(os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "events.jsonl"))
        self.mounted = {}
        self.watcher = None

    async def _main(self):
        self._setup()
        # O primeiro instantâneo é o estado inicial, não uma inserção: o frontend o lê
        # com current_cards() e reconcilia sem auto-launch. Assim reiniciar o
        # plugin_loader com o cartão inserido não inicia o jogo sozinho.
        self.mounted = {c["real"]: c for c in await asyncio.to_thread(cards.find_cards, cards.default_media_roots())}
        self.ready = True
        self.watcher = asyncio.get_event_loop().create_task(self._watch())
        decky.logger.info("InsertPlay: started with %d card(s)", len(self.mounted))

    async def _unload(self):
        if self.watcher:
            self.watcher.cancel()

    async def _watch(self):
        while True:
            try:
                found = await asyncio.to_thread(cards.find_cards, cards.default_media_roots())
                current = {c["real"]: c for c in found}
                inserted, removed = cards.diff(self.mounted, current)
                self.mounted = current
                for card in removed:
                    self.log.add("removed", card["mount"])
                    await decky.emit("card_removed", {"mount": card["mount"], "label": card["label"]})
                for card in inserted:
                    payload = await asyncio.to_thread(_describe, card)
                    self.log.add("inserted", f"{card['mount']}: {payload.get('appName') or payload['error']}")
                    await decky.emit("card_inserted", payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                decky.logger.exception("InsertPlay: watcher iteration failed")
            await asyncio.sleep(POLL_SECONDS)

    # fallback None, não []: "não sei" não pode parecer "nenhum cartão" (o frontend oculta
    # os atalhos de todo cartão que não está na lista)
    @guarded(lambda: None)
    async def current_cards(self) -> list:
        return [await asyncio.to_thread(_describe, c) for c in self.mounted.values()]

    @guarded(str)
    async def artwork_base64(self, path: str) -> str:
        """Só entrega arquivos de dentro de um cartão montado — a RPC não pode virar
        leitura arbitrária do disco."""
        real = _on_mounted_card(self.mounted, path)
        if real in self.mounted:
            raise PermissionError(f"{path} is a card, not a file")
        with open(real, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    @guarded(lambda: None)
    async def package_status(self) -> dict:
        """Versão instalada e, por cartão, a versão do pacote do plugin que ele traz."""
        installed = decky.DECKY_PLUGIN_VERSION
        out = []
        for card in self.mounted.values():
            status = await asyncio.to_thread(plugin_package.card_status, card["mount"], installed)
            out.append({"mount": card["mount"], "label": card["label"], **status})
        return {"installed": installed, "cards": out}

    @guarded(lambda: None)
    async def write_package_to_card(self, mount: str) -> dict:
        """Grava a versão instalada do plugin no cartão. Devolve o status do pacote gravado."""
        real = _on_mounted_card(self.mounted, mount)
        if real not in self.mounted:
            raise PermissionError(f"{mount} is not the root of a mounted card")
        card_mount = self.mounted[real]["mount"]
        dest = plugin_package.card_package_path(card_mount)
        written = await asyncio.to_thread(plugin_package.build_zip_from_dir, decky.DECKY_PLUGIN_DIR, dest)
        self.log.add("package", f"{card_mount}: wrote plugin v{written['version']}")
        # build_zip_from_dir já deixou o resultado no cache: isto não relê o zip
        return await asyncio.to_thread(plugin_package.card_status, card_mount, decky.DECKY_PLUGIN_VERSION)

    # None, não {}: um registro "vazio" por falha faria o frontend criar atalhos duplicados
    @guarded(lambda: None)
    async def registry_all(self) -> dict:
        return self.registry.all()

    @guarded(lambda: None)
    async def registry_put(self, guid: str, record: dict) -> dict:
        return self.registry.put(guid, record)

    @guarded(lambda: None)
    async def registry_forget(self, guid: str) -> None:
        self.registry.forget(guid)

    @guarded(dict)
    async def get_settings(self) -> dict:
        return self.settings.all()

    @guarded(dict)
    async def set_setting(self, key: str, value) -> dict:
        return self.settings.set(key, value)

    @guarded(lambda: None)
    async def log_add(self, kind: str, message: str) -> None:
        self.log.add(kind, message)

    @guarded(list)
    async def log_tail(self, count: int = 30) -> list:
        return self.log.tail(count)
