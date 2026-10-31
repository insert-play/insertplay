"""Plugin RPC layer. The only module that imports `decky`; the logic lives in
py_modules/insertplay_decky and is tested without the Deck."""
import asyncio
import base64
import functools
import os
import sys

import decky

sys.path.append(os.path.join(os.path.dirname(__file__), "py_modules"))

from insertplay_decky import cards, icons, plugin_package, prefixes  # noqa: E402
from insertplay_decky.manifest import GUID_RE, CardError, load_card  # noqa: E402
from insertplay_decky.store import EventLog, Registry, Settings  # noqa: E402

POLL_SECONDS = 1.0
READY_TIMEOUT_SECONDS = 15.0


def guarded(fallback):
    """An exception in an RPC becomes an empty response + a log line, never a stuck plugin.

    Every RPC waits for `_main` to finish the first scan: Decky serves RPCs in
    parallel with `_main`, and before that `mounted` and the registry don't exist. Answering
    "no card" or "empty registry" in that window made the frontend hide the card
    that was present or create a duplicate shortcut (PR #2 review)."""

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
    """Detected card + parsed manifest. A read error goes in the payload instead of
    breaking detection: the panel has to show *why* the card is unusable."""
    out = {"mount": card["mount"], "label": card["label"], "error": None}
    try:
        out.update(load_card(card["mount"]))
    except CardError as exc:
        out["error"] = str(exc)
    return out


def _on_mounted_card(mounted: dict, path: str) -> str:
    """Real path of `path`, as long as it is inside a mounted card. RPCs that
    read or write files must not become arbitrary disk access."""
    real = os.path.realpath(path)
    if not any(real == root or real.startswith(root.rstrip(os.sep) + os.sep) for root in mounted):
        raise PermissionError(f"{path} is not on a mounted InsertPlay card")
    return real


def _steam_card(mount: str, libraries: list) -> tuple:
    """Real mount point of a card with a Steam library, and its libraries.
    The portable saves RPCs write to the card and to the SSD `compatdata`: the card has
    to be truly mounted media, and every library has to be inside it."""
    card = cards.library_card(mount, cards.default_media_roots())
    real = os.path.realpath(mount)
    if not card or card["mount"] != real:
        raise PermissionError(f"{mount} is not a mounted card")
    libs = [os.path.realpath(lib) for lib in libraries]
    if any(not (lib == real or lib.startswith(real + os.sep)) for lib in libs):
        raise PermissionError(f"a library is not on {mount}")
    return real, libs


def _steam_root() -> str:
    home = getattr(decky, "DECKY_USER_HOME", None) or os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "Steam")


class Plugin:
    # class attribute: exists before `_main` runs, when RPCs already arrive
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
        # The first snapshot is the initial state, not an insertion: the frontend reads it
        # with current_cards() and reconciles without auto-launch. So restarting
        # plugin_loader with the card inserted doesn't start the game by itself.
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

    # fallback None, not []: "don't know" must not look like "no card" (the frontend hides
    # the shortcuts of every card not in the list)
    @guarded(lambda: None)
    async def current_cards(self) -> list:
        return [await asyncio.to_thread(_describe, c) for c in self.mounted.values()]

    @guarded(str)
    async def artwork_base64(self, path: str) -> str:
        """Only serves files from inside a mounted card — the RPC must not become
        an arbitrary disk read."""
        real = _on_mounted_card(self.mounted, path)
        if real in self.mounted:
            raise PermissionError(f"{path} is a card, not a file")
        with open(real, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    def _prune_icons(self, icons_dir: str, keep: set) -> None:
        """Cleanup is optional: an unreadable `shortcuts.vdf` only postpones it, never breaks the RPC."""
        try:
            used = icons.referenced_icons(_steam_root())
        except Exception as exc:
            decky.logger.warning("InsertPlay: icon cleanup skipped: %s", exc)
            return
        removed = icons.prune(icons_dir, used | keep)
        if removed:
            self.log.add("icon", f"removed unused: {', '.join(removed)}")

    # None = "don't know": the frontend leaves the icon alone
    @guarded(lambda: None)
    async def shortcut_icon(self, steamid: str, appid: int, guid: str, source: str) -> dict:
        """Copies the card's icon to the SSD and tells whether the frontend should apply it with
        `SetShortcutIcon` (issue #17). Never applied over an icon the user chose."""
        real = _on_mounted_card(self.mounted, source)
        if real in self.mounted or not os.path.isfile(real):
            raise PermissionError(f"{source} is not a file on a card")
        if not GUID_RE.match(guid or ""):
            raise ValueError(f"invalid guid {guid!r}")
        guid = guid.lower()
        icons_dir = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "icons")
        # without shortcuts.vdf there's no telling whose the current icon is: raise, don't apply
        current = (await asyncio.to_thread(icons.shortcut_icons, icons.shortcuts_vdf(_steam_root(), steamid))).get(
            int(appid)
        )
        target = await asyncio.to_thread(icons.target_path, current, real, guid, icons_dir)
        apply = icons.plan(current, target, icons_dir)
        if apply:
            await asyncio.to_thread(icons.stage, real, target)
        await asyncio.to_thread(self._prune_icons, icons_dir, {target} if apply else set())
        return {"path": target, "apply": apply, "current": current}

    # None = "don't know": the frontend treats it as an InsertPlay card and notifies nothing
    @guarded(lambda: None)
    async def library_card(self, path: str) -> dict:
        """Card holding a Steam library, and whether it is an InsertPlay card (issue #10)."""
        return await asyncio.to_thread(cards.library_card, path, cards.default_media_roots())

    @guarded(lambda: None)
    async def package_status(self) -> dict:
        """Installed version and, per card, the version of the plugin package it carries."""
        installed = decky.DECKY_PLUGIN_VERSION
        out = []
        for card in self.mounted.values():
            status = await asyncio.to_thread(plugin_package.card_status, card["mount"], installed)
            out.append({"mount": card["mount"], "label": card["label"], **status})
        return {"installed": installed, "cards": out}

    @guarded(lambda: None)
    async def write_package_to_card(self, mount: str) -> dict:
        """Writes the installed plugin version to the card. Returns the written package's status."""
        real = _on_mounted_card(self.mounted, mount)
        if real not in self.mounted:
            raise PermissionError(f"{mount} is not the root of a mounted card")
        card_mount = self.mounted[real]["mount"]
        written = await asyncio.to_thread(plugin_package.write_card_package, decky.DECKY_PLUGIN_DIR, card_mount)
        self.log.add("package", f"{card_mount}: wrote plugin v{written['version']}")
        # build_zip_from_dir already left the result in the cache: this doesn't reread the zip
        return await asyncio.to_thread(plugin_package.card_status, card_mount, decky.DECKY_PLUGIN_VERSION)

    # None, not {}: a registry "empty" because of a failure would make the frontend create duplicate shortcuts
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

    def _log_portable(self, mount: str, results: dict) -> None:
        quiet = ("ok", "none", "running")
        changed = {appid: r for appid, r in results.items() if r not in quiet}
        if changed:
            detail = ", ".join(f"{appid} {r}" for appid, r in sorted(changed.items()))
            self.log.add("portable", f"{os.path.basename(mount)}: {detail}")

    # None = "don't know": the panel keeps the toggle disabled
    @guarded(lambda: None)
    async def portable_status(self, mount: str) -> dict:
        real, _ = _steam_card(mount, [])
        return await asyncio.to_thread(prefixes.status, real)

    @guarded(lambda: None)
    async def portable_set(
        self, mount: str, mode: str, libraries: list, appids: list, running: list, elsewhere: list
    ) -> dict:
        """Portable saves mode for the whole card: "off", "userdata" or "prefix". Leaving
        a mode copies back to the SSD; with a card game open, nothing changes."""
        real, libs = _steam_card(mount, libraries)
        if mode != "off":
            status = await asyncio.to_thread(prefixes.status, real)
            if not status["fsOk"]:
                raise ValueError(status["reason"])
        results = await asyncio.to_thread(
            prefixes.set_mode, real, mode, libs, appids, running, elsewhere, _steam_root(), decky.DECKY_PLUGIN_VERSION
        )
        status = await asyncio.to_thread(prefixes.status, real)
        if status["mode"] == mode:
            self.log.add("portable", f"{os.path.basename(real)}: mode {mode}")
        self._log_portable(real, results)
        return {"status": status, "results": results}

    @guarded(lambda: None)
    async def portable_sync(self, mount: str, libraries: list, appids: list, running: list, elsewhere: list) -> dict:
        """One pass over a card. When off, does nothing (returns {})."""
        real, libs = _steam_card(mount, libraries)
        status = await asyncio.to_thread(prefixes.status, real)
        # Unwritable card (ext4 remounted read-only, for example) with the mode on: don't
        # link anything, or the game would keep its prefix or save somewhere it can't write.
        # When off, the pass goes ahead: it only copies from the card back to the SSD.
        if status["mode"] != "off" and not status["fsOk"]:
            return {}
        results = await asyncio.to_thread(prefixes.sync, real, libs, appids, running, elsewhere, _steam_root())
        self._log_portable(real, results)
        return results

    @guarded(lambda: None)
    async def portable_unlink_dangling(self, running: list = ()) -> list:
        removed = await asyncio.to_thread(prefixes.unlink_dangling, _steam_root(), running)
        if removed:
            self.log.add("portable", f"unlinked (card absent): {', '.join(map(str, removed))}")
        return removed

    @guarded(lambda: None)
    async def log_add(self, kind: str, message: str) -> None:
        self.log.add(kind, message)

    @guarded(list)
    async def log_tail(self, count: int = 30) -> list:
        return self.log.tail(count)
