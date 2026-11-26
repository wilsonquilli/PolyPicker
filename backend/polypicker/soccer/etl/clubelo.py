"""Club Elo (clubelo.com, read through the ``soccerdata`` library's ``ClubElo`` reader) -> soccer_clubelo:
daily-resolution Elo history for European clubs.

``read_by_date`` = every rated club on a date (team, country, level, elo, from, to); ``read_team_history`` = one
club's full history. soccerdata caches the CSVs under ~/soccerdata/data/ClubElo. Clubs to fetch: those in a top-five league (level 1) on each season's opening date, plus any club
in the Champions / Europa League pools. Clubs map to ESPN ids per league-season by a one-to-one name assignment
between the ~20 Club Elo level-1 clubs of that country and the ~20 ESPN clubs of that league season (names like
"Forest" or "Bilbao" can't be matched alone, but the constraint that each club is used once settles them).
"""

from __future__ import annotations

import difflib
import logging
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

import numpy as np
import polars as pl
from scipy.optimize import linear_sum_assignment
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer import models as sm
from polypicker.soccer.etl.crosscheck import norm

API = "https://api.clubelo.com/{q}"
COUNTRY = {"eng.1": "ENG", "esp.1": "ESP", "ger.1": "GER", "ita.1": "ITA", "fra.1": "FRA"}
PAUSE = 0.5
# Club Elo names with nothing in common with ESPN's
ALIASES = {"Bilbao": "Athletic Club", "Atletico": "Atletico Madrid", "Paris SG": "Paris Saint-Germain",
           "Koeln": "FC Koln", "Gladbach": "Borussia Monchengladbach", "Inter": "Internazionale",
           "Man United": "Manchester United", "Man City": "Manchester City"}
log = logging.getLogger(__name__)


def _rows(df) -> list[dict]:
    """soccerdata frame -> our row dicts (Club, Country, Level, Elo, From, To)."""
    df = df.reset_index()
    out = []
    for r in df.to_dict("records"):
        out.append({"Club": r.get("team"), "Country": r.get("country"),
                    "Level": None if r.get("level") != r.get("level") else str(int(r["level"])) if r.get("level") is not None else None,
                    "Elo": r.get("elo"), "From": str(r.get("from"))[:10], "To": str(r.get("to"))[:10]})
    return out


def http_get(q: str) -> list[dict]:
    """A date ('YYYY-MM-DD') -> that day's ratings; anything else -> that club's history."""
    import soccerdata as sd
    reader = sd.ClubElo()
    if len(q) == 10 and q[4] == "-" and q[7] == "-":
        return _rows(reader.read_by_date(q))
    try:
        return _rows(reader.read_team_history(q))
    except ValueError:      # club unknown to clubelo.com
        return []


def _sim(a: str, b: str) -> float:
    a, b = norm(ALIASES.get(a, a)), norm(b)
    if a == b:
        return 1.0
    s = difflib.SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:                     # "forest" in "nottingham forest", "bilbao" vs "athletic bilbao"
        s = max(s, 0.85)
    return s


def assign(elo_clubs: list[str], espn: dict[int, list[str]]) -> dict[str, int]:
    """One-to-one Club Elo name -> ESPN team id by best total name similarity (``espn``: id -> names)."""
    ids = list(espn)
    if not elo_clubs or not ids:
        return {}
    cost = np.array([[1 - max(_sim(c, n) for n in espn[t]) for t in ids] for c in elo_clubs])
    rows, cols = linear_sum_assignment(cost)
    return {elo_clubs[r]: ids[c] for r, c in zip(rows, cols) if cost[r, c] < 0.6}


def sync_clubelo(engine: Engine, seasons: list[int], *, get=http_get, pause: float = PAUSE) -> dict:
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        votes: dict[str, Counter] = defaultdict(Counter)
        clubs: set[str] = set()
        with engine.connect() as c:
            for season in seasons:
                snap = get(date(season, 9, 1).isoformat())
                time.sleep(pause)
                for comp, cc in COUNTRY.items():
                    rows = c.execute(text("""
                        SELECT DISTINCT t.team_id, t.name, t.short_name FROM soccer_games g
                        JOIN soccer_teams t ON t.team_id IN (g.home_team_id, g.away_team_id)
                        WHERE g.competition = :c AND g.season = :s"""), {"c": comp, "s": season}).all()
                    espn = {r.team_id: [x for x in (r.name, r.short_name) if x] for r in rows}
                    top = [r["Club"] for r in snap if r.get("Country") == cc and r.get("Level") == "1"]
                    clubs.update(top)
                    for club, tid in assign(top, espn).items():
                        votes[club][tid] += 1
                    if len(top) and len(assign(top, espn)) < min(len(top), len(espn)) - 1:
                        issues.add("clubelo", f"{comp}:{season}", "unassigned_clubs", "info", "kept")
        mapping = {club: v.most_common(1)[0][0] for club, v in votes.items()}
        hist = []
        for club in sorted(clubs):
            for r in get(club):
                try:
                    hist.append({"club": club[:40], "from_date": date.fromisoformat(r["From"]),
                                 "to_date": date.fromisoformat(r["To"]), "team_id": mapping.get(club),
                                 "country": (r.get("Country") or "")[:3] or None,
                                 "level": int(r["Level"]) if r.get("Level") else None, "elo": float(r["Elo"])})
                except (KeyError, TypeError, ValueError):
                    continue
            time.sleep(pause)
        hist = [h for h in hist if h["to_date"] >= date(min(seasons), 7, 1)]
        with engine.begin() as c:
            res = load.upsert(c, sm.SoccerClubElo.__table__, pl.DataFrame(hist) if hist else pl.DataFrame(),
                              provenance={"source_id": source_id, "ingestion_run_id": run_id,
                                          "source_updated_at": datetime.now(timezone.utc)})
        return {"clubs": len(clubs), "mapped": len(mapping), "rows": len(hist), **res}

    return tracked(engine, "clubelo", "clubelo", {"seasons": [seasons[0], seasons[-1]]}, run, retries=2)
