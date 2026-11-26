from datetime import datetime, timezone

from polypicker.soccer.etl.crosscheck import norm
from polypicker.soccer.etl.fotmob import match_fixtures, parse_stats


def test_parse_stats_takes_first_full_value_per_key():
    d = {"content": {"stats": {"Periods": {"All": {"stats": [
        {"key": "top_stats", "stats": [{"key": "expected_goals", "stats": ["2.57", "0.23"]},
                                       {"key": "big_chance", "stats": [3, 1]}]},
        {"key": "expected_goals", "stats": [{"key": "expected_goals", "stats": [None, None]},
                                            {"key": "expected_goals_on_target", "stats": ["2.38", "0.56"]}]}]}}}}}
    assert parse_stats(d) == {"home_xg": 2.57, "away_xg": 0.23, "home_big_chances": 3, "away_big_chances": 1,
                              "home_xgot": 2.38, "away_xgot": 0.56}
    assert parse_stats({"content": {}}) is None


def test_match_fixtures_detects_reversed_orientation():
    ko = datetime(2026, 8, 21, 19, tzinfo=timezone.utc)
    games = [{"game_id": 7, "kickoff_utc": ko, "home_names": {norm("Coventry City")}, "away_names": {norm("Arsenal")}}]
    fx = [{"id": "1", "home": {"name": "Arsenal"}, "away": {"name": "Coventry City"},
           "status": {"utcTime": "2026-08-21T19:00:00Z", "finished": True}},
          {"id": "2", "home": {"name": "Arsenal"}, "away": {"name": "Chelsea"},
           "status": {"utcTime": "2026-08-30T19:00:00Z", "finished": False}}]
    (f, g, swapped), = match_fixtures(fx, games)
    assert f["id"] == "1" and g["game_id"] == 7 and swapped
