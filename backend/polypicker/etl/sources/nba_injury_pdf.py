"""Official NBA injury report (the league's PDF) -> nba_official_injuries.

Teams must file it for every game; the league republishes it through the day (every 15 minutes on game
days since 2025-26, hourly before) at ``ak-static.cms.nba.com/referee/injury/Injury-Report_<date>_<time>.pdf``
(time ``05_00PM``; ``05PM`` in older seasons). Each row: game date / time / matchup (``MEM@CHA``), team,
player ("Last, First"), status (Out, Doubtful, Questionable, Probable, Available) and reason. Teams that
haven't filed yet show "NOT YET SUBMITTED".

It's more complete than ESPN's list (G League assignments, every questionable tag) and is the source
sportsbooks react to. Parsing uses text positions (pypdf) against each page's column headers; game,
team and matchup cells are carried down to the rows below them. Public file, no key; archive back to 2021.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import Engine, text

from polypicker.db import models as m
from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked

BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{d}_{t}.pdf"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PolyPicker/1.0)"}
ET = ZoneInfo("America/New_York")
NOT_SUBMITTED = "Not Yet Submitted"
TEAM_ROW = "(team)"          # player_name of the row that records a team's "not yet submitted" state
STATUS_WEIGHT = {"out": 0.0, "doubtful": 0.15, "questionable": 0.5, "probable": 0.9, "available": 1.0}
COLUMNS = ("Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason")
SUFFIXES = ("iii", "ii", "iv", "jr", "sr")

log = logging.getLogger(__name__)
Fetcher = Callable[[str], bytes | None]


def http_fetch(url: str) -> bytes | None:
    import requests
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    return r.content if r.content[:4] == b"%PDF" else None


def report_urls(at: datetime) -> list[str]:
    """Both URL styles for a report time (ET)."""
    t = at.astimezone(ET)
    d = t.strftime("%Y-%m-%d")
    out = [BASE.format(d=d, t=t.strftime("%I_%M%p"))]
    if t.minute == 0:
        out.append(BASE.format(d=d, t=t.strftime("%I%p")))
    return out


# ---------------------------------------------------------------- PDF -> rows

def _fragments(page) -> list[tuple[float, float, str]]:
    frags: list[tuple[float, float, str]] = []

    def visit(txt, cm, tm, _font, _size):
        if txt and txt.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            frags.append((round(y, 1), x, txt.strip()))

    page.extract_text(visitor_text=visit)
    return frags


def _lines(frags) -> list[tuple[float, list[tuple[float, str]]]]:
    """Group fragments into text lines (same baseline, within 1pt), top to bottom."""
    out: list[tuple[float, list[tuple[float, str]]]] = []
    for y, x, t in sorted(frags, key=lambda f: (-f[0], f[1])):
        if out and abs(out[-1][0] - y) <= 1.0:
            out[-1][1].append((x, t))
        else:
            out.append((y, [(x, t)]))
    return out


def _header(lines) -> list[float] | None:
    """x where each column starts, from the header line ('Game Date  Game Time  Matchup ...')."""
    for _, cells in lines:
        joined = " ".join(t for _, t in sorted(cells))
        if "Matchup" in joined and "Status" in joined:
            words = sorted(cells)
            starts = []
            for col in COLUMNS:
                first = col.split()[0]
                xs = [x for x, t in words if t == first or t.startswith(first)]
                # "Game" appears twice (Game Date, Game Time): take them in order
                taken = [s for s in starts]
                xs = [x for x in xs if all(abs(x - s) > 1 for s in taken)]
                if not xs:
                    return None
                starts.append(min(xs))
            return starts
    return None


def parse_pdf(blob: bytes) -> list[dict]:
    """Rows: game_date, game_time, matchup, team, player ("Last, First" or TEAM_ROW), status, reason."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(blob))
    rows: list[dict] = []
    cur = {"game_date": None, "game_time": None, "matchup": None, "team": None}
    starts: list[float] | None = None
    for page in reader.pages:
        lines = _lines(_fragments(page))
        starts = _header(lines) or starts
        if not starts:
            continue
        for _, cells in lines:
            cols = [""] * len(COLUMNS)
            for x, t in sorted(cells):
                i = max((k for k, s in enumerate(starts) if x >= s - 2), default=None)
                if i is not None:
                    cols[i] = f"{cols[i]} {t}".strip()
            gdate, gtime, matchup, team, player, status, reason = cols
            joined = " ".join(cols)
            if "Matchup" in matchup or gdate.startswith("Game") or "Report:" in joined \
                    or re.search(r"Page \d+ of \d+", joined):
                continue
            if re.match(r"\d{2}/\d{2}/\d{4}", gdate):
                cur["game_date"] = datetime.strptime(gdate[:10], "%m/%d/%Y").date()
            if re.match(r"\d{1,2}:\d{2}", gtime):
                cur["game_time"] = gtime
            if re.match(r"^[A-Z]{2,3}@[A-Z]{2,3}$", matchup.replace(" ", "")):
                cur["matchup"] = matchup.replace(" ", "")
                cur["team"] = None
            if team:
                cur["team"] = team
            tail = f"{player} {status} {reason}".upper().replace(" ", "")
            if "NOTYETSUBMITTED" in tail:
                rows.append({**cur, "player": TEAM_ROW, "status": NOT_SUBMITTED, "reason": None})
                continue
            if player and status:
                rows.append({**cur, "player": player, "status": status.title(), "reason": reason or None})
    return [r for r in rows if r["matchup"] and r["team"] and r["game_date"]]


# ---------------------------------------------------------------- matching

def _letters(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _strip_suffix(last: str) -> str:
    for suf in SUFFIXES:
        if last.endswith(suf) and len(last) > len(suf) + 2:
            return last[: -len(suf)]
    return last


def pdf_keys(name: str) -> list[str]:
    """'Pippen Jr., Scotty' -> ['pippenjrscotty', 'pippenscotty']."""
    if "," not in name:
        return [_letters(name)]
    last, first = name.split(",", 1)
    la, fi = _letters(last), _letters(first)
    return list(dict.fromkeys([la + fi, _strip_suffix(la) + fi]))


def db_keys(full_name: str) -> list[str]:
    """'Scotty Pippen Jr.' -> ['pippenjrscotty', 'pippenscotty']."""
    parts = full_name.split()
    if len(parts) < 2:
        return [_letters(full_name)]
    first, rest = _letters(parts[0]), _letters(" ".join(parts[1:]))
    return list(dict.fromkeys([rest + first, _strip_suffix(rest) + first]))


def team_for(team_text: str, matchup: str, codes: dict[str, tuple[int, str]]) -> int | None:
    """Pick the matchup side whose nickname appears in the team cell ('LA Clippers', 'DallasMavericks')."""
    t = _letters(team_text)
    for code in matchup.split("@"):
        tid_name = codes.get(code)
        if tid_name and _letters(tid_name[1].split()[-1]) in t:
            return tid_name[0]
    return None


def resolve(rows: list[dict], report_at: datetime, conn, issues: IssueLog) -> pl.DataFrame:
    teams = conn.execute(text("SELECT team_id, abbreviation, full_name FROM teams")).all()
    codes = {r.abbreviation: (r.team_id, r.full_name) for r in teams}
    dates = sorted({r["game_date"] for r in rows})
    games = {(g.game_date_et, g.away, g.home): g.game_id for g in conn.execute(text("""
        SELECT g.game_id, g.game_date_et, a.abbreviation away, h.abbreviation home FROM games g
        JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
        WHERE g.game_date_et = ANY(:d)"""), {"d": dates})} if dates else {}
    recent = conn.execute(text("""
        SELECT DISTINCT ON (p.player_id, p.team_id) p.player_id, p.team_id, pl.full_name
        FROM player_game_stats p JOIN games g USING (game_id) JOIN players pl USING (player_id)
        WHERE g.game_date_et BETWEEN CAST(:d AS date) - 400 AND CAST(:d AS date)"""),
        {"d": report_at.astimezone(ET).date()}).all()
    roster: dict[int, dict[str, int]] = {}
    league: dict[str, set[int]] = {}
    for r in recent:
        for k in db_keys(r.full_name):
            roster.setdefault(r.team_id, {}).setdefault(k, r.player_id)
            league.setdefault(k, set()).add(r.player_id)
    out = []
    for r in rows:
        team_id = team_for(r["team"], r["matchup"], codes)
        if team_id is None:
            issues.add("nba_official_injury", f"{r['matchup']} {r['team']}", "unknown_team", "warning", "skipped")
            continue
        away, home = r["matchup"].split("@")
        pid = None
        if r["player"] != TEAM_ROW:
            for k in pdf_keys(r["player"]):
                pid = roster.get(team_id, {}).get(k)
                if pid is None and len(league.get(k, ())) == 1:
                    pid = next(iter(league[k]))
                if pid is not None:
                    break
            if pid is None:
                issues.add("nba_official_injury", r["player"], "unmatched_player", "info", "kept_no_id", team_id=team_id)
        out.append({"report_at": report_at, "team_id": team_id, "player_name": r["player"][:128],
                    "game_date": r["game_date"], "matchup": r["matchup"][:16],
                    "game_id": games.get((r["game_date"], away, home)), "player_id": pid,
                    "status": r["status"][:32], "reason": (r["reason"] or None) and r["reason"][:200]})
    if not out:
        return pl.DataFrame()
    return pl.DataFrame(out, infer_schema_length=None).unique(["report_at", "team_id", "player_name"], keep="last")


# ---------------------------------------------------------------- sync

def find_report(at: datetime, fetch: Fetcher, *, step_minutes: int = 15, lookback_hours: float = 24
                ) -> tuple[datetime, bytes] | None:
    """Newest report published at or before ``at`` (ET quarter hours, then the hourly style)."""
    t = at.astimezone(ET).replace(second=0, microsecond=0)
    t -= timedelta(minutes=t.minute % step_minutes)
    for _ in range(int(lookback_hours * 60 / step_minutes) + 1):
        for url in report_urls(t):
            blob = fetch(url)
            if blob:
                return t.astimezone(timezone.utc), blob
        t -= timedelta(minutes=step_minutes)
    return None


def _store(engine: Engine, report_at: datetime, blob: bytes, run_id: int, source_id: int, issues: IssueLog) -> dict:
    rows = parse_pdf(blob)
    with engine.connect() as conn:
        df = resolve(rows, report_at, conn, issues)
    if df.height:
        df = df.with_columns(pl.lit(datetime.now(timezone.utc)).alias("fetched_at"))
    with engine.begin() as conn:
        res = load.upsert(conn, m.NbaOfficialInjury.__table__, df,
                          provenance={"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": report_at})
    players = df.filter(pl.col("player_name") != TEAM_ROW) if df.height else df
    return {"rows": df.height, "players": players.height,
            "matched": int(players["player_id"].is_not_null().sum()) if players.height else 0, **res}


def sync_official_injuries(engine: Engine, *, retries: int = 2, fetch: Fetcher = http_fetch,
                           now: datetime | None = None) -> dict:
    """Load the newest official report (searching back up to a day)."""
    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        hit = find_report(now or datetime.now(timezone.utc), fetch)
        if hit is None:
            return {"report": None}
        report_at, blob = hit
        return {"report": report_at.astimezone(ET).strftime("%Y-%m-%d %I:%M %p ET"),
                **_store(engine, report_at, blob, run_id, source_id, issues)}

    return tracked(engine, "nba_injury_report", "nba_official_injuries", {}, run, retries=retries)


def backfill_official_injuries(engine: Engine, first_season: int, *, fetch: Fetcher = http_fetch,
                               workers: int = 4) -> dict:
    """One pregame report per game day: the last one before the day's first tip (at most 5 PM ET)."""
    from concurrent.futures import ThreadPoolExecutor

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        with engine.connect() as conn:
            days = conn.execute(text("""
                SELECT g.game_date_et, min(g.tipoff_utc) first_tip FROM games g
                WHERE g.season >= :s AND g.status = 'final'
                  AND g.season_type IN ('regular', 'playoffs', 'play_in', 'cup_final')
                  AND NOT EXISTS (SELECT 1 FROM nba_official_injuries o WHERE o.game_date = g.game_date_et)
                GROUP BY 1 ORDER BY 1"""), {"s": first_season}).all()

        def job(d):
            five = datetime.combine(d.game_date_et, time(17, 0), ET)
            at = min(five, d.first_tip.astimezone(ET) - timedelta(minutes=30))
            return d.game_date_et, find_report(at, fetch, step_minutes=30, lookback_hours=6)

        found = missing = 0
        stats = {"rows": 0, "players": 0, "matched": 0}
        with ThreadPoolExecutor(workers) as ex:
            for day, hit in ex.map(job, days):
                if hit is None:
                    missing += 1
                    issues.add("nba_official_injury", str(day), "report_not_found", "info", "skipped")
                    continue
                found += 1
                st = _store(engine, hit[0], hit[1], run_id, source_id, issues)
                for k in stats:
                    stats[k] += st[k]
        return {"days": len(days), "reports_found": found, "days_missing": missing, **stats}

    return tracked(engine, "nba_injury_report", "nba_official_injuries_backfill", {"first_season": first_season},
                   run, retries=1)


def latest_official(engine: Engine, now: datetime | None = None) -> pl.DataFrame:
    """Rows of the newest report from the last 18 hours (empty if none)."""
    now = now or datetime.now(timezone.utc)
    try:
        with engine.connect() as conn:
            res = conn.execute(text("""
                SELECT game_id, team_id, player_id, player_name, status, reason, report_at FROM nba_official_injuries
                WHERE report_at = (SELECT max(report_at) FROM nba_official_injuries
                                   WHERE report_at BETWEEN :now - interval '18 hours' AND :now)"""), {"now": now})
            cols, rows = list(res.keys()), res.fetchall()
    except Exception as e:  # noqa: BLE001 -- table missing / DB hiccup: callers fall back to ESPN
        log.warning("official injury report unavailable: %s", e)
        return pl.DataFrame()
    return pl.DataFrame(rows, schema=cols, orient="row", infer_schema_length=None) if rows else pl.DataFrame()


def status_weight(status: str | None) -> float | None:
    return STATUS_WEIGHT.get((status or "").strip().lower())
