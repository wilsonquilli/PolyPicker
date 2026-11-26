"""football-data.co.uk -> soccer_fdcuk_matches (+ fills soccer_odds where ESPN has no line).

One CSV per league-season (``/mmz4281/{yy}{yy}/{div}.csv``): results, shots, corners, fouls, cards, referee,
xG (from 2026-27), and 1X2 / over-under prices -- Pinnacle closing from 2012-13, market-average closing from
2019-20, market averages back to the 2000s. ESPN's odds only start in 2019, so this roughly doubles the
odds-era history the market model and every backtest can use.

Clubs are mapped by fixtures, not spellings: a football-data.co.uk name maps to the ESPN club that played at home
on the same dates (+-1 day) in that competition most often.
"""

from __future__ import annotations

import io
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import polars as pl
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer import models as sm
from polypicker.soccer.leagues import League

URL = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"
DIVS = {"epl": "E0", "laliga": "SP1", "bundesliga": "D1", "seriea": "I1", "ligue1": "F1"}
STATS = {"HS": "home_shots", "AS": "away_shots", "HST": "home_sot", "AST": "away_sot", "HC": "home_corners",
         "AC": "away_corners", "HF": "home_fouls", "AF": "away_fouls", "HY": "home_yellow", "AY": "away_yellow",
         "HR": "home_red", "AR": "away_red"}
# (label, home, draw, away) price sets, best first
CLOSE_SETS = [("pinnacle_close", "PSCH", "PSCD", "PSCA"), ("avg_close", "AvgCH", "AvgCD", "AvgCA"),
              ("pinnacle", "PSH", "PSD", "PSA"), ("avg", "AvgH", "AvgD", "AvgA"), ("avg", "BbAvH", "BbAvD", "BbAvA")]
OPEN_SETS = [("AvgH", "AvgD", "AvgA"), ("BbAvH", "BbAvD", "BbAvA")]
OVER_SETS = [("PC>2.5", "PC<2.5"), ("AvgC>2.5", "AvgC<2.5"), ("P>2.5", "P<2.5"), ("Avg>2.5", "Avg<2.5"),
             ("BbAv>2.5", "BbAv<2.5")]

log = logging.getLogger(__name__)


def season_code(season: int) -> str:
    return f"{season % 100:02d}{(season + 1) % 100:02d}"


def http_get(url: str) -> bytes | None:
    import requests
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def read_csv(raw: bytes) -> list[dict]:
    df = pl.read_csv(io.BytesIO(raw), infer_schema_length=0, truncate_ragged_lines=True, encoding="utf8-lossy",
                     ignore_errors=True)
    df = df.rename({c: c.lstrip("﻿") for c in df.columns})
    return [r for r in df.iter_rows(named=True) if r.get("HomeTeam") and r.get("Date")]


def _date(v: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(v.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _f(v) -> float | None:
    try:
        x = float(v)
        return x if x > 1.0 else None
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def implied(r: dict, cols: tuple[str, ...]) -> list[float] | None:
    prices = [_f(r.get(c)) for c in cols]
    if any(p is None for p in prices):
        return None
    inv = [1 / p for p in prices]
    t = sum(inv)
    return [x / t for x in inv]


def map_teams(rows: list[dict], games: list[dict]) -> dict[str, int]:
    """football-data.co.uk club name -> ESPN team id, by who played at home / away on the same dates."""
    by_day: dict = defaultdict(list)
    for g in games:
        by_day[g["game_date"]].append(g)
    votes: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        d = _date(r["Date"])
        if d is None:
            continue
        for day in (d, d - timedelta(days=1), d + timedelta(days=1)):
            for g in by_day.get(day, []):
                votes[r["HomeTeam"]][g["home_team_id"]] += 1
                votes[r["AwayTeam"]][g["away_team_id"]] += 1
    out, used = {}, set()
    # most confident first, one ESPN club per name
    for name, c in sorted(votes.items(), key=lambda kv: -kv[1].most_common(1)[0][1]):
        for tid, n in c.most_common():
            if tid not in used and n >= 3:
                out[name] = tid
                used.add(tid)
                break
    return out


def build_rows(rows: list[dict], games: list[dict], competition: str) -> tuple[list[dict], int]:
    teams = map_teams(rows, games)
    idx = {(g["home_team_id"], g["away_team_id"], g["game_date"]): g["game_id"] for g in games}
    out, unmatched = [], 0
    for r in rows:
        d, h, a = _date(r["Date"]), teams.get(r["HomeTeam"]), teams.get(r["AwayTeam"])
        gid = next((idx.get((h, a, day)) for day in (d, d - timedelta(days=1), d + timedelta(days=1))
                    if d and idx.get((h, a, day))), None) if h and a else None
        if gid is None:
            unmatched += 1
            continue
        src, p = next(((lab, p) for lab, *cols in CLOSE_SETS if (p := implied(r, tuple(cols)))), (None, None))
        po = next((p2 for cols in OPEN_SETS if (p2 := implied(r, cols))), None)
        ou = next((p3 for cols in OVER_SETS if (p3 := implied(r, cols))), None)
        xg = (_xg(r.get("HxG")), _xg(r.get("AxG")))
        out.append({"game_id": gid, "competition": competition, "referee": (r.get("Referee") or None) and r["Referee"][:64],
                    **{col: _i(r.get(k)) for k, col in STATS.items()}, "home_xg": xg[0], "away_xg": xg[1],
                    "odds_source": src, "p_home": p and p[0], "p_draw": p and p[1], "p_away": p and p[2],
                    "p_home_open": po and po[0], "p_draw_open": po and po[1], "p_away_open": po and po[2],
                    "p_over25": ou and ou[0]})
    return out, unmatched


def _xg(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


FILL_ODDS = """
INSERT INTO soccer_odds (game_id, competition, providers, n_books, p_home, p_draw, p_away, total_line, p_over,
                         is_closing, fetched_at, source_id, ingestion_run_id, source_updated_at, updated_at)
SELECT f.game_id, f.competition, 'football-data.co.uk ' || f.odds_source, 1, f.p_home, f.p_draw, f.p_away,
       CASE WHEN f.p_over25 IS NOT NULL THEN 2.5 END, f.p_over25, f.odds_source LIKE '%close', now(),
       f.source_id, f.ingestion_run_id, now(), now()
FROM soccer_fdcuk_matches f
WHERE f.competition = :c AND f.p_home IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM soccer_odds o WHERE o.game_id = f.game_id)
"""


def sync_fdcuk(engine: Engine, league: League, seasons: list[int], *, get=http_get, fill_odds: bool = True) -> dict:
    div = DIVS.get(league.slug)
    if div is None:
        return {"league": league.slug, "skipped": "no football-data.co.uk file for this competition"}

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        tot = {"rows": 0, "matched": 0, "unmatched": 0, "with_odds": 0, "odds_filled": 0}
        for season in seasons:
            raw = get(URL.format(code=season_code(season), div=div))
            if not raw:
                continue
            rows = read_csv(raw)
            with engine.connect() as c:
                games = [dict(r._mapping) for r in c.execute(text(
                    """SELECT game_id, game_date, home_team_id, away_team_id FROM soccer_games
                       WHERE competition = :c AND season = :s"""), {"c": league.competition, "s": season})]
            out, unmatched = build_rows(rows, games, league.competition)
            if unmatched:
                issues.add("fdcuk", f"{league.slug}:{season}", "unmatched_match", "warning" if unmatched > 5 else "info",
                           "skipped", count=unmatched)
            with engine.begin() as c:
                load.upsert(c, sm.SoccerFdcukMatch.__table__, pl.DataFrame(out, infer_schema_length=None) if out else pl.DataFrame(),
                            provenance={"source_id": source_id, "ingestion_run_id": run_id,
                                        "source_updated_at": datetime.now(timezone.utc)})
            tot["rows"] += len(rows); tot["matched"] += len(out); tot["unmatched"] += unmatched
            tot["with_odds"] += sum(1 for o in out if o["p_home"] is not None)
        if fill_odds:
            with engine.begin() as c:
                tot["odds_filled"] = c.execute(text(FILL_ODDS), {"c": league.competition}).rowcount
        return {"league": league.slug, "seasons": [seasons[0], seasons[-1]] if seasons else [], **tot}

    return tracked(engine, "fdcuk", "fdcuk", {"league": league.slug}, run, retries=2)
