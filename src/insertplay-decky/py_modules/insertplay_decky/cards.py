"""Detecção de cartões InsertPlay montados.

Sem import de `decky`: tudo aqui roda e é testado fora do Deck.
"""
import glob
import os

MANIFEST_NAME = "steam-shortcut.json"
LEGACY_DIR = "insertplay"


def default_media_roots() -> list:
    return ["/run/media", "/media"]


def manifest_path(mount: str):
    """Mesma regra do ManifestPathResolver.cs: raiz primeiro, depois `insertplay/`."""
    root = os.path.join(mount, MANIFEST_NAME)
    if os.path.isfile(root):
        return root
    legacy = os.path.join(mount, LEGACY_DIR, MANIFEST_NAME)
    return legacy if os.path.isfile(legacy) else None


def find_cards(media_roots: list) -> list:
    """Cartões com manifesto InsertPlay, cada um exatamente uma vez.

    No SteamOS `/run/media/<rótulo>` é symlink para `/run/media/deck/<rótulo>`, então os
    dois níveis do glob acham o mesmo cartão. Deduplicamos por realpath e o ponto de
    montagem real vence o symlink (mesma lógica de decky-nonsteam-sync/cards.py).

    Uma subpasta de um cartão já encontrado nunca é outro cartão — sem isso a pasta
    `insertplay/` do layout legado, que tem o próprio manifesto, contaria duas vezes."""
    cards = {}
    for root in media_roots:
        for pattern in (os.path.join(root, "*"), os.path.join(root, "*", "*")):
            for mount in glob.glob(pattern):
                if not os.path.isdir(mount) or not manifest_path(mount):
                    continue
                real = os.path.realpath(mount)
                if os.path.dirname(real) in cards:
                    continue
                known = cards.get(real)
                if known and known["mount"] == real:
                    continue
                cards[real] = {"mount": mount, "label": os.path.basename(mount), "real": real}
    return sorted(cards.values(), key=lambda c: c["real"])


def diff(previous: dict, current: dict) -> tuple:
    """Compara dois instantâneos `{real: card}` e devolve (inseridos, removidos)."""
    inserted = [current[k] for k in sorted(current) if k not in previous]
    removed = [previous[k] for k in sorted(previous) if k not in current]
    return inserted, removed
