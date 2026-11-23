"""ESPN odds parsing: American odds, margin removal, book choice, spread sign. No network."""

from datetime import date

from polypicker.etl.issues import IssueLog
from polypicker.etl.sources.espn_odds import american_to_decimal, collect, no_vig_home, parse_odds, parse_props, pick_book


def _book(name, home_ml, away_ml, *, spread=5.5, home_fav=False, total=219.5):
    side = lambda ml, fav, open_ml: {"moneyLine": ml, "favorite": fav, "open": {"moneyLine": {"american": open_ml}}}  # noqa: E731
    return {"provider": {"name": name}, "spread": spread, "overUnder": total,
            "homeTeamOdds": side(home_ml, home_fav, "+220" if not home_fav else "-270"),
            "awayTeamOdds": side(away_ml, not home_fav, "-270" if not home_fav else "+220")}


def test_american_to_decimal():
    assert american_to_decimal(-200) == 1.5
    assert american_to_decimal(150) == 2.5
    assert american_to_decimal(None) is None and american_to_decimal(0) is None


def test_no_vig_probability_removes_margin():
    p = no_vig_home(175, -210)                 # home underdog
    raw_h, raw_a = 1 / 2.75, 1 / (1 + 100 / 210)
    assert abs(p - raw_h / (raw_h + raw_a)) < 1e-12 and 0.33 < p < 0.36
    assert abs(no_vig_home(-110, -110) - 0.5) < 1e-12


def test_pick_book_skips_live_and_prefers_draftkings():
    items = [_book("ESPN Bet - Live Odds", 1000, -3000), _book("MGM", 170, -200), _book("DraftKings", 175, -210)]
    assert pick_book(items)["provider"]["name"] == "DraftKings"
    assert pick_book([_book("Some Book", 175, -210)])["provider"]["name"] == "Some Book"
    assert pick_book([_book("X - Live Odds", 100, -120)]) is None


def test_parse_odds_spread_is_home_handicap():
    dog = parse_odds(_book("DraftKings", 175, -210, spread=5.5, home_fav=False))
    fav = parse_odds(_book("DraftKings", -240, 200, spread=6.5, home_fav=True))
    assert dog["spread_home"] == 5.5 and fav["spread_home"] == -6.5
    assert dog["open_home_ml"] == 220 and dog["open_away_ml"] == -270
    assert dog["p_home"] < 0.5 < fav["p_home"] and dog["total"] == 219.5


def test_collect_matches_events_to_games_by_date_and_teams():
    day = date(2025, 1, 15)
    board = {"events": [
        {"id": "1", "competitions": [{"status": {"type": {"state": "post"}}, "competitors": [
            {"homeAway": "home", "team": {"displayName": "LA Clippers"}},
            {"homeAway": "away", "team": {"displayName": "New York Knicks"}}]}]},
        {"id": "2", "competitions": [{"status": {"type": {"state": "post"}}, "competitors": [
            {"homeAway": "home", "team": {"displayName": "Exhibition Team"}},
            {"homeAway": "away", "team": {"displayName": "Boston Celtics"}}]}]}]}

    def get(url, params=None):
        return board if "scoreboard" in url else {"items": [_book("DraftKings", 175, -210)]}

    games = {(day, "los angeles clippers", "new york knicks"): 42}
    rows = collect(get, [day], games, IssueLog(), workers=2, with_props=False)
    assert len(rows) == 1 and rows[0]["line"]["game_id"] == 42 and rows[0]["line"]["is_closing"] is True
    assert [b["provider"] for b in rows[0]["books"]] == ["DraftKings"] and rows[0]["props"] == []


def test_parse_props_over_first_and_open_close():
    def item(aid, typ, dec, dec_open, line, line_open):
        return {"athlete": {"$ref": f"http://x/athletes/{aid}?lang=en"}, "type": {"name": typ},
                "odds": {"decimal": {"value": dec, "open": dec_open}},
                "current": {"target": {"value": line}}, "open": {"target": {"value": line_open}}}
    items = [item(7, "Total Points", "1.84", "2.01", 11.5, 12.5), item(7, "Total Points", "1.92", "1.77", 11.5, 12.5),
             item(7, "First Basket", "9.0", "9.0", 0, 0), item(8, "Total Rebounds", "1.9", "1.9", 5.5, 5.5)]
    out = parse_props(items)
    assert len(out) == 1  # unpaired rebounds and unmodelled types dropped
    p = out[0]
    assert p["espn_athlete_id"] == "7" and p["stat"] == "points" and p["over"] == 1.84 and p["under"] == 1.92
    assert p["line_open"] == 12.5 and p["line"] == 11.5 and p["over_open"] == 2.01
