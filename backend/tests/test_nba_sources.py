"""Kalshi, official injury report, The Odds API, confirmed lineups and APM ratings. No network, no DB."""

from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from polypicker.etl.sources.kalshi import mid_price, parse_event
from polypicker.etl.sources.nba_injury_pdf import db_keys, pdf_keys, report_urls, status_weight, team_for
from polypicker.etl.sources.odds_api import parse as parse_odds
from polypicker.ml.apm import apm_ratings
from polypicker.ml.pipeline import load_confirmed_lineups


def test_kalshi_event_ticker():
    assert parse_event("KXNBAGAME-26NOV23SASSAC") == (date(2026, 11, 23), "SAS", "SAC")
    assert parse_event("KXNBAGAME-26XYZ23SASSAC") is None
    assert parse_event("KXNBAGAME-26FEB30SASSAC") is None  # impossible date


def test_kalshi_mid_price_prefers_tight_book():
    assert mid_price(0.60, 0.62, 0.55) == 0.61
    assert mid_price(0.40, 0.70, 0.55) == 0.55   # wide spread -> last trade
    assert mid_price(None, None, None) is None


def test_injury_report_name_keys_match_db_names():
    for pdf, db in [("Pippen Jr., Scotty", "Scotty Pippen Jr."), ("LivelyII,Dereck", "Dereck Lively II"),
                    ("Gilgeous-Alexander, Shai", "Shai Gilgeous-Alexander"), ("Krejci,Vit", "Vít Krejčí")]:
        assert set(pdf_keys(pdf)) & set(db_keys(db)), (pdf, db)


def test_injury_report_team_and_status():
    codes = {"LAC": (1, "Los Angeles Clippers"), "DAL": (2, "Dallas Mavericks")}
    assert team_for("LA Clippers", "LAC@DAL", codes) == 1
    assert team_for("DallasMavericks", "LAC@DAL", codes) == 2
    assert status_weight("Out") == 0.0 and status_weight("Available") == 1.0 and status_weight("Probable") == 0.9
    urls = report_urls(datetime(2026, 11, 23, 22, 0, tzinfo=timezone.utc))  # 5:00 PM ET
    assert urls[0].endswith("Injury-Report_2026-11-23_05_00PM.pdf") and urls[1].endswith("_05PM.pdf")


def test_odds_api_parse_matches_games():
    tip = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    games = [{"game_id": 7, "tipoff_utc": tip, "home": "orlando magic", "away": "detroit pistons"}]
    events = [{"commence_time": "2026-11-25T00:10:00Z", "home_team": "Orlando Magic", "away_team": "Detroit Pistons",
               "bookmakers": [{"key": "pinnacle", "title": "Pinnacle", "markets": [{"key": "h2h", "outcomes": [
                   {"name": "Orlando Magic", "price": -145}, {"name": "Detroit Pistons", "price": 133}]}]}]},
              {"commence_time": "2026-11-26T00:10:00Z", "home_team": "Utah Jazz", "away_team": "Miami Heat",
               "bookmakers": []}]
    rows, unmatched = parse_odds(events, games)
    assert unmatched == 1
    assert rows[0]["game_id"] == 7 and rows[0]["provider"] == "Pinnacle" and rows[0]["home_ml"] == -145


def test_confirmed_lineup_inactive_and_window():
    now = datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc)
    games = pl.DataFrame({"game_id": [1, 2], "status": ["scheduled", "scheduled"],
                          "tipoff_utc": [now + timedelta(minutes=30), now + timedelta(hours=5)]})
    box = {"game": {"homeTeam": {"teamId": 10, "players": [
        {"personId": 100, "name": "A", "status": "ACTIVE", "starter": "1"},
        {"personId": 101, "name": "B", "status": "INACTIVE", "starter": "0", "notPlayingReason": "INACTIVE_INJURY"}]},
        "awayTeam": {"teamId": 20, "players": []}}}
    calls = []
    entries, teams = load_confirmed_lineups(games, now, fetch=lambda gid: calls.append(gid) or box)
    assert calls == [1]                      # game 2 is too far away
    assert teams == {10}                     # an empty list isn't a confirmation
    assert entries[(10, 100)]["status"] == "Starting" and entries[(10, 100)]["weight"] == 1.0
    assert entries[(10, 101)]["weight"] == 0.0 and entries[(10, 101)]["detail"] == "Injury"


def test_apm_credits_the_player_who_moves_the_margin():
    rows, box = [], []
    d0 = date(2024, 1, 1)
    for i in range(500):
        d = d0 + timedelta(days=i // 5)
        star_plays = i % 2 == 0
        margin = 10 if star_plays else -2
        rows.append({"game_id": i, "game_date_et": d, "home_team_id": 1, "status": "final",
                     "home_score": 100 + margin, "away_score": 100})
        for pid, team, mins in [(1, 1, 36 if star_plays else 0), (2, 1, 36), (3, 2, 36), (4, 2, 36)]:
            if mins:
                box.append({"game_id": i, "player_id": pid, "team_id": team, "participation": "played", "minutes": float(mins)})
    games = pl.DataFrame(rows).vstack(pl.DataFrame([{"game_id": 999, "game_date_et": d0 + timedelta(days=200),
                                                     "home_team_id": 1, "status": "scheduled",
                                                     "home_score": None, "away_score": None}]), )
    r = apm_ratings(games, pl.DataFrame(box), alpha=1.0)
    last = r[d0 + timedelta(days=200)]
    assert last[1] > last[2] + 5            # the star carries the margin, not his teammate
    assert r[d0] == {}                       # nothing before the first games


def test_nfl_kalshi_event_uses_market_suffixes():
    from polypicker.nfl.etl.markets import parse_kalshi_event
    assert parse_kalshi_event("KXNFLGAME-25SEP07KCLAC", {"KC", "LAC"}) == (date(2025, 9, 7), "KC", "LAC")
    assert parse_kalshi_event("KXNFLGAME-25SEP07JACLAR", {"JAC", "LAR"}) == (date(2025, 9, 7), "JAX", "LA")
    assert parse_kalshi_event("KXNFLGAME-25SEP07KCLAC", {"KC"}) is None


def test_nfl_market_consensus_and_calibration():
    from polypicker.nfl.ml.game_model import calibrated, market_consensus
    g = pl.DataFrame({"game_id": [1, 2, 3], "market_spread_home": [3.0, None, None],
                      "market_home_ml": [-150, -200, None], "market_away_ml": [130, 170, None],
                      "kalshi_p_home": [0.6, None, None], "pinnacle_p_home": [None, None, None]})
    c = dict(market_consensus(g).iter_rows())
    assert 0.55 < c[1] < 0.65 and 0.62 < c[2] < 0.68 and c[3] is None
    assert calibrated(0.6, (0.0, 1.0)) == pytest.approx(0.6, abs=1e-6)
    assert calibrated(0.6, (0.0, 1.2)) > 0.6          # slope > 1 stretches away from 50%
