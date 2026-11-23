"""ESPN NBA injury report -> nba_injury_reports (one row per player per ET day).

The report lists every currently injured / day-to-day player per team with a status (Out,
Doubtful, Questionable, Day-To-Day, ...). Players are matched to our player ids by normalised name,
first among the team's recent players, then league-wide when the name is unique. Unofficial
endpoint: failures are retried and logged; forecasts fall back to box-score availability.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import Engine, text

from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PolyPicker/1.0)", "Accept": "application/json"}
ET = ZoneInfo("America/New_York")
TEAM_ALIASES = {"la clippers": "los angeles clippers"}

# Probability-style weight that a listed player plays (used by the lineup features).
STATUS_WEIGHT = {"out": 0.0, "doubtful": 0.15, "questionable": 0.5, "day-to-day": 0.6, "probable": 0.9}

Getter = Callable[[], dict]


def http_get() -> dict:
    import requests
    r = requests.get(URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.json()


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’`-]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def status_weight(status: str | None) -> float | None:
    return STATUS_WEIGHT.get((status or "").strip().lower())


def _date(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v[:10]) if v else None
    except ValueError:
        return None


def _ts(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def parse(payload: dict, teams: dict[str, int], roster: dict[int, dict[str, int]], league: dict[str, list[int]],
          fetched_at: datetime, issues: IssueLog) -> pl.DataFrame:
    """teams: normalised full name -> team_id; roster: team_id -> {normalised name: player_id};
    league: normalised name -> [player_id, ...]."""
    day = fetched_at.astimezone(ET).date()
    rows = []
    for t in payload.get("injuries", []):
        tname = (t.get("displayName") or "").lower()
        team_id = teams.get(TEAM_ALIASES.get(tname, tname))
        if team_id is None:
            issues.add("nba_injury", t.get("displayName"), "unknown_team", "warning", "skipped")
            continue
        for i in t.get("injuries", []):
            a = i.get("athlete") or {}
            hrefs = " ".join(link.get("href", "") for link in a.get("links", []))
            mt = re.search(r"/id/(\d+)", hrefs)
            aid = mt.group(1) if mt else f"inj{i.get('id')}"
            name = a.get("displayName") or ""
            n = norm_name(name)
            pid = roster.get(team_id, {}).get(n)
            if pid is None and len(league.get(n, [])) == 1:
                pid = league[n][0]
            if pid is None:
                issues.add("nba_injury", name, "unmatched_player", "info", "kept_no_id", team_id=team_id)
            d = i.get("details") or {}
            detail = " ".join(x for x in (d.get("type"), d.get("detail")) if x) or None
            rows.append({"report_date": day, "espn_athlete_id": aid[:16], "player_id": pid, "team_id": team_id,
                         "player_name": name[:128], "status": (i.get("status") or "Unknown")[:32],
                         "detail": detail[:128] if detail else None, "short_comment": i.get("shortComment"),
                         "return_date": _date(d.get("returnDate")), "reported_at": _ts(i.get("date")),
                         "fetched_at": fetched_at})
    # one row per athlete per day (ESPN occasionally repeats an athlete)
    return pl.DataFrame(rows, infer_schema_length=None).unique(["report_date", "espn_athlete_id"], keep="last") \
        if rows else pl.DataFrame()


def sync_injuries(engine: Engine, *, retries: int = 2, getter: Getter = http_get) -> dict:
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        payload = getter()
        now = datetime.now(timezone.utc)
        with engine.connect() as conn:
            teams = {r.full_name.lower(): r.team_id for r in conn.execute(text("SELECT team_id, full_name FROM teams"))}
            recent = conn.execute(text("""
                SELECT DISTINCT ON (p.player_id) p.player_id, p.team_id, pl.full_name
                FROM player_game_stats p JOIN games g USING (game_id) JOIN players pl USING (player_id)
                WHERE g.game_date_et >= current_date - 400
                ORDER BY p.player_id, g.game_date_et DESC""")).all()
        roster: dict[int, dict[str, int]] = {}
        league: dict[str, list[int]] = {}
        for r in recent:
            n = norm_name(r.full_name)
            roster.setdefault(r.team_id, {})[n] = r.player_id
            league.setdefault(n, []).append(r.player_id)
        df = parse(payload, teams, roster, league, now, issues)
        with engine.begin() as conn:
            res = load.upsert(conn, m.NbaInjuryReport.__table__, df,
                              provenance={"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": now})
        matched = int(df["player_id"].is_not_null().sum()) if df.height else 0
        return {"players_listed": df.height, "matched_to_player_id": matched, **res,
                "statuses": dict(df.group_by("status").len().iter_rows()) if df.height else {}}

    return tracked(engine, "espn_nba", "nba_injuries", {}, run, retries=retries)


def latest_reports(engine: Engine) -> pl.DataFrame:
    """Each player's most recent report from the last 2 days (older entries are stale)."""
    from polypicker.ml.data import _read
    return _read(engine, """
        SELECT DISTINCT ON (espn_athlete_id) report_date, espn_athlete_id, player_id, team_id, player_name, status,
               detail, short_comment, return_date, fetched_at
        FROM nba_injury_reports
        WHERE report_date >= current_date - 1
        ORDER BY espn_athlete_id, report_date DESC""")
