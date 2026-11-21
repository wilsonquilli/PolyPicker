"""Point-in-time guarantees (NFR-09.1, spec §9.2: never use post-tipoff data)."""

from datetime import date, datetime, timedelta, timezone

import polars as pl

from polypicker.ml.team_features import build_team_features

A, B, C, D = 1, 2, 3, 4


def _games(results: dict[int, tuple[int, int]]):
    rows, box = [], []
    start = date(2025, 10, 21)
    sched = [(1, A, B, 0), (2, C, D, 0), (3, A, C, 2), (4, B, D, 2), (5, A, D, 4), (6, B, C, 4)]
    for gid, h, a, day in sched:
        d = start + timedelta(days=day)
        hs, as_ = results.get(gid, (110, 100))
        final = gid in results
        rows.append({"game_id": gid, "season": 2025, "season_type": "regular",
                     "tipoff_utc": datetime(d.year, d.month, d.day, 23, tzinfo=timezone.utc), "game_date_et": d,
                     "home_team_id": h, "away_team_id": a, "status": "final" if final else "scheduled",
                     "home_score": hs if final else None, "away_score": as_ if final else None})
        if final:
            for t, pts in ((h, hs), (a, as_)):
                box.append({"game_id": gid, "team_id": t, "points": pts, "fga": 88, "oreb": 10, "tov": 13, "fta": 22})
    return pl.DataFrame(rows), pl.DataFrame(box)


def test_features_ignore_same_day_and_future_results():
    known = {1: (115, 100), 2: (101, 99)}
    g1, b1 = _games(known)
    # Same schedule, but games 3-6 (same day as / after the target) have wildly different results.
    g2, b2 = _games({**known, 3: (150, 60), 4: (60, 150), 5: (140, 70), 6: (70, 140)})
    f1 = build_team_features(g1, b1).filter(pl.col("game_id") == 3).sort("team_id")
    f2 = build_team_features(g2, b2).filter(pl.col("game_id") == 3).sort("team_id")
    cols = ["ortg", "drtg", "pace", "ortg_l10", "drtg_l10", "elo"]
    assert f1.select(cols).equals(f2.select(cols))


def test_rest_days_come_from_schedule():
    g, b = _games({1: (110, 100)})
    f = build_team_features(g, b)
    a_game3 = f.filter((pl.col("game_id") == 3) & (pl.col("team_id") == A)).row(0, named=True)
    assert a_game3["rest_days"] == 2
