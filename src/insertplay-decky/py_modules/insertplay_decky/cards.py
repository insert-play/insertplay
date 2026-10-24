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


def library_card(path: str, media_roots: list, ismount=os.path.ismount):
    """Cartão onde fica uma biblioteca Steam (`path` vem do Steam e pode ser subpasta,
    como `Games/SteamLibrary`). Devolve `{"mount", "label", "insertplay"}` ou `None` se
    o caminho não está em mídia removível montada.

    `insertplay` olha o manifesto no disco, não `find_cards`: o Steam avisa da biblioteca
    antes da próxima varredura do backend, e o cartão InsertPlay que também tem
    biblioteca Steam não pode ganhar a notificação dos dois fluxos."""
    roots = [os.path.realpath(r) for r in media_roots]
    current = os.path.realpath(path)
    if not any(current.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
        return None
    while current not in roots and current != os.path.dirname(current):
        if ismount(current):
            return {
                "mount": current,
                "label": os.path.basename(current),
                "insertplay": manifest_path(current) is not None,
            }
        current = os.path.dirname(current)
    return None
