from datetime import datetime, timedelta, timezone

from polypicker.soccer.etl.crosscheck import norm
from polypicker.soccer.etl.odds_api import parse

NOW = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
KO = NOW + timedelta(hours=3)


def _game(gid, home, away, ko=KO):
    return {"game_id": gid, "kickoff_utc": ko, "home_names": {norm(x) for x in home}, "away_names": {norm(x) for x in away}}


def _event(home, away, ko=KO, prices=(2.1, 3.4, 3.6)):
    return {"home_team": home, "away_team": away, "commence_time": ko.isoformat().replace("+00:00", "Z"),
            "bookmakers": [{"key": "pinnacle", "title": "Pinnacle", "markets": [{"key": "h2h", "last_update": NOW.isoformat(),
                            "outcomes": [{"name": home, "price": prices[0]}, {"name": "Draw", "price": prices[1]},
                                         {"name": away, "price": prices[2]}]}]}]}


def test_matches_odds_api_spellings_and_removes_margin():
    games = [_game(1, ("Brighton & Hove Albion", "Brighton"), ("Tottenham Hotspur", "Spurs")),
             _game(2, ("AFC Bournemouth", "Bournemouth"), ("Nottingham Forest", "Nottm Forest"))]
    rows, unmatched = parse([_event("Brighton and Hove Albion", "Tottenham Hotspur"), _event("Bournemouth", "Nottingham Forest")],
                            games, "eng.1", NOW)
    assert unmatched == 0 and [r["game_id"] for r in rows] == [1, 2]
    r = rows[0]
    assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1) < 1e-9 and r["p_home"] > r["p_away"]
    assert r["minutes_to_kickoff"] == 180 and r["provider"] == "Pinnacle"


def test_skips_unknown_clubs_and_matches_in_play():
    games = [_game(1, ("Arsenal",), ("Chelsea",), ko=NOW - timedelta(minutes=30))]
    rows, unmatched = parse([_event("Arsenal", "Chelsea", ko=NOW - timedelta(minutes=30)), _event("Real Madrid", "Barcelona")],
                            games, "eng.1", NOW)
    assert rows == [] and unmatched == 1
