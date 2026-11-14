"""Where a game keeps its save: one class per strategy, in a registry.

A strategy turns the game folder (`root`) and the manifest's `saves` block into a
`SaveSpec`: the save patterns (relative to `root`, `*` allowed in one segment) and the
exclusions (relative to each save root). Contract: docs/card-format.md#saves.
"""
import fnmatch
import json
import os

MANIFEST_NAME = "steam-shortcut.json"
LEGACY_DIR = "insertplay"


class StrategyError(Exception):
    """No usable strategy: unknown name, `custom-path` without paths, bad path, or nothing
    to infer from. The game still opens, without sync."""


class SaveSpec:
    def __init__(self, strategy: str, patterns: list, exclude: list, source: str):
        self.strategy = strategy
        self.patterns = patterns
        self.exclude = exclude
        self.source = source  # "manifest", "server" or "inferred"

    def as_json(self) -> dict:
        return {"strategy": self.strategy, "paths": self.patterns, "exclude": self.exclude}

    def __repr__(self) -> str:
        return f"SaveSpec({self.strategy!r}, {self.patterns!r}, exclude={self.exclude!r}, {self.source})"


def check_rel(path) -> str:
    """A relative path with `/`, no `..`, no empty segment, no absolute start."""
    if not isinstance(path, str) or not path.strip():
        raise StrategyError(f"bad save path {path!r}")
    path = path.strip().replace("\\", "/")
    if path.startswith("/"):
        raise StrategyError(f"bad save path {path!r}: absolute")
    path = path.rstrip("/")
    parts = path.split("/")
    if not path or any(p in ("", ".", "..") for p in parts) or ":" in parts[0] or "\0" in path:
        raise StrategyError(f"bad save path {path!r}")
    return path


def expand(root: str, pattern: str) -> list:
    """Existing paths (files or folders, never symlinks) matching `pattern`, relative to `root`."""
    found = [""]
    for segment in pattern.split("/"):
        nxt = []
        for base in found:
            folder = os.path.join(root, base) if base else root
            if any(c in segment for c in "*?["):
                try:
                    names = sorted(os.listdir(folder))
                except OSError:
                    continue
                nxt += [f"{base}/{n}" if base else n for n in names if fnmatch.fnmatchcase(n, segment)]
            else:
                nxt.append(f"{base}/{segment}" if base else segment)
        found = nxt
    out = []
    for rel in found:
        full = os.path.join(root, rel)
        if not os.path.islink(full) and os.path.exists(full) and not _through_link(root, rel):
            out.append(rel)
    return out


def _through_link(root: str, rel: str) -> bool:
    """A parent of `rel` is a symlink: a save path never goes through one."""
    parts = rel.split("/")[:-1]
    cur = root
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return True
    return False


def matches(pattern: str, rel: str) -> bool:
    """`rel` is `pattern` or inside it, segment by segment (`*` matches one segment)."""
    p, r = pattern.split("/"), rel.split("/")
    return len(r) >= len(p) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(r, p))


def excluded(rel_in_root: str, exclude: list) -> bool:
    return any(matches(pattern, rel_in_root) for pattern in exclude)


def _has_file(root: str, rel: str, exclude: list) -> bool:
    full = os.path.join(root, rel)
    if os.path.isfile(full):
        return True
    for dirpath, dirnames, filenames in os.walk(full):
        inner = os.path.relpath(dirpath, full).replace(os.sep, "/")
        inner = "" if inner == "." else inner
        for name in filenames:
            sub = f"{inner}/{name}" if inner else name
            if not os.path.islink(os.path.join(dirpath, name)) and not excluded(sub, exclude):
                return True
    return False


class Strategy:
    name = ""
    default_paths: tuple = ()
    default_exclude: tuple = ()

    def resolve(self, spec: dict, source: str) -> SaveSpec:
        paths = spec.get("paths")
        if paths is None or paths == []:
            patterns = list(self.default_paths)
        elif isinstance(paths, list):
            patterns = [check_rel(p) for p in paths]
        else:
            raise StrategyError("saves.paths must be a list")
        if not patterns:
            raise StrategyError(f"strategy {self.name} needs saves.paths")
        extra = spec.get("exclude") or []
        if not isinstance(extra, list):
            raise StrategyError("saves.exclude must be a list")
        exclude = list(dict.fromkeys(list(self.default_exclude) + [check_rel(e) for e in extra]))
        return SaveSpec(self.name, patterns, exclude, source)

    def infer(self, root: str, manifest: dict):
        """A SaveSpec when this strategy fits the game folder with confidence (its folder
        exists and holds at least one file), else None."""
        spec = self.resolve({}, "inferred")
        for pattern in spec.patterns:
            for rel in expand(root, pattern):
                if _has_file(root, rel, spec.exclude):
                    return spec
        return None


class ProtonDefault(Strategy):
    name = "proton-default"
    default_paths = ("prefix/pfx/drive_c/users/steamuser",)
    # per-device caches and temporary files Wine/DXVK/drivers keep in the user profile
    default_exclude = (
        "Temp",
        "AppData/Local/Temp",
        "AppData/Local/Microsoft",
        "AppData/LocalLow/Microsoft",
        "AppData/Roaming/Microsoft",
        "AppData/Local/D3DSCache",
        "AppData/Local/NVIDIA",
        "AppData/Local/AMD",
    )


class CustomPath(Strategy):
    name = "custom-path"


class DuckStation(Strategy):
    name = "duckstation"
    default_paths = ("duckstation/memcards",)


class Pcsx2(Strategy):
    name = "pcsx2"
    default_paths = ("pcsx2/PCSX2/memcards",)


class Cemu(Strategy):
    name = "cemu"
    # only the games' saves: usr/save/system (account, PlayStats) and mlc01/sys stay out (#67)
    default_paths = ("cemu/portable/mlc01/usr/save/00050000",)


class Dolphin(Strategy):
    name = "dolphin"
    # GameCube memory cards and the Wii games' data; Wii system titles (00000001) stay out (#67)
    default_paths = ("dolphin/User/GC", "dolphin/User/Wii/title/00010000/*/data")


REGISTRY = {cls.name: cls() for cls in (ProtonDefault, CustomPath, DuckStation, Pcsx2, Cemu, Dolphin)}


def _get(data, key):
    if not isinstance(data, dict):
        return None
    if key in data:
        return data[key]
    lowered = key.lower()
    for k, v in data.items():
        if isinstance(k, str) and k.lower() == lowered:
            return v
    return None


def read_manifest(root: str) -> dict:
    """The game's `steam-shortcut.json` (root, then the legacy `insertplay/`), or {}."""
    for path in (os.path.join(root, MANIFEST_NAME), os.path.join(root, LEGACY_DIR, MANIFEST_NAME)):
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def from_block(block, source: str) -> SaveSpec:
    """A `saves` block (manifest or server `strategy.json`) → SaveSpec."""
    if not isinstance(block, dict):
        raise StrategyError("saves must be an object")
    name = _get(block, "strategy")
    strategy = REGISTRY.get(str(name).strip().lower()) if isinstance(name, str) else None
    if strategy is None:
        raise StrategyError(f"unknown save strategy {name!r}")
    return strategy.resolve({"paths": _get(block, "paths"), "exclude": _get(block, "exclude")}, source)


def manifest_spec(manifest: dict):
    block = _get(manifest, "saves")
    return None if block is None else from_block(block, "manifest")


def infer(root: str, manifest: dict) -> SaveSpec:
    """Last resort (#63): the declared emulator's strategy, or `proton-default` for a game
    with `prefix/` and no `game` block; only with confidence."""
    game = _get(manifest, "game")
    emulator = _get(game, "emulator") if isinstance(game, dict) else None
    candidate = None
    if isinstance(emulator, str) and emulator.strip().lower() in REGISTRY:
        candidate = REGISTRY[emulator.strip().lower()]
    elif not isinstance(game, dict) and os.path.isdir(os.path.join(root, "prefix")):
        candidate = REGISTRY["proton-default"]
    spec = candidate.infer(root, manifest) if candidate else None
    if spec is None:
        raise StrategyError("no save strategy: no saves block, nothing on the server and nothing to infer")
    return spec
