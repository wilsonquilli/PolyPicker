"""Combos: multi-leg picks built from our forecasts (Polymarket's "Combo", a sportsbook parlay). A combo wins only if
every leg wins.

Per sport, two combos from upcoming games that haven't started:

* **Safer**: the model's strongest favorites, one leg per game.
* **Riskier**: legs where our probability is above the market's (NBA: Polymarket price, else sportsbook line;
  NFL: closing spread turned into a win probability). The payout is bigger and each leg carries a measured edge.
  Soccer has no market price in our data, so its riskier combo uses close calls the model still leans toward.

Legs come from different games and are treated as independent, so the combo chance is the product of the legs.
Payouts use market prices with the bookmaker margin removed; real payouts are lower. Informational only.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from scipy.stats import norm
from sqlalchemy import Connection

from polypicker.api.deps import cached, get_conn, q
from polypicker.api.queries import headlines as nba_headlines, meta
from polypicker.api.routers.nfl import headlines as nfl_headlines
from polypicker.api.routers.soccer import headlines as soccer_headlines
from polypicker.soccer.leagues import BY_COMPETITION

router = APIRouter(tags=["combos"])

NFL_MARKET_SIGMA = 13.5     # same as the NFL model's market benchmark
SAFE_MIN = {"nba": 0.62, "nfl": 0.62, "soccer": 0.55}
SAFE_LEGS, RISKY_LEGS, SOCCER_RISKY_LEGS = 3, 3, 4
EDGE_MIN = 0.03             # model above market by at least 3 points
RISKY_MAX_MARKET = 0.65     # riskier legs are underdogs, coin flips or modest favorites by the market (no heavy favorites)


def _leg(sport: str, g: dict, pick: str, p_model: float, p_market: float | None, source: str | None, league: str | None = None) -> dict:
    home, away = g["home"], g["away"]
    team = home if pick == "home" else away if pick == "away" else None
    href = (f"/soccer/{league}/games/{g['game_id']}" if sport == "soccer" else f"/{sport}/games/{g['game_id']}")
    return {"sport": sport, "league": league, "game_id": g["game_id"], "tipoff_utc": g["tipoff_utc"], "pick": pick,
            "team": team, "home": home, "away": away, "p_model": round(p_model, 4),
            "p_market": round(p_market, 4) if p_market is not None else None, "market_source": source, "href": href}


def _combo(kind: str, legs: list[dict]) -> dict | None:
    if len(legs) < 2:
        return None
    p_model = math.prod(x["p_model"] for x in legs)
    priced = all(x["p_market"] for x in legs)
    p_market = math.prod(x["p_market"] for x in legs) if priced else None
    return {"kind": kind, "legs": legs, "p_model": round(p_model, 4),
            "p_market": round(p_market, 4) if p_market else None,
            # what $1 returns if every leg wins: at market prices (margin removed) when we have them, else at our own odds
            "payout": round(1 / p_market, 2) if p_market else round(1 / p_model, 2),
            "payout_basis": "market" if p_market else "model",
            "edge": round(p_model / p_market - 1, 4) if p_market else None}


def _pick_distinct(cands: list[dict], n: int, avoid: set[int] = frozenset()) -> list[dict]:
    out, seen = [], set(avoid)
    for c in cands:
        if c["game_id"] in seen:
            continue
        out.append(c); seen.add(c["game_id"])
        if len(out) == n:
            break
    return out


def _two_way(sport: str, games: list[dict]) -> dict:
    """NBA / NFL: games carry p_home (model) and p_home_mkt (market, may be None)."""
    sides = []
    for g in games:
        for side in ("home", "away"):
            pm = g["p_home"] if side == "home" else 1 - g["p_home"]
            mk = g["p_home_mkt"] if side == "home" else (1 - g["p_home_mkt"] if g["p_home_mkt"] is not None else None)
            sides.append(_leg(sport, g, side, pm, mk, g["market_source"]))
    safe = _pick_distinct(sorted((s for s in sides if s["p_model"] >= SAFE_MIN[sport]), key=lambda s: -s["p_model"]), SAFE_LEGS)
    used = {x["game_id"] for x in safe}
    priced = [s for s in sides if s["p_market"] is not None and s["p_market"] <= RISKY_MAX_MARKET and s["p_model"] >= 0.30]
    by_edge = lambda pool: sorted(pool, key=lambda s: -(s["p_model"] - s["p_market"]))  # noqa: E731
    risky = _pick_distinct(by_edge([s for s in priced if s["p_model"] - s["p_market"] >= EDGE_MIN]), RISKY_LEGS, used)
    if len(risky) < 2:
        # Fewer clear edges today: allow smaller ones, but never a leg the market rates higher than we do.
        risky = _pick_distinct(by_edge([s for s in priced if s["p_model"] >= s["p_market"]]), RISKY_LEGS, used)
    if not any(s["p_market"] is not None for s in sides):
        # No market prices at all for this slate: the model's closest leans (labelled as model-only odds).
        risky = _pick_distinct(sorted((s for s in sides if 0.45 <= s["p_model"] < SAFE_MIN[sport]), key=lambda s: -s["p_model"]),
                               RISKY_LEGS, used)
    return {"safe": _combo("safe", safe), "risky": _combo("risky", risky)}


def _upcoming(conn: Connection, table: str, time_col: str, now: datetime, hours: int, extra: str = "") -> list[dict]:
    return q(conn, f"""SELECT game_id, {time_col} AS tipoff_utc, home_team_id, away_team_id {extra} FROM {table}
                      WHERE status = 'scheduled' AND {time_col} > :now AND {time_col} <= :until ORDER BY {time_col}""",
             now=now, until=now + timedelta(hours=hours))


def nba(conn: Connection, now: datetime) -> dict:
    teams = {r["team_id"]: r for r in q(conn, "SELECT team_id, abbreviation, name, full_name FROM teams")}
    for hours in (36, 96):
        rows = _upcoming(conn, "games", "tipoff_utc", now, hours)
        heads = nba_headlines(conn, [r["game_id"] for r in rows])
        rows = [r for r in rows if r["game_id"] in heads]
        if len(rows) >= 4:
            break
    ids = [r["game_id"] for r in rows]
    pm = {r["game_id"]: r["p"] for r in q(conn, """SELECT game_id, coalesce(p_home_live, p_home_pregame) p FROM polymarket_nba
                                                  WHERE game_id = ANY(:ids)""", ids=ids) if r["p"] is not None}
    book = {r["game_id"]: r["p_home"] for r in q(conn, """SELECT DISTINCT ON (game_id) game_id, p_home FROM nba_odds
                                                         WHERE game_id = ANY(:ids) AND p_home IS NOT NULL
                                                         ORDER BY game_id, fetched_at DESC""", ids=ids)}
    t = lambda tid: {"team_id": tid, "abbr": teams[tid]["abbreviation"], "name": teams[tid]["full_name"], "short": teams[tid]["name"]}  # noqa: E731
    games = [{"game_id": r["game_id"], "tipoff_utc": r["tipoff_utc"], "home": t(r["home_team_id"]), "away": t(r["away_team_id"]),
              "p_home": heads[r["game_id"]]["p_home_win"],
              "p_home_mkt": pm.get(r["game_id"], book.get(r["game_id"])),
              "market_source": "Polymarket" if r["game_id"] in pm else "Sportsbook" if r["game_id"] in book else None}
             for r in rows]
    return _two_way("nba", games)


def nfl(conn: Connection, now: datetime) -> dict:
    teams = {r["team_id"]: r for r in q(conn, "SELECT team_id, abbreviation, name, full_name FROM nfl_teams")}
    rows = _upcoming(conn, "nfl_games", "kickoff_utc", now, 24 * 8, ", market_spread_home")
    heads = nfl_headlines(conn, [r["game_id"] for r in rows])
    t = lambda tid: {"team_id": tid, "abbr": teams[tid]["abbreviation"], "name": teams[tid]["full_name"], "short": teams[tid]["name"]}  # noqa: E731
    games = []
    for r in rows:
        h = heads.get(r["game_id"])
        if not h:
            continue
        spread = r["market_spread_home"]
        games.append({"game_id": r["game_id"], "tipoff_utc": r["tipoff_utc"], "home": t(r["home_team_id"]), "away": t(r["away_team_id"]),
                      "p_home": h["p_home_win"],
                      "p_home_mkt": float(norm.sf(0, spread, NFL_MARKET_SIGMA)) if spread is not None else None,
                      "market_source": "Betting spread" if spread is not None else None})
    return _two_way("nfl", games)


def soccer(conn: Connection, now: datetime) -> dict:
    """Across every competition we cover; three-way markets, so a leg can also be a draw."""
    teams = {r["team_id"]: r for r in q(conn, "SELECT team_id, name, short_name, abbreviation, color FROM soccer_teams")}
    for hours in (48, 120):
        rows = _upcoming(conn, "soccer_games", "kickoff_utc", now, hours, ", competition")
        heads = soccer_headlines(conn, [r["game_id"] for r in rows])
        rows = [r for r in rows if r["game_id"] in heads and r["competition"] in BY_COMPETITION]
        if len(rows) >= 6:
            break
    t = lambda tid: {"team_id": tid, "abbr": teams[tid]["abbreviation"], "name": teams[tid]["name"],  # noqa: E731
                     "short": teams[tid]["short_name"] or teams[tid]["name"], "color": teams[tid]["color"]}
    legs = []
    for r in rows:
        h = heads[r["game_id"]]
        g = {"game_id": r["game_id"], "tipoff_utc": r["tipoff_utc"], "home": t(r["home_team_id"]), "away": t(r["away_team_id"])}
        lg = BY_COMPETITION[r["competition"]].slug
        legs.append(_leg("soccer", g, h["pick"], h["pick_probability"], None, None, lg))
    wins = [x for x in legs if x["pick"] != "draw"]
    safe = _pick_distinct(sorted((x for x in wins if x["p_model"] >= SAFE_MIN["soccer"]), key=lambda x: -x["p_model"]), SAFE_LEGS)
    used = {x["game_id"] for x in safe}
    close = sorted((x for x in legs if 0.40 <= x["p_model"] < SAFE_MIN["soccer"]), key=lambda x: -x["p_model"])
    return {"safe": _combo("safe", safe), "risky": _combo("risky", _pick_distinct(close, SOCCER_RISKY_LEGS, used))}


@router.get("/combos")
@cached(ttl=60)
def combos(conn: Connection = Depends(get_conn)):
    """Safer and riskier combo per sport (NBA, NFL, soccer) from upcoming games' latest forecasts."""
    now = datetime.now(timezone.utc)
    return {"data": {"nba": nba(conn, now), "nfl": nfl(conn, now), "soccer": soccer(conn, now)},
            "meta": meta(note="Legs are from different games and treated as independent. Payouts use market prices "
                              "with the bookmaker margin removed (real payouts are lower), or our own odds where no "
                              "market price exists. Informational only; not betting advice.")}
