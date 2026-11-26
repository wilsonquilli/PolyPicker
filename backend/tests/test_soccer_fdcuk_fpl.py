from datetime import date, datetime, timedelta, timezone

from polypicker.soccer.etl.fdcuk import build_rows, season_code
from polypicker.soccer.etl.fpl import map_fixtures


def _games(n=4):
    # club ids 1..4, every pair at home once over consecutive Saturdays
    out, gid, d = [], 100, date(2024, 8, 17)
    for h in range(1, n + 1):
        for a in range(1, n + 1):
            if h != a:
                out.append({"game_id": gid, "game_date": d, "home_team_id": h, "away_team_id": a})
                gid += 1
                d += timedelta(days=7)
    return out


NAMES = {1: "Man United", 2: "Nott'm Forest", 3: "Wolves", 4: "Spurs"}


def test_fdcuk_maps_clubs_by_fixture_dates_and_removes_margin():
    games = _games()
    rows = [{"Date": g["game_date"].strftime("%d/%m/%Y"), "HomeTeam": NAMES[g["home_team_id"]], "AwayTeam": NAMES[g["away_team_id"]],
             "HS": "12", "AS": "9", "HC": "5", "AC": "3", "PSCH": "2.0", "PSCD": "3.5", "PSCA": "4.0", "AvgH": "2.1", "AvgD": "3.4",
             "AvgA": "3.8", "PC>2.5": "1.9", "PC<2.5": "1.95"} for g in games]
    out, unmatched = build_rows(rows, games, "eng.1")
    assert unmatched == 0 and sorted(r["game_id"] for r in out) == [g["game_id"] for g in games]
    r = out[0]
    assert r["odds_source"] == "pinnacle_close" and abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1) < 1e-9
    assert r["home_corners"] == 5 and 0.45 < r["p_over25"] < 0.55
    assert season_code(2010) == "1011" and season_code(2099) == "9900"


def test_fpl_maps_club_ids_and_fixtures():
    games = _games()
    fpl_id = {1: 11, 2: 12, 3: 13, 4: 14}
    fixtures = [{"id": i, "team_h": fpl_id[g["home_team_id"]], "team_a": fpl_id[g["away_team_id"]],
                 "kickoff_time": datetime.combine(g["game_date"], datetime.min.time(), tzinfo=timezone.utc).replace(hour=15).isoformat()}
                for i, g in enumerate(games, 1)]
    teams, fx = map_fixtures(fixtures, games)
    assert teams == {11: 1, 12: 2, 13: 3, 14: 4}
    assert fx == {i: g["game_id"] for i, g in enumerate(games, 1)}
