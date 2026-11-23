"""Second and third sources, matched onto ESPN matches by date + teams:

* openfootball (football.json, CC0): independent result history -> every final ESPN result is checked
  (``of_verified``); disagreements are logged; a result is filled in only if ESPN has none.
* football-data.org (official API, free key): current-season backup -> stores its match id, fills a
  missing result, logs disagreements. Skipped (not an error) when no API key is configured.

Team names differ by source ("Manchester City FC" / "Manchester City" / "Man City"), so they are
normalized and matched with a small alias table.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Engine, text

from polypicker.config import get_settings
from polypicker.etl.issues import IssueLog
from polypicker.nfl.etl.runner import tracked
from polypicker.soccer.leagues import LEAGUES, League

log = logging.getLogger(__name__)
OF_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{code}.json"
FD_URL = "https://api.football-data.org/v4/competitions/{code}/matches"

ALIASES = {"man city": "manchester city", "man utd": "manchester united", "man united": "manchester united",
           "spurs": "tottenham hotspur", "tottenham": "tottenham hotspur", "wolves": "wolverhampton wanderers",
           "wolverhampton": "wolverhampton wanderers", "brighton": "brighton and hove albion",
           "brighton hove albion": "brighton and hove albion", "west brom": "west bromwich albion",
           "qpr": "queens park rangers", "newcastle": "newcastle united", "west ham": "west ham united",
           "leeds": "leeds united", "leicester": "leicester city", "norwich": "norwich city", "stoke": "stoke city",
           "swansea": "swansea city", "hull": "hull city", "cardiff": "cardiff city", "huddersfield": "huddersfield town",
           "nottm forest": "nottingham forest", "nott'm forest": "nottingham forest", "sheffield utd": "sheffield united",
           "bournemouth": "afc bournemouth", "ipswich": "ipswich town", "luton": "luton town", "coventry": "coventry city",
           "birmingham": "birmingham city", "blackburn": "blackburn rovers", "bolton": "bolton wanderers", "wigan": "wigan athletic",
           "sunderland": "sunderland", "watford": "watford", "middlesbrough": "middlesbrough", "reading": "reading",
           "burnley": "burnley", "fulham": "fulham", "everton": "everton", "chelsea": "chelsea", "arsenal": "arsenal",
           "liverpool": "liverpool", "southampton": "southampton", "brentford": "brentford", "blackpool": "blackpool",
           "crystal palace": "crystal palace", "aston villa": "aston villa"}


# Spanish clubs: openfootball uses full legal names ("RC Celta de Vigo", "Real Betis Balompié"), ESPN short ones.
# Club-type tokens (CF, CD, UD, SD, RCD, RC, CA, SAD) are dropped first, then these map what's left.
ALIASES_ES = {"club atletico de madrid": "atletico madrid", "atletico de madrid": "atletico madrid",
              "deportivo alaves": "alaves", "celta de vigo": "celta vigo", "celta": "celta vigo",
              "espanyol de barcelona": "espanyol", "espanyol barcelona": "espanyol",
              "rayo vallecano de madrid": "rayo vallecano", "real betis balompie": "real betis", "betis": "real betis",
              "real sociedad de futbol": "real sociedad", "real racing club de santander": "racing santander",
              "racing de santander": "racing santander", "racing club de santander": "racing santander",
              "deportivo": "deportivo la coruna", "deportivo de la coruna": "deportivo la coruna",
              "real sporting de gijon": "sporting gijon", "sporting de gijon": "sporting gijon",
              "athletic bilbao": "athletic club", "real club deportivo mallorca": "mallorca",
              "hercules": "hercules", "xerez": "xerez", "real murcia": "real murcia"}
ES_TOKENS = r"\b(cf|cd|ud|sd|rcd|rc|ca|sad)\b"
# Italian: AC / ACF / AS / BC / CFC / SS / SSC / UC / US / Calcio and founding years ("Bologna FC 1909").
IT_TOKENS = r"\b(ac|acf|as|bc|cfc|ss|ssc|uc|us|calcio|1[89]\d\d|20[0-2]\d)\b"
ALIASES_IT = {"inter": "internazionale", "internazionale milano": "internazionale", "inter milan": "internazionale",
              "lazio roma": "lazio", "delfino pescara": "pescara", "spal ferrara": "spal", "hellas verona": "verona",
              "chievo verona": "chievo", "ac chievo verona": "chievo"}
# German / French: openfootball full names -> ESPN's (often English or city-only) names; both spellings map here.
ALIASES_DE_FR = {
    "1 fsv mainz 05": "mainz", "mainz 05": "mainz", "fsv mainz 05": "mainz", "1 koln": "cologne", "koln": "cologne",
    "bayer 04 leverkusen": "bayer leverkusen", "leverkusen": "bayer leverkusen", "bayern munchen": "bayern munich",
    "bor monchengladbach": "borussia monchengladbach", "monchengladbach": "borussia monchengladbach",
    "bor dortmund": "borussia dortmund", "eintracht braunschweig": "tsv eintracht braunschweig", "hamburger sv": "hamburg sv",
    "hertha bsc": "hertha berlin", "hoffenheim": "tsg hoffenheim", "sv 07 elversberg": "sv elversberg",
    "sv werder bremen": "werder bremen", "1 kaiserslautern": "kaiserslautern", "schalke": "schalke 04",
    "amiens sc": "amiens", "angers sco": "angers", "clermont foot 63": "clermont", "clermont foot": "clermont",
    "ea guingamp": "guingamp", "en avant guingamp": "guingamp", "es troyes": "troyes", "estac troyes": "troyes",
    "girondins bordeaux": "bordeaux", "girondins de bordeaux": "bordeaux", "le havre ac": "le havre", "lille osc": "lille",
    "losc lille": "lille", "montpellier hsc": "montpellier", "nimes olympique": "nimes", "ogc nice": "nice",
    "olympique de marseille": "marseille", "olympique marseille": "marseille", "olympique lyonnais": "lyon",
    "racing club de lens": "lens", "rc lens": "lens", "sc bastia": "bastia", "sm caen": "caen", "stade brestois 29": "brest",
    "stade brestois": "brest", "stade de reims": "reims", "strasbourg alsace": "strasbourg", "rc strasbourg alsace": "strasbourg",
    "rennes": "stade rennais", "gfc ajaccio": "gazelec ajaccio", "paris sg": "paris saint germain", "psg": "paris saint germain",
    "dijon fco": "dijon", "aj auxerre": "auxerre", "le mans ufc": "le mans"}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace(".", " ").replace("-", " ")
    s = re.sub(r"\b(fc|afc)\b", " ", s) if not s.strip().startswith("afc bournemouth") else s
    s = re.sub(ES_TOKENS, " ", s)
    s = re.sub(IT_TOKENS, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = ALIASES_ES.get(s, s)
    s = ALIASES_IT.get(s, s)
    s = ALIASES_DE_FR.get(s, s)
    s = ALIASES.get(s, s)
    return "afc bournemouth" if s in ("bournemouth", "afc bournemouth") else s


def _espn_index(engine: Engine, season: int, competition: str = "eng.1") -> tuple[dict, dict]:
    with engine.connect() as c:
        teams = {r.team_id: norm(r.name) for r in c.execute(text("SELECT team_id, name FROM soccer_teams"))}
        games = [dict(r._mapping) for r in c.execute(text("""
            SELECT game_id, game_date, home_team_id, away_team_id, status, home_score, away_score, kickoff_utc
            FROM soccer_games WHERE competition = :c AND season = :s"""), {"c": competition, "s": season})]
    idx = {}
    for g in games:
        idx[(teams[g["home_team_id"]], teams[g["away_team_id"]])] = idx.get((teams[g["home_team_id"]], teams[g["away_team_id"]]), []) + [g]
    return idx, teams


def _find(idx: dict, home: str, away: str, d: date | None) -> dict | None:
    cands = idx.get((home, away)) or []
    if len(cands) == 1:
        return cands[0]
    if d:
        near = [g for g in cands if abs((g["game_date"] - d).days) <= 3]
        return near[0] if len(near) == 1 else None
    return None


def _fill_result(conn, g: dict, hs: int, as_: int, source_id: int, run_id: int) -> None:
    res = "H" if hs > as_ else "A" if as_ > hs else "D"
    conn.execute(text("""UPDATE soccer_games SET status = 'final', home_score = :h, away_score = :a, result = :r,
                         source_id = :sid, ingestion_run_id = :rid, updated_at = now()
                         WHERE game_id = :g AND status <> 'final'"""),
                 {"h": hs, "a": as_, "r": res, "sid": source_id, "rid": run_id, "g": g["game_id"]})


# ---------------------------------------------------------------- openfootball

def _of_matches(payload: dict) -> list[tuple[str, str, date | None, int | None, int | None]]:
    out = []
    for m in payload.get("matches") or [x for r in payload.get("rounds") or [] for x in r.get("matches") or []]:
        home, away = m.get("team1"), m.get("team2")
        if isinstance(home, dict):
            home, away = home.get("name"), away.get("name")
        sc = m.get("score") or {}
        ft = sc.get("ft") if isinstance(sc, dict) else None
        hs, as_ = (ft[0], ft[1]) if ft else (m.get("score1"), m.get("score2"))
        d = date.fromisoformat(m["date"]) if m.get("date") else None
        out.append((norm(home), norm(away), d, hs, as_))
    return out


def crosscheck_openfootball(engine: Engine, seasons: list[int], league: League = LEAGUES["epl"], *, fetch=None) -> dict:
    if not league.openfootball and fetch is None:
        return {"skipped": f"openfootball has no {league.name} file"}
    import requests
    fetch = fetch or (lambda season: requests.get(OF_URL.format(season=f"{season}-{str(season + 1)[2:]}", code=league.openfootball), timeout=30,
                                                  headers={"User-Agent": "PolyPicker/1.0"}).json())

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        per = {}
        for season in seasons:
            try:
                ms = _of_matches(fetch(season))
            except Exception as e:  # noqa: BLE001
                issues.add("soccer_crosscheck", season, "openfootball_fetch_failed", "warning", "skipped", error=str(e)[:200])
                continue
            idx, _ = _espn_index(engine, season, league.competition)
            st = {"openfootball_matches": len(ms), "matched": 0, "agree": 0, "disagree": 0, "unmatched": 0, "filled": 0}
            with engine.begin() as conn:
                for home, away, d, hs, as_ in ms:
                    g = _find(idx, home, away, d)
                    if g is None:
                        st["unmatched"] += 1
                        issues.add("soccer_crosscheck", f"{season}:{home} v {away}", "openfootball_unmatched", "info", "skipped")
                        continue
                    st["matched"] += 1
                    if hs is None:
                        continue
                    if g["status"] == "final":
                        ok = (g["home_score"], g["away_score"]) == (hs, as_)
                        st["agree" if ok else "disagree"] += 1
                        conn.execute(text("UPDATE soccer_games SET of_verified = :v WHERE game_id = :g"), {"v": ok, "g": g["game_id"]})
                        if not ok:
                            issues.add("soccer_game", g["game_id"], "result_disagrees_with_openfootball", "warning", "flagged",
                                       espn=f"{g['home_score']}-{g['away_score']}", openfootball=f"{hs}-{as_}")
                    elif g["kickoff_utc"] < datetime.now(timezone.utc) - timedelta(hours=4):
                        _fill_result(conn, g, hs, as_, source_id, run_id)
                        st["filled"] += 1
            per[season] = st
        return {"seasons": per}

    return tracked(engine, "openfootball", "soccer_openfootball_check", {"seasons": seasons, "competition": league.competition}, run, retries=1)


# ---------------------------------------------------------------- football-data.org

def sync_football_data(engine: Engine, season: int, league: League = LEAGUES["epl"], *, api_key: str | None = None, fetch=None) -> dict:
    if not league.football_data and fetch is None:
        return {"skipped": f"{league.name} isn't in football-data.org's free tier"}
    key = api_key or get_settings().football_data_api_key
    if not key and fetch is None:
        return {"skipped": "no football-data.org API key (set FOOTBALL_DATA_API_KEY in backend/.env)"}
    import requests
    fetch = fetch or (lambda: requests.get(FD_URL.format(code=league.football_data), params={"season": season}, headers={"X-Auth-Token": key}, timeout=30).json())

    def run(run_id: int, source_id: int, issues: IssueLog) -> dict:
        payload = fetch()
        if "matches" not in payload:
            raise RuntimeError(f"football-data.org: {payload.get('message') or payload}")
        idx, _ = _espn_index(engine, season, league.competition)
        st = {"fd_matches": len(payload["matches"]), "matched": 0, "unmatched": 0, "agree": 0, "disagree": 0, "filled": 0}
        with engine.begin() as conn:
            for m in payload["matches"]:
                home, away = norm(m["homeTeam"].get("name") or ""), norm(m["awayTeam"].get("name") or "")
                d = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).date() if m.get("utcDate") else None
                g = _find(idx, home, away, d)
                if g is None:
                    st["unmatched"] += 1
                    issues.add("soccer_crosscheck", f"fd:{m.get('id')}", "football_data_unmatched", "info", "skipped",
                               teams=f"{home} v {away}")
                    continue
                st["matched"] += 1
                conn.execute(text("UPDATE soccer_games SET fd_match_id = :f WHERE game_id = :g"), {"f": m["id"], "g": g["game_id"]})
                ft = (m.get("score") or {}).get("fullTime") or {}
                hs, as_ = ft.get("home"), ft.get("away")
                if m.get("status") != "FINISHED" or hs is None:
                    continue
                if g["status"] == "final":
                    ok = (g["home_score"], g["away_score"]) == (hs, as_)
                    st["agree" if ok else "disagree"] += 1
                    if not ok:
                        issues.add("soccer_game", g["game_id"], "result_disagrees_with_football_data", "warning", "flagged",
                                   espn=f"{g['home_score']}-{g['away_score']}", football_data=f"{hs}-{as_}")
                else:
                    _fill_result(conn, g, hs, as_, source_id, run_id)
                    st["filled"] += 1
        return st

    return tracked(engine, "football_data_org", "soccer_football_data_sync", {"season": season, "competition": league.competition}, run, retries=2,
                   backoff_seconds=15)
