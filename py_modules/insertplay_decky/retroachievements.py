"""RetroAchievements (issue #24): the plugin's own login, the web API client, the
mapping to Steam's achievement format and the per-game cache.

The plugin only *shows* progress: unlocking stays with the emulator (rcheevos). It never
reads or writes the emulators' credentials. The API key never goes to a log, to the
event log or to an exception message.
"""
import calendar
import json
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://retroachievements.org/API/"
BADGE_URL = "https://media.retroachievements.org/Badge/{}.png"
TIMEOUT_SECONDS = 15
USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
# Web API keys are 32 alphanumeric characters; the range only rejects pasted garbage early.
KEY_RE = re.compile(r"^[A-Za-z0-9]{16,64}$")


class RAError(Exception):
    """`code` is what the panel shows: "invalid", "refused" or "network". Only "refused"
    is a warning outside the login: "network" (offline, RA down) keeps the cache silently."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _write_private(path: str, data) -> None:
    """Atomic write readable only by the plugin's user (0600)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Credentials:
    """`ra_credentials.json` in the settings folder, outside `Settings`: that one only
    takes the keys of `DEFAULT_SETTINGS` and is returned whole to the frontend."""

    def __init__(self, path: str):
        self.path = path

    def load(self):
        data = _read(self.path)
        if not isinstance(data, dict):
            return None
        user, key = data.get("user"), data.get("key")
        if not isinstance(user, str) or not isinstance(key, str) or not USER_RE.match(user) or not KEY_RE.match(key):
            return None
        return {"user": user, "key": key}

    def save(self, user: str, key: str) -> None:
        """A new login also clears the "refused" mark (issue #34)."""
        _write_private(self.path, {"user": user, "key": key})

    def refused(self) -> bool:
        """RA refused the saved key (HTTP 401/403): the API stays paused until a new login."""
        data = _read(self.path)
        return self.load() is not None and data.get("refused") is True

    def mark_refused(self, creds: dict) -> bool:
        """Saved next to the key it applies to, so it survives a Decky restart and a new
        login or logout clears it. True only on the change: the caller warns once. False
        too if the login changed since `creds` was read."""
        if self.load() != creds or self.refused():
            return False
        _write_private(self.path, {**creds, "refused": True})
        return True

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def check_credentials(user: str, key: str) -> tuple:
    """Trims and validates the format. Raises RAError("invalid") without saying which part
    of the key was wrong."""
    user = (user or "").strip()
    key = (key or "").strip()
    if not USER_RE.match(user) or not KEY_RE.match(key):
        raise RAError("invalid", "user or key has the wrong format")
    return user, key


def _ssl_context() -> ssl.SSLContext:
    """Decky's PluginLoader is a frozen Python: the system CA bundle may not be where its
    `ssl` looks. certifi comes with Decky (aiohttp uses it); the default is the fallback."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def call(endpoint: str, params: dict, key: str, user_agent: str, opener=None) -> object:
    """GET on the web API. Errors become RAError with no URL (the URL has the key)."""
    query = urllib.parse.urlencode({**params, "y": key})
    request = urllib.request.Request(f"{API_URL}{endpoint}?{query}", headers={"User-Agent": user_agent})
    try:
        if opener:
            body = opener(request)
        else:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=_ssl_context()) as response:
                body = response.read()
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RAError("refused", f"HTTP {exc.code}") from None
        raise RAError("network", f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RAError("network", f"{type(exc).__name__}: {reason}") from None
    except ValueError:
        raise RAError("network", "response is not JSON") from None


def verify_login(user: str, key: str, user_agent: str, opener=None) -> str:
    """Light call that only works with a valid key. Returns the user name as RA writes it."""
    profile = call("API_GetUserProfile.php", {"u": user}, key, user_agent, opener)
    name = profile.get("User") if isinstance(profile, dict) else None
    if not isinstance(name, str) or name.lower() != user.lower():
        raise RAError("refused", "profile does not match the user")
    return name


def fetch_game(user: str, key: str, game_id: int, user_agent: str, opener=None) -> dict:
    raw = call("API_GetGameInfoAndUserProgress.php", {"u": user, "g": game_id, "a": 1}, key, user_agent, opener)
    if not isinstance(raw, dict) or "Achievements" not in raw:
        raise RAError("network", f"unexpected response for game {game_id}")
    return to_steam(game_id, raw)


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _timestamp(value) -> int:
    """RA dates are UTC, `YYYY-MM-DD HH:MM:SS`."""
    if not isinstance(value, str) or not value:
        return 0
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return 0


def to_steam(game_id: int, raw: dict) -> dict:
    """RA response → Steam's achievement objects (`Ach`, measured on the Deck: same fields
    as a real Steam game's `details.achievements`).

    - `strID` is the RA ID, never the title: two achievements can share a title.
    - Unlocked in hardcore or softcore counts; hardcore's date wins.
    - Locked badges use RA's `_lock` image, as on the site."""
    items = raw.get("Achievements") or {}
    items = list(items.values()) if isinstance(items, dict) else list(items)
    items = [a for a in items if isinstance(a, dict) and _int(a.get("ID")) > 0]
    items.sort(key=lambda a: (_int(a.get("DisplayOrder")), _int(a.get("ID"))))
    players = _int(raw.get("NumDistinctPlayers"))
    achievements = []
    for a in items:
        unlocked = _timestamp(a.get("DateEarnedHardcore")) or _timestamp(a.get("DateEarned"))
        badge = str(a.get("BadgeName") or "00000")
        achievements.append(
            {
                "strID": str(_int(a.get("ID"))),
                "strName": str(a.get("Title") or ""),
                "strDescription": str(a.get("Description") or ""),
                "bAchieved": unlocked > 0,
                "rtUnlocked": unlocked,
                "strImage": BADGE_URL.format(badge if unlocked else f"{badge}_lock"),
                "bHidden": False,
                "flMinProgress": 0,
                "flCurrentProgress": 0,
                "flMaxProgress": 0,
                "flAchieved": round(min(_int(a.get("NumAwarded")) / players * 100, 100.0), 1) if players else 0.0,
            }
        )
    return {
        "gameId": game_id,
        "title": str(raw.get("Title") or ""),
        "total": len(achievements),
        "unlocked": sum(1 for a in achievements if a["bAchieved"]),
        "achievements": achievements,
    }


class Cache:
    """Last fetch per GameID (`ra-cache/<gameId>.json`), with the user it belongs to:
    another account's progress is never shown after a login change."""

    def __init__(self, folder: str):
        self.folder = folder

    def _path(self, game_id: int) -> str:
        return os.path.join(self.folder, f"{int(game_id)}.json")

    def get(self, game_id: int, user: str):
        data = _read(self._path(game_id))
        if not isinstance(data, dict) or str(data.get("user", "")).lower() != user.lower():
            return None
        game = data.get("game")
        if not isinstance(game, dict):
            return None
        return {**game, "fetchedAt": data.get("fetchedAt", 0)}

    def put(self, game_id: int, user: str, game: dict, now: float) -> dict:
        _write_private(self._path(game_id), {"user": user, "fetchedAt": now, "game": game})
        return {**game, "fetchedAt": now}
