"""Detection of mounted InsertPlay cards.

No `decky` import: everything here runs and is tested off the Deck.
"""
import glob
import os

MANIFEST_NAME = "steam-shortcut.json"
LEGACY_DIR = "insertplay"


def default_media_roots() -> list:
    return ["/run/media", "/media"]


def manifest_path(mount: str):
    """Card root first, then `insertplay/` (layout of cards written by the legacy CLI)."""
    root = os.path.join(mount, MANIFEST_NAME)
    if os.path.isfile(root):
        return root
    legacy = os.path.join(mount, LEGACY_DIR, MANIFEST_NAME)
    return legacy if os.path.isfile(legacy) else None


def find_cards(media_roots: list) -> list:
    """Cards with an InsertPlay manifest, each exactly once.

    On SteamOS `/run/media/<label>` is a symlink to `/run/media/deck/<label>`, so both
    glob levels find the same card. We dedupe by realpath and the real mount
    point wins over the symlink (same logic as decky-nonsteam-sync/cards.py).

    A subfolder of a card already found is never another card — without this the
    `insertplay/` folder of the legacy layout, which has its own manifest, would count twice."""
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
    """Compares two `{real: card}` snapshots and returns (inserted, removed)."""
    inserted = [current[k] for k in sorted(current) if k not in previous]
    removed = [previous[k] for k in sorted(previous) if k not in current]
    return inserted, removed


def disk_is_removable(mount: str, sys_root: str = "/sys", resolve=os.path.realpath) -> bool:
    """The mount point's disk declares itself removable in the kernel (`removable` = 1).

    Measured on 2026-10-24: the Steam Machine's USB reader (`/dev/sda1`) has `removable` 1,
    but Steam marks the card's library as `bIsFixed: true`. Without this, a card in a
    USB reader never counts as a card. `removable` is on the disk, not the partition:
    `/sys/dev/block/<maj>:<min>` points to the partition, and the file is in the parent folder."""
    try:
        dev = os.stat(mount).st_dev
        node = resolve(os.path.join(sys_root, "dev", "block", f"{os.major(dev)}:{os.minor(dev)}"))
        for path in (node, os.path.dirname(node)):
            flag = os.path.join(path, "removable")
            if os.path.isfile(flag):
                with open(flag) as f:
                    return f.read().strip() == "1"
    except (OSError, AttributeError):  # AttributeError: os.major doesn't exist outside POSIX
        pass
    return False


def library_card(path: str, media_roots: list, ismount=os.path.ismount, removable=disk_is_removable):
    """Card holding a Steam library (`path` comes from Steam and may be a subfolder,
    like `Games/SteamLibrary`). Returns `{"mount", "label", "insertplay", "removable"}`
    or `None` if the path is not on mounted media.

    `insertplay` checks the manifest on disk, not `find_cards`: Steam reports the library
    before the backend's next scan, and an InsertPlay card that also has a
    Steam library must not get the notification from both flows."""
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
                "removable": removable(current),
            }
        current = os.path.dirname(current)
    return None
