"""ESPN soccer adapter: fixtures, results, half-time scores and team match stats (shots, shots on target,
possession, ...) -- the primary source for history (2003+ available) and the live/up-to-date feed.

ESPN publishes each season's list of match dates (``calendar``), so a league season is ~100 scoreboard calls
plus one summary call per finished match. Cups publish stage date ranges instead, so every day in each stage
is fetched (~300 calls a season). Cup matches also carry a stage, leg, aggregate note, and extra-time /
penalty outcome; the 90-minute score (what we forecast) comes from the per-period line scores. Backfills commit season by season and skip matches whose
stats are already stored, so an interrupted run resumes where it stopped. Unofficial endpoints:
failures are logged and retried on the next run.
"""

from __future__ import annotations

import re

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import Engine, text

from polypicker.etl import load
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer import models as sm
from polypicker.soccer.leagues import BY_COMPETITION

log = logging.getLogger(__name__)
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PolyPicker/1.0)", "Accept": "application/json"}
ET = ZoneInfo("America/New_York")
COMPETITIONS = {"eng.1": "English Premier League"}

STAT_MAP = {"totalShots": "shots", "shotsOnTarget": "shots_on_target", "possessionPct": "possession",
            "wonCorners": "corners", "foulsCommitted": "fouls", "yellowCards": "yellow_cards", "redCards": "red_cards",
            "offsides": "offsides", "saves": "saves", "totalPasses": "passes", "passPct": "pass_pct"}
FLOAT_STATS = {"possession", "pass_pct"}

Getter = Callable[[str, dict], dict]
_LOCK = threading.Lock()
FULL_EVERY = timedelta(hours=20)   # quick syncs in between only re-read dates that already have a match



def _stage(slug: str | None) -> str | None:
    """Competition stage from ESPN's season slug. League matches are sometimes tagged with the season itself
    ("2026-27-english-premier-league") and sometimes not at all: both mean the regular season (no stage), so the
    table never splits in two halfway through a season."""
    if not slug or re.match(r"^\d{4}(-\d{2})?-", slug):
        return None
    return slug

def http_get(path: str, params: dict) -> dict:
    import requests
    r = requests.get(f"{BASE}/{path}", params=params, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.json()


def _num(v, as_float=False):
    try:
        f = float(str(v).replace("%", ""))
    except (TypeError, ValueError):
        return None
    return f if as_float else int(round(f))


def map_status(st: dict) -> str:
    t = (st or {}).get("type") or {}
    name, state = t.get("name", ""), t.get("state", "")
    if "POSTPONED" in name:
        return "postponed"
    if "CANCEL" in name or "ABANDON" in name:
        return "canceled"
    if state == "post" and t.get("completed"):
        return "final"
    if state == "in":
        return "live"
    if state == "pre":
        return "scheduled"
    return "unknown"


def _hex(v) -> str | None:
    """ESPN colors are 6-digit hex without '#'; anything else (empty, '#xxxxxx', names) is dropped."""
    s = str(v or "").lstrip("#")
    return s.lower() if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s) else None


def _is_cup(competition: str) -> bool:
    lg = BY_COMPETITION.get(competition)
    return bool(lg and lg.kind == "cup")


def decided_by(st: dict) -> str | None:
    """'aet' / 'pen' for matches settled in extra time or on penalties (cup knockouts), else None."""
    name = (((st or {}).get("type") or {}).get("name") or "").upper()
    return "pen" if name.endswith("_PEN") else "aet" if name.endswith("_AET") else None


def parse_scoreboard_event(ev: dict, competition: str) -> tuple[dict, list[dict]] | None:
    comp = (ev.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    if "home" not in sides or "away" not in sides:
        return None
    kickoff = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
    status = map_status(ev.get("status") or comp.get("status"))
    teams = []
    for c in (sides["home"], sides["away"]):
        t = c.get("team") or {}
        teams.append({"team_id": int(t["id"]), "name": (t.get("displayName") or t.get("name") or "")[:64],
                      "short_name": (t.get("shortDisplayName") or "")[:40] or None,
                      "abbreviation": (t.get("abbreviation") or "")[:16],
                      "color": _hex(t.get("color")), "alt_color": _hex(t.get("alternateColor"))})
    hs, as_ = (_num(sides["home"].get("score")), _num(sides["away"].get("score"))) if status in ("final", "live") else (None, None)
    decided = decided_by(ev.get("status") or comp.get("status")) if status == "final" else None
    result = None
    if status == "final" and hs is not None and as_ is not None and not decided:
        result = "H" if hs > as_ else "A" if as_ > hs else "D"   # decided matches: set from the 90-minute score
    notes = [n.get("headline") or n.get("text") for n in comp.get("notes") or [] if n.get("headline") or n.get("text")]
    leg = (comp.get("leg") or {}).get("value") if isinstance(comp.get("leg"), dict) else None
    lg = BY_COMPETITION.get(competition)
    season = int((ev.get("season") or {}).get("year") or 0)
    if lg and lg.calendar_year:
        # ESPN labels pre-2016 Libertadores seasons with the previous year; a calendar-year season is its kickoff year.
        season = kickoff.astimezone(ET).year
    g = {"game_id": int(ev["id"]), "competition": competition, "season": season,
         "kickoff_utc": kickoff, "game_date": kickoff.astimezone(ET).date(),
         "home_team_id": int(sides["home"]["team"]["id"]), "away_team_id": int(sides["away"]["team"]["id"]),
         "status": status, "home_score": hs, "away_score": as_, "result": result,
         "venue": ((comp.get("venue") or ev.get("venue") or {}).get("fullName")),
         "attendance": _num(comp.get("attendance")) or None,
         "stage": _stage((ev.get("season") or {}).get("slug")), "leg": _num(leg),
         # ESPN rarely flags neutral venues; single-match cup finals are played at one.
         "neutral": bool(comp.get("neutralSite")) or (_is_cup(competition) and (ev.get("season") or {}).get("slug") == "final"
                                                      and leg is None),
         "decided": decided, "home_shootout": _num(sides["home"].get("shootoutScore")) if decided == "pen" else None,
         "away_shootout": _num(sides["away"].get("shootoutScore")) if decided == "pen" else None,
         "note": notes[0][:200] if notes else None}
    return g, teams


def score_after_90(s: dict) -> dict:
    """{"home": n, "away": n} after 90 minutes (+ stoppage), or {} if it can't be trusted. Goals from the timeline
    (periods 1-2, shootout excluded) are used when they add up to the final score; otherwise the 1st + 2nd half line
    scores, if they're sane. ESPN's line scores are sometimes corrupt (e.g. a -1 in extra time)."""
    hc = ((s.get("header") or {}).get("competitions") or [{}])[0]
    side_of = {str((c.get("team") or {}).get("id")): c.get("homeAway") for c in hc.get("competitors") or []}
    final = {c.get("homeAway"): _num(c.get("score")) for c in hc.get("competitors") or []}
    if None in final.values() or set(final) != {"home", "away"}:
        return {}
    tot, reg = {"home": 0, "away": 0}, {"home": 0, "away": 0}
    for k in s.get("keyEvents") or []:
        if not k.get("scoringPlay") or k.get("shootout"):
            continue
        side = side_of.get(str((k.get("team") or {}).get("id")))
        if side is None:
            tot = None
            break
        tot[side] += 1
        if ((k.get("period") or {}).get("number") or 99) <= 2:
            reg[side] += 1
    if tot == final:
        return reg
    lines = {}
    for c in hc.get("competitors") or []:
        parts = [_num(x.get("displayValue")) for x in (c.get("linescores") or [])[:2]]
        if len(parts) < 2 or None in parts or min(parts) < 0:
            return {}
        lines[c.get("homeAway")] = sum(parts)
    return lines if all(lines[x] <= final[x] for x in ("home", "away")) else {}


def parse_summary(s: dict, game_id: int) -> tuple[dict, list[dict]]:
    """Half-time score + team match stats from an ESPN match summary."""
    hc = ((s.get("header") or {}).get("competitions") or [{}])[0]
    ht = {}
    for c in hc.get("competitors") or []:
        ls = c.get("linescores") or []
        if ls:
            ht[c.get("homeAway")] = _num(ls[0].get("displayValue"))
    reg = score_after_90(s)
    rows = []
    for t in (s.get("boxscore") or {}).get("teams") or []:
        team = t.get("team") or {}
        st = {x.get("name"): x.get("displayValue") for x in t.get("statistics") or []}
        if not st:
            continue
        row = {"game_id": game_id, "team_id": int(team["id"]), "is_home": t.get("homeAway") == "home"}
        for src, dst in STAT_MAP.items():
            v = _num(st.get(src), dst in FLOAT_STATS)
            if dst == "pass_pct" and v is not None and v > 1.0:
                v = v / 100.0
            row[dst] = v
        rows.append(row)
    return {"home_ht_score": ht.get("home"), "away_ht_score": ht.get("away"),
            "home_score_90": reg.get("home"), "away_score_90": reg.get("away")}, rows


def calendar_dates(cal: list) -> list[date]:
    """Match dates from an ESPN calendar: leagues list dates; cups list stages with start/end ranges (every day
    in each range is a candidate, in US-Eastern like ``game_date``)."""
    out: set[date] = set()
    for c in cal:
        if isinstance(c, str):
            out.add(date.fromisoformat(c[:10]))
            continue
        for e in (c or {}).get("entries") or []:
            try:
                a = datetime.fromisoformat(e["startDate"].replace("Z", "+00:00")).astimezone(ET).date()
                b = datetime.fromisoformat(e["endDate"].replace("Z", "+00:00")).astimezone(ET).date()
            except (KeyError, ValueError):
                continue
            while a <= b:
                out.add(a)
                a += timedelta(days=1)
    return sorted(out)


class EspnSoccerSource:
    code = "espn_soccer"

    def __init__(self, engine: Engine, competition: str = "eng.1", *, get: Getter | None = None, pause: float = 0.25,
                 workers: int = 4):
        self.engine, self.comp, self.get, self.pause, self.workers = engine, competition, get or http_get, pause, workers
        self.summary: dict = {"dates": 0, "events": 0, "summaries": 0, "failed": 0}

    def _call(self, path: str, params: dict, issues: IssueLog, backoff: tuple[float, ...] = (2, 10, 30)) -> dict | None:
        """One ESPN request; connection blips and 5xx are retried with backoff (a short network outage otherwise
        silently empties whole seasons of a backfill). A 4xx isn't retried."""
        out = None
        for i, wait in enumerate((0, *backoff)):
            if wait:
                time.sleep(wait if self.pause else 0)
            try:
                out = self.get(f"{self.comp}/{path}", params)
                break
            except Exception as e:  # noqa: BLE001
                status = getattr(getattr(e, "response", None), "status_code", None)
                if (status is not None and status < 500) or i == len(backoff):
                    with _LOCK:
                        self.summary["failed"] += 1
                    issues.add("soccer", f"{path}?{params}", "espn_fetch_failed", "warning", "skipped", error=str(e)[:200])
                    break
        if self.pause:
            time.sleep(self.pause)
        return out

    def season_dates(self, season: int, issues: IssueLog) -> list[date]:
        lg = BY_COMPETITION.get(self.comp)
        anchor = f"{season}0615" if lg and lg.calendar_year else f"{season + 1}0115"
        board = self._call("scoreboard", {"dates": anchor}, issues) or {}
        return calendar_dates(((board.get("leagues") or [{}])[0]).get("calendar") or [])

    def collect(self, dates: list[date], issues: IssueLog) -> tuple[list[dict], dict[int, dict]]:
        """One scoreboard call per date, ``workers`` at a time (each worker keeps the polite pause between calls);
        results are parsed in date order, so the outcome is the same as a serial fetch."""
        from concurrent.futures import ThreadPoolExecutor
        games, teams = {}, {}
        fetch = lambda d: self._call("scoreboard", {"dates": d.strftime("%Y%m%d")}, issues)  # noqa: E731
        if self.workers > 1 and len(dates) > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                boards = list(ex.map(fetch, dates))
        else:
            boards = [fetch(d) for d in dates]
        for board in boards:
            self.summary["dates"] += 1
            for ev in (board or {}).get("events") or []:
                parsed = parse_scoreboard_event(ev, self.comp)
                if parsed is None:
                    issues.add("soccer_game", ev.get("id"), "espn_event_unparsed", "warning", "skipped")
                    continue
                g, ts = parsed
                games[g["game_id"]] = g
                for t in ts:
                    teams[t["team_id"]] = t
        self.summary["events"] += len(games)
        return list(games.values()), teams

    def enrich(self, games: list[dict], have_stats: set[int], issues: IssueLog) -> list[dict]:
        """Half-time scores + team stats for finished matches that don't have them yet."""
        stats = []
        for g in games:
            # Matches settled in extra time are re-read until their 90-minute score is known.
            if g["status"] != "final" or (g["game_id"] in have_stats and not g.get("decided")):
                continue
            s = self._call("summary", {"event": g["game_id"]}, issues)
            if not s:
                continue
            ht, rows = parse_summary(s, g["game_id"])
            g.update(ht)
            if g.get("decided") and ht["home_score_90"] is not None and ht["away_score_90"] is not None:
                h90, a90 = ht["home_score_90"], ht["away_score_90"]
                g["result"] = "H" if h90 > a90 else "A" if a90 > h90 else "D"
            g["has_team_stats"] = bool(rows)
            for r in rows:
                r["goals"] = g["home_score"] if r["is_home"] else g["away_score"]
            stats += rows
            self.summary["summaries"] += 1
        return stats


GAME_SCHEMA = {"game_id": pl.Int64, "competition": pl.String, "season": pl.Int64, "kickoff_utc": pl.Datetime("us", "UTC"),
               "game_date": pl.Date, "home_team_id": pl.Int64, "away_team_id": pl.Int64, "status": pl.String,
               "home_score": pl.Int64, "away_score": pl.Int64, "result": pl.String, "venue": pl.String, "attendance": pl.Int64,
               "stage": pl.String, "leg": pl.Int64, "neutral": pl.Boolean, "decided": pl.String, "home_shootout": pl.Int64,
               "away_shootout": pl.Int64, "note": pl.String}
STATS_SCHEMA = {"game_id": pl.Int64, "team_id": pl.Int64, "is_home": pl.Boolean, "goals": pl.Int64, "shots": pl.Int64,
                "shots_on_target": pl.Int64, "possession": pl.Float64, "corners": pl.Int64, "fouls": pl.Int64,
                "yellow_cards": pl.Int64, "red_cards": pl.Int64, "offsides": pl.Int64, "saves": pl.Int64,
                "passes": pl.Int64, "pass_pct": pl.Float64}


def _store(engine: Engine, src: EspnSoccerSource, games: list[dict], teams: dict[int, dict], stats: list[dict],
           prov: dict) -> dict:
    out = {}
    with engine.begin() as conn:
        if teams:
            out["teams"] = load.upsert(conn, sm.SoccerTeam.__table__, pl.DataFrame(list(teams.values()), schema={
                "team_id": pl.Int64, "name": pl.String, "short_name": pl.String, "abbreviation": pl.String,
                "color": pl.String, "alt_color": pl.String}), provenance=prov)
        if games:
            gdf = pl.DataFrame([{k: g.get(k) for k in GAME_SCHEMA} for g in games], schema=GAME_SCHEMA)
            gdf, protected = load.protect_final_results(conn, gdf, table="soccer_games")
            out["games"] = load.upsert(conn, sm.SoccerGame.__table__, gdf, provenance=prov)
            out["final_results_protected"] = protected
            extra = [g for g in games if "has_team_stats" in g]
            for g in extra:   # half-time + stats flag, only for matches we just enriched
                conn.execute(text("""UPDATE soccer_games SET home_ht_score = :h, away_ht_score = :a, has_team_stats = :s,
                                       home_score_90 = :h90, away_score_90 = :a90,
                                       result = CASE WHEN decided IS NOT NULL THEN :r ELSE result END
                                     WHERE game_id = :g"""),
                             {"h": g.get("home_ht_score"), "a": g.get("away_ht_score"), "s": g["has_team_stats"], "g": g["game_id"],
                              "h90": g.get("home_score_90") if g.get("decided") else None,
                              "a90": g.get("away_score_90") if g.get("decided") else None, "r": g.get("result")})
        if stats:
            out["team_game_stats"] = load.upsert(conn, sm.SoccerTeamGameStats.__table__,
                                                 pl.DataFrame(stats, schema=STATS_SCHEMA), provenance=prov)
        # A decided match re-read from the scoreboard arrives without its 90-minute result; restore it.
        conn.execute(text("""
            UPDATE soccer_games SET result = CASE WHEN home_score_90 > away_score_90 THEN 'H'
                                                  WHEN away_score_90 > home_score_90 THEN 'A' ELSE 'D' END
            WHERE decided IS NOT NULL AND result IS NULL AND home_score_90 IS NOT NULL AND away_score_90 IS NOT NULL"""))
        out["marked_unknown"] = conn.execute(text("""
            UPDATE soccer_games SET status = 'unknown', updated_at = now()
            WHERE status = 'scheduled' AND kickoff_utc < now() - interval '4 hours'""")).rowcount
    return out


STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/{code}/standings"


def parse_groups(payload: dict) -> list[tuple[str, int, int]]:
    """(group name, team id, position) from ESPN standings: "2025 CONMEBOL Libertadores - Group A" -> "Group A"."""
    import re
    out = []
    for child in payload.get("children") or []:
        raw = child.get("name") or ""
        m = re.search(r"group\s+([A-Z0-9]+)\s*$", raw, re.I)
        name = f"Group {m.group(1).upper()}" if m else raw.strip() or "Table"
        for i, e in enumerate((child.get("standings") or {}).get("entries") or [], 1):
            tid = (e.get("team") or {}).get("id")
            if tid:
                out.append((name, int(tid), i))
    return out


def store_groups(engine: Engine, competition: str, season: int, prov: dict, issues: IssueLog, fetch=None) -> int:
    """Group membership for one season (cups and split-season leagues only; single tables need none)."""
    import requests
    try:
        payload = fetch() if fetch else requests.get(STANDINGS.format(code=competition), params={"season": season},
                                                     headers=HEADERS, timeout=25).json()
    except Exception as e:  # noqa: BLE001
        issues.add("soccer", f"standings:{competition}:{season}", "espn_fetch_failed", "warning", "skipped", error=str(e)[:200])
        return 0
    rows = parse_groups(payload)
    if len({r[0] for r in rows}) <= 1 and not any(r[0] == "League Phase" for r in rows):
        return 0
    with engine.begin() as conn:
        known = {r[0] for r in conn.execute(text("SELECT team_id FROM soccer_teams WHERE team_id = ANY(:i)"), {"i": [r[1] for r in rows]})}
        df = pl.DataFrame([{"competition": competition, "season": season, "team_id": t, "group_name": g, "position": p}
                           for g, t, p in rows if t in known],
                          schema={"competition": pl.String, "season": pl.Int64, "team_id": pl.Int64, "group_name": pl.String,
                                  "position": pl.Int64})
        if df.is_empty():
            return 0
        load.upsert(conn, sm.SoccerGroup.__table__, df, provenance=prov)
    return df.height


def _wants_groups(competition: str) -> bool:
    lg = BY_COMPETITION.get(competition)
    return bool(lg and (lg.kind == "cup" or lg.calendar_year))


def _have_stats(engine: Engine, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("SELECT game_id FROM soccer_games WHERE has_team_stats AND game_id = ANY(:i)"),
                                        {"i": ids})}


def ingest_espn(engine: Engine, seasons: list[int], competition: str = "eng.1", *, retries: int = 2,
                get: Getter | None = None, pause: float = 0.25) -> dict:
    """Backfill / re-sync whole seasons (fixtures, results, half-time scores, team stats)."""
    src = EspnSoccerSource(engine, competition, get=get, pause=pause)

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        per = {}
        for season in seasons:
            dates = src.season_dates(season, issues)
            games, teams = src.collect(dates, issues)
            games = [g for g in games if g["season"] == season]
            stats = src.enrich(games, _have_stats(engine, [g["game_id"] for g in games]), issues)
            prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": datetime.now(timezone.utc)}
            per[season] = {"dates": len(dates), "games": len(games), **_store(engine, src, games, teams, stats, prov)}
            if _wants_groups(competition):
                per[season]["groups"] = store_groups(engine, competition, season, prov, issues)
            log.info("season %s: %s", season, per[season])
        return {"seasons": per, "source": src.summary, "_source_updated_at": datetime.now(timezone.utc)}

    return tracked(engine, src.code, "soccer_espn_ingest", {"seasons": seasons, "competition": competition}, run,
                   retries=retries, backoff_seconds=30)


def _last_full_sync(engine: Engine, competition: str) -> datetime | None:
    with engine.connect() as c:
        return c.execute(text("""SELECT max(finished_at) FROM ingestion_runs WHERE job = 'soccer_espn_sync'
                                 AND status = 'succeeded' AND params @> CAST(:p AS jsonb)"""),
                         {"p": f'{{"competition": "{competition}", "mode": "full"}}'}).scalar()


def known_dates(engine: Engine, competition: str, start: date, end: date) -> list[date]:
    """Match dates (US-Eastern) in [start, end] with a match we haven't seen finish -- all a quick sync re-reads."""
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("""
            SELECT DISTINCT game_date FROM soccer_games
            WHERE competition = :c AND game_date BETWEEN :a AND :b AND status NOT IN ('final', 'canceled')
            ORDER BY 1"""), {"c": competition, "a": start, "b": end})]


def sync_espn(engine: Engine, competition: str = "eng.1", *, back_days: int = 10, ahead_days: int = 60,
              retries: int = 2, get: Getter | None = None, full: bool | None = None) -> dict:
    """Recent results (+ stats) and upcoming fixtures for match dates in [today-back, today+ahead].

    ``full`` walks every calendar date in the window (finds new fixtures, cup draws, rescheduled matches); a quick
    sync only re-reads today plus dates that already hold an unfinished match (results, kickoff changes) --
    ~5-15 calls instead of 20-70. ``None`` = full when the last full sync is older than ``FULL_EVERY``."""
    src = EspnSoccerSource(engine, competition, get=get)
    if full is None:
        last = _last_full_sync(engine, competition)
        full = last is None or datetime.now(timezone.utc) - last > FULL_EVERY

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        today = datetime.now(ET).date()
        lg = BY_COMPETITION.get(competition)
        season = lg.current_season(today) if lg else (today.year if today.month >= 7 else today.year - 1)
        start, end = today - timedelta(days=back_days), today + timedelta(days=ahead_days)
        if full:
            cal = src.season_dates(season, issues)
            window = [d for d in cal if start <= d <= end]
        else:
            window = sorted(set(known_dates(engine, competition, start, end)) | {today})
            cal = window
        games, teams = src.collect(window, issues)
        stats = src.enrich(games, _have_stats(engine, [g["game_id"] for g in games]), issues)
        if cal and not games and src.summary["failed"] >= 2:
            raise RuntimeError("ESPN soccer scoreboard unavailable")
        prov = {"source_id": source_id, "ingestion_run_id": run_id, "source_updated_at": datetime.now(timezone.utc)}
        out = {"mode": "full" if full else "quick", "window_dates": len(window), "source": src.summary, **_store(engine, src, games, teams, stats, prov)}
        if _wants_groups(competition) and games:
            out["groups"] = store_groups(engine, competition, season, prov, issues)
        return {**out, "_source_updated_at": datetime.now(timezone.utc)}

    return tracked(engine, src.code, "soccer_espn_sync", {"competition": competition, "mode": "full" if full else "quick"},
                   run, retries=retries, backoff_seconds=20)
