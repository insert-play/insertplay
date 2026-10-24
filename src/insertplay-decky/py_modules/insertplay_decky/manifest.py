"""Leitura do manifesto e da identidade do cartão.

Porta da lógica de InstallSdShortcutUseCase.cs: placeholder `{SD_ROOT}`, artes com
fallback e `--guid` anexado às opções de inicialização. Só leitura: a POC nunca
escreve no cartão — criar a identidade continua sendo trabalho do CLI.
"""
import json
import os
import re

from .cards import LEGACY_DIR, manifest_path

IDENTITY_NAME = "shortcut-identity.json"
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SD_ROOT = "{SD_ROOT}"

# Tipos que o Steam aceita em SetCustomArtworkForApp. O ícone fica de fora da POC:
# não há chamada SteamClient confirmada para ele.
ARTWORK_KINDS = ("capsule", "hero", "logo", "horizontal")


class CardError(Exception):
    pass


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise CardError(f"could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CardError(f"{path} is not a JSON object")
    return data


def _get(data: dict, key: str):
    """O CLI desserializa sem diferenciar maiúsculas; aceitamos `appName` e `AppName`."""
    if key in data:
        return data[key]
    lowered = key.lower()
    for k, v in data.items():
        if k.lower() == lowered:
            return v
    return None


def resolve_placeholder(value: str, sd_root: str) -> str:
    return (value or "").replace(SD_ROOT, sd_root)


def read_guid(mount: str) -> str:
    for path in (os.path.join(mount, IDENTITY_NAME), os.path.join(mount, LEGACY_DIR, IDENTITY_NAME)):
        if os.path.isfile(path):
            guid = str(_get(_read_json(path), "guid") or "").strip()
            if not GUID_RE.match(guid):
                raise CardError(f"invalid guid in {path}: {guid!r}")
            return guid.lower()
    raise CardError(f"{IDENTITY_NAME} not found; prepare the card with build-sd-card.sh")


def _artwork_path(mount: str, value, kind: str):
    if value:
        replaced = resolve_placeholder(str(value), mount).strip()
        path = replaced if os.path.isabs(replaced) else os.path.join(mount, replaced)
        if os.path.isfile(path):
            return os.path.normpath(path)
    for fallback in (os.path.join(mount, "steam-artwork", f"{kind}.png"),
                     os.path.join(mount, LEGACY_DIR, "steam-artwork", f"{kind}.png")):
        if os.path.isfile(fallback):
            return os.path.normpath(fallback)
    return None


def load_card(mount: str) -> dict:
    """Tudo o que o frontend precisa para criar ou atualizar o atalho deste cartão."""
    path = manifest_path(mount)
    if not path:
        raise CardError(f"manifest not found in {mount}")
    shortcut = _get(_read_json(path), "shortcut")
    if not isinstance(shortcut, dict):
        raise CardError(f"{path} has no 'shortcut' object")
    app_name = str(_get(shortcut, "appName") or "").strip()
    launch = _get(shortcut, "launch")
    if not app_name or not isinstance(launch, dict) or not _get(launch, "exe"):
        raise CardError(f"{path} needs shortcut.appName and shortcut.launch.exe")

    guid = read_guid(mount)
    args = resolve_placeholder(str(_get(launch, "args") or ""), mount).strip()
    launch_options = f"{args} --guid {guid}" if args else f"--guid {guid}"
    artwork_def = _get(shortcut, "artwork") or {}
    artwork = {}
    for kind in ARTWORK_KINDS:
        found = _artwork_path(mount, _get(artwork_def, kind) if isinstance(artwork_def, dict) else None, kind)
        if found:
            artwork[kind] = found

    return {
        "guid": guid,
        "appName": app_name,
        "exe": resolve_placeholder(str(_get(launch, "exe")), mount),
        "startDir": resolve_placeholder(str(_get(launch, "startDir") or mount), mount),
        "launchOptions": launch_options,
        "artwork": artwork,
    }
