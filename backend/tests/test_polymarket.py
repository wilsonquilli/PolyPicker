"""Polymarket market matching and pregame price selection. No network."""

from datetime import datetime, timedelta, timezone

from polypicker.etl.sources.polymarket import _ts, match, pregame_price

TIP = datetime(2026, 11, 22, 0, 30, tzinfo=timezone.utc)
GAMES = [{"game_id": 1, "tipoff_utc": TIP, "home_name": "knicks", "away_name": "trail blazers"},
         {"game_id": 2, "tipoff_utc": TIP + timedelta(days=3), "home_name": "trail blazers", "away_name": "knicks"}]


def _mk(outcomes, start="2026-11-22 00:30:00+00"):
    return {"outcomes": outcomes, "gameStartTime": start}


def test_timestamp_formats():
    assert _ts("2026-11-22 00:30:00+00") == TIP
    assert _ts("2026-11-22T00:30:00Z") == TIP
    assert _ts(None) is None and _ts("garbage") is None


def test_match_by_names_and_time_and_orients_home_away():
    g, ih, ia = match(_mk('["Trail Blazers", "Knicks"]'), GAMES)
    assert g["game_id"] == 1 and (ih, ia) == (1, 0)
    # same teams three days later -> the return fixture, not game 1
    g2, ih2, _ = match(_mk('["Knicks", "Trail Blazers"]', "2026-11-25 00:30:00+00"), GAMES)
    assert g2["game_id"] == 2 and ih2 == 1
    assert match(_mk('["Knicks", "Lakers"]'), GAMES) is None
    assert match(_mk('["Knicks"]'), GAMES) is None


def test_pregame_price_takes_last_point_before_tipoff():
    t = int(TIP.timestamp())
    hist = {"history": [{"t": t - 3600, "p": 0.40}, {"t": t - 300, "p": 0.44}, {"t": t + 600, "p": 0.90}]}
    p, at = pregame_price(lambda url, params=None: hist, "tok", TIP)
    assert p == 0.44 and at == TIP - timedelta(minutes=5)
    assert pregame_price(lambda url, params=None: {"history": []}, "tok", TIP) == (None, None)
