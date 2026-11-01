import io
import json
import os
import stat
import sys
import urllib.error
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from insertplay_decky import retroachievements as ra  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ra_game_5613.json")
KEY = "AbCdEf0123456789AbCdEf0123456789"


def raw_game():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_to_steam_maps_every_field_of_a_steam_achievement():
    game = ra.to_steam(5613, raw_game())
    first = game["achievements"][0]
    assert set(first) == {
        "strID", "strName", "strDescription", "bAchieved", "rtUnlocked", "strImage", "bHidden",
        "flMinProgress", "flCurrentProgress", "flMaxProgress", "flAchieved",
    }
    assert game["gameId"] == 5613
    assert game["total"] == len(game["achievements"])
    assert game["unlocked"] == sum(a["bAchieved"] for a in game["achievements"])


def test_to_steam_with_the_real_nfsu2_response():
    """Values checked against the Deck's screen on 2026-11-01 (issue #24: 87.6% for the first one)."""
    game = ra.to_steam(5613, raw_game())
    assert (game["title"], game["total"], game["unlocked"]) == ("Need for Speed: Underground 2", 6, 3)
    first = game["achievements"][0]
    assert first == {
        "strID": "261034",
        "strName": "Welcome to Bayview",
        "strDescription": "Complete the Tutorial",
        "bAchieved": True,
        "rtUnlocked": 1780374631,  # 2026-06-02 04:30:31 UTC
        "strImage": "https://media.retroachievements.org/Badge/609317.png",
        "bHidden": False,
        "flMinProgress": 0,
        "flCurrentProgress": 0,
        "flMaxProgress": 0,
        "flAchieved": 87.6,
    }
    locked = game["achievements"][3]
    assert (locked["strID"], locked["bAchieved"], locked["rtUnlocked"]) == ("261036", False, 0)
    assert locked["strImage"] == "https://media.retroachievements.org/Badge/609322_lock.png"


def test_to_steam_orders_by_display_order_then_id_and_uses_the_ra_id():
    raw = raw_game()
    ids = [a["strID"] for a in ra.to_steam(5613, raw)["achievements"]]
    expected = sorted(raw["Achievements"].values(), key=lambda a: (a["DisplayOrder"], a["ID"]))
    assert ids == [str(a["ID"]) for a in expected]
    # same title twice stays as two achievements: the id is the RA ID, not the title
    raw = {"Achievements": {"1": {"ID": 1, "Title": "Same"}, "2": {"ID": 2, "Title": "Same"}}}
    assert [a["strID"] for a in ra.to_steam(1, raw)["achievements"]] == ["1", "2"]


def test_to_steam_unlock_time_is_utc_and_hardcore_wins():
    raw = {"NumDistinctPlayers": 10, "Achievements": {
        "1": {"ID": 1, "BadgeName": "9", "DateEarned": "2026-01-02 00:00:00", "DateEarnedHardcore": "2026-01-01 00:00:00"},
        "2": {"ID": 2, "BadgeName": "9", "DateEarned": "2026-01-02 00:00:00"},
        "3": {"ID": 3, "BadgeName": "9", "DateEarned": "", "DateEarnedHardcore": None},
    }}
    a, b, c = ra.to_steam(1, raw)["achievements"]
    assert (a["bAchieved"], a["rtUnlocked"]) == (True, 1767225600)  # 2026-01-01T00:00:00Z
    assert (b["bAchieved"], b["rtUnlocked"]) == (True, 1767312000)
    assert (c["bAchieved"], c["rtUnlocked"]) == (False, 0)


def test_to_steam_badge_is_locked_until_unlocked():
    by_id = {a["strID"]: a for a in ra.to_steam(5613, raw_game())["achievements"]}
    unlocked = next(a for a in by_id.values() if a["bAchieved"])
    locked = next(a for a in by_id.values() if not a["bAchieved"])
    assert unlocked["strImage"].startswith("https://media.retroachievements.org/Badge/")
    assert not unlocked["strImage"].endswith("_lock.png")
    assert locked["strImage"].endswith("_lock.png")


def test_to_steam_global_percentage():
    raw = {"NumDistinctPlayers": 1000, "Achievements": {"1": {"ID": 1, "NumAwarded": 876}}}
    assert ra.to_steam(1, raw)["achievements"][0]["flAchieved"] == 87.6
    raw["NumDistinctPlayers"] = 0
    assert ra.to_steam(1, raw)["achievements"][0]["flAchieved"] == 0.0


def test_to_steam_tolerates_empty_and_odd_input():
    assert ra.to_steam(1, {"Achievements": []}) == {"gameId": 1, "title": "", "total": 0, "unlocked": 0, "achievements": []}
    odd = {"Achievements": {"x": "nope", "y": {"ID": "abc"}, "z": {"ID": 4, "NumAwarded": "?"}}}
    game = ra.to_steam(1, odd)
    assert [a["strID"] for a in game["achievements"]] == ["4"]


def test_credentials_are_private_and_round_trip(tmp_path):
    creds = ra.Credentials(str(tmp_path / "settings" / "ra_credentials.json"))
    assert creds.load() is None
    creds.save("Someone", KEY)
    assert creds.load() == {"user": "Someone", "key": KEY}
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(creds.path).st_mode) == 0o600
    creds.clear()
    creds.clear()  # twice is fine
    assert creds.load() is None


def test_credentials_refused_mark_until_a_new_login(tmp_path):
    creds = ra.Credentials(str(tmp_path / "ra_credentials.json"))
    assert not creds.refused() and not creds.mark_refused({"user": "Someone", "key": KEY})  # no login
    creds.save("Someone", KEY)
    login = creds.load()
    assert creds.mark_refused(login) and creds.refused()
    assert not creds.mark_refused(login)  # only the change counts
    assert creds.load() == login  # the key is still there, unchanged
    creds.save("Someone", KEY)  # a new login clears it
    assert not creds.refused()
    assert not creds.mark_refused({"user": "Other", "key": KEY})  # login changed meanwhile
    assert not creds.refused()
    creds.mark_refused(login)
    creds.clear()  # logout clears it too
    assert not creds.refused()


def test_credentials_with_a_broken_file_are_absent(tmp_path):
    path = tmp_path / "ra_credentials.json"
    path.write_text('{"user": "x", "key": "short"}', encoding="utf-8")
    assert ra.Credentials(str(path)).load() is None
    path.write_text("not json", encoding="utf-8")
    assert ra.Credentials(str(path)).load() is None


@pytest.mark.parametrize("user,key", [("", KEY), ("a b", KEY), ("user", ""), ("user", "k" * 10), ("user", KEY + "!")])
def test_check_credentials_refuses_bad_format(user, key):
    with pytest.raises(ra.RAError) as err:
        ra.check_credentials(user, key)
    assert err.value.code == "invalid"
    assert KEY not in str(err.value)


def test_check_credentials_trims():
    assert ra.check_credentials("  Someone ", f" {KEY}\n") == ("Someone", KEY)


def opener_returning(payload, seen=None):
    def opener(request):
        if seen is not None:
            seen.append(request)
        return json.dumps(payload).encode()

    return opener


def test_call_sends_user_agent_and_key_only_in_the_query():
    seen = []
    ra.call("API_GetUserProfile.php", {"u": "Someone"}, KEY, "InsertPlay/1.0", opener_returning({"User": "Someone"}, seen))
    request = seen[0]
    assert request.get_header("User-agent") == "InsertPlay/1.0"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    assert query == {"u": ["Someone"], "y": [KEY]}


@pytest.mark.parametrize(
    "error,code",
    [
        (urllib.error.HTTPError("https://x/?y=" + KEY, 401, "Unauthorized", {}, io.BytesIO(b"")), "refused"),
        (urllib.error.HTTPError("https://x/?y=" + KEY, 500, "Boom", {}, io.BytesIO(b"")), "network"),
        (urllib.error.URLError("Name or service not known"), "network"),
        (TimeoutError("timed out"), "network"),
    ],
)
def test_call_errors_never_carry_the_key(error, code):
    def opener(request):
        raise error

    with pytest.raises(ra.RAError) as err:
        ra.call("API_GetUserProfile.php", {"u": "Someone"}, KEY, "ua", opener)
    assert err.value.code == code
    assert KEY not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_call_with_non_json_is_a_network_error():
    with pytest.raises(ra.RAError) as err:
        ra.call("x", {}, KEY, "ua", lambda request: b"<html>")
    assert err.value.code == "network"


def test_verify_login_matches_the_user_ignoring_case():
    assert ra.verify_login("someone", KEY, "ua", opener_returning({"User": "Someone"})) == "Someone"
    with pytest.raises(ra.RAError) as err:
        ra.verify_login("someone", KEY, "ua", opener_returning({"User": "Other"}))
    assert err.value.code == "refused"
    with pytest.raises(ra.RAError):
        ra.verify_login("someone", KEY, "ua", opener_returning([]))


def test_fetch_game_maps_the_response():
    seen = []
    game = ra.fetch_game("Someone", KEY, 5613, "ua", opener_returning(raw_game(), seen))
    assert game["gameId"] == 5613 and game["total"] > 0
    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0].full_url).query)
    assert query["g"] == ["5613"] and query["a"] == ["1"]


def test_fetch_game_with_an_unexpected_response_fails():
    with pytest.raises(ra.RAError):
        ra.fetch_game("Someone", KEY, 5613, "ua", opener_returning({"message": "nope"}))


def test_cache_belongs_to_one_user(tmp_path):
    cache = ra.Cache(str(tmp_path / "ra-cache"))
    assert cache.get(5613, "Someone") is None
    game = ra.to_steam(5613, raw_game())
    stored = cache.put(5613, "Someone", game, 1000.0)
    assert stored["fetchedAt"] == 1000.0
    assert cache.get(5613, "someone") == stored
    assert cache.get(5613, "Other") is None
    assert cache.get(1, "Someone") is None
