"""Combos: multi-leg picks built from our forecasts (Polymarket's "Combo", a sportsbook parlay). A combo wins only if
every leg wins.

Per sport, a safer and a riskier combo of 3, 4 and 5 legs (six "slots"), from upcoming games that haven't started:

* **Safer**: the model's strongest favorites, one leg per game.
* **Riskier**: legs where our probability is above the market's (NBA: Polymarket price, else sportsbook line;
  NFL: closing spread turned into a win probability). Soccer has no market price in our data, so its riskier combos
  use close calls the model still leans toward.

Legs come from different games and are treated as independent, so the combo chance is the product of the legs.
Payouts use market prices with the bookmaker margin removed (real payouts are lower), or our own odds where no market
price exists.

``refresh(conn)`` (run by every data job) keeps the ``combos`` table in step with the forecasts:

1. grade combos whose legs have started: each leg gets ``result`` won / lost / void (postponed or canceled) and the
   live score; a combo is lost as soon as one leg loses, won when every leg is settled and none lost;
2. rebuild each slot: an open combo (no leg started) is replaced with the fresh one; a started combo stays until it
   is lost or settled, then a new one is built for the slot.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from scipy.stats import norm
from sqlalchemy import Connection, text

from polypicker.api.deps import q
from polypicker.api.queries import headlines as nba_headlines
from polypicker.api.routers.nfl import headlines as nfl_headlines
from polypicker.api.routers.soccer import headlines as soccer_headlines
from polypicker.soccer.leagues import BY_COMPETITION

SPORTS = ("nba", "nfl", "soccer")
KINDS = ("safe", "risky")
SIZES = (3, 4, 5)
NFL_MARKET_SIGMA = 13.5     # same as the NFL model's market benchmark
SAFE_MIN = {"nba": 0.62, "nfl": 0.62, "soccer": 0.55}
SAFE_FLOOR = 0.55           # bigger safer combos may dip to this when there aren't enough strong favorites
EDGE_MIN = 0.03             # model above market by at least 3 points
RISKY_MAX_MARKET = 0.65     # riskier legs are underdogs, coin flips or modest favorites by the market (no heavy favorites)
GAME_TABLES = {"nba": ("games", "tipoff_utc"), "nfl": ("nfl_games", "kickoff_utc"), "soccer": ("soccer_games", "kickoff_utc")}


# ---------------------------------------------------------------- selection

def _leg(sport: str, g: dict, pick: str, p_model: float, p_market: float | None, source: str | None, league: str | None = None) -> dict:
    home, away = g["home"], g["away"]
    team = home if pick == "home" else away if pick == "away" else None
    href = (f"/soccer/{league}/games/{g['game_id']}" if sport == "soccer" else f"/{sport}/games/{g['game_id']}")
    tip = g["tipoff_utc"]
    return {"sport": sport, "league": league, "game_id": g["game_id"],
            "tipoff_utc": tip.isoformat() if isinstance(tip, datetime) else tip, "pick": pick,
            "team": team, "home": home, "away": away, "p_model": round(p_model, 4),
            "p_market": round(p_market, 4) if p_market is not None else None, "market_source": source, "href": href}


def _combo(kind: str, legs: list[dict], n: int) -> dict | None:
    if len(legs) < n:
        return None
    p_model = math.prod(x["p_model"] for x in legs)
    priced = all(x["p_market"] for x in legs)
    p_market = math.prod(x["p_market"] for x in legs) if priced else None
    return {"kind": kind, "n_legs": n, "legs": legs, "p_model": round(p_model, 4),
            "p_market": round(p_market, 4) if p_market else None,
            # what $1 returns if every leg wins: at market prices (margin removed) when we have them, else at our own odds
            "payout": round(1 / p_market, 2) if p_market else round(1 / p_model, 2),
            "payout_basis": "market" if p_market else "model",
            "edge": round(p_model / p_market - 1, 4) if p_market else None}


def _fill(pools: list[list[dict]], n: int, avoid: dict[int, str] | None = None) -> list[dict]:
    """First ``n`` legs from the pools in order, one per game. ``avoid`` maps game -> side already picked by the
    matching safer combo: those games are skipped on a first pass, then allowed (same side only) if legs run short."""
    avoid = avoid or {}
    out: list[dict] = []
    seen: set[int] = set()
    for strict in (True, False):
        for pool in pools:
            for c in pool:
                gid = c["game_id"]
                if gid in seen or (gid in avoid and (strict or avoid[gid] != c["pick"])):
                    continue
                out.append(c); seen.add(gid)
                if len(out) == n:
                    return out
        if not avoid:
            break
    return out


def _ladders(sport: str, safe_pools: list[list[dict]], risky_pools: list[list[dict]]) -> dict[tuple[str, int], dict | None]:
    out: dict[tuple[str, int], dict | None] = {}
    for n in SIZES:
        safe = _fill(safe_pools, n)
        out[("safe", n)] = _combo("safe", safe, n)
        out[("risky", n)] = _combo("risky", _fill(risky_pools, n, {x["game_id"]: x["pick"] for x in safe}), n)
    return out


def _two_way(sport: str, games: list[dict]) -> dict[tuple[str, int], dict | None]:
    """NBA / NFL: games carry p_home (model) and p_home_mkt (market, may be None)."""
    sides = []
    for g in games:
        for side in ("home", "away"):
            pm = g["p_home"] if side == "home" else 1 - g["p_home"]
            mk = g["p_home_mkt"] if side == "home" else (1 - g["p_home_mkt"] if g["p_home_mkt"] is not None else None)
            sides.append(_leg(sport, g, side, pm, mk, g["market_source"]))
    fav = sorted(sides, key=lambda s: -s["p_model"])
    safe_pools = [[s for s in fav if s["p_model"] >= SAFE_MIN[sport]], [s for s in fav if SAFE_FLOOR <= s["p_model"] < SAFE_MIN[sport]]]
    priced = [s for s in sides if s["p_market"] is not None and s["p_market"] <= RISKY_MAX_MARKET and s["p_model"] >= 0.30]
    by_edge = lambda pool: sorted(pool, key=lambda s: -(s["p_model"] - s["p_market"]))  # noqa: E731
    risky_pools = [
        by_edge([s for s in priced if s["p_model"] - s["p_market"] >= EDGE_MIN]),
        # fewer clear edges: smaller ones, but never a leg the market rates higher than we do
        by_edge([s for s in priced if 0 <= s["p_model"] - s["p_market"] < EDGE_MIN]),
        # games without a market price: the model's closest leans (labelled as model-only odds)
        [s for s in fav if s["p_market"] is None and 0.45 <= s["p_model"] < SAFE_MIN[sport]],
    ]
    return _ladders(sport, safe_pools, risky_pools)


def _upcoming(conn: Connection, sport: str, now: datetime, hours: int, extra: str = "") -> list[dict]:
    table, col = GAME_TABLES[sport]
    return q(conn, f"""SELECT game_id, {col} AS tipoff_utc, home_team_id, away_team_id {extra} FROM {table}
                      WHERE status = 'scheduled' AND {col} > :now AND {col} <= :until ORDER BY {col}""",
             now=now, until=now + timedelta(hours=hours))


def _nba(conn: Connection, now: datetime) -> dict:
    teams = {r["team_id"]: r for r in q(conn, "SELECT team_id, abbreviation, name, full_name FROM teams")}
    for hours in (36, 60, 96, 144):    # widen the window until there are enough games for two 5-leg combos
        rows = _upcoming(conn, "nba", now, hours)
        heads = nba_headlines(conn, [r["game_id"] for r in rows])
        rows = [r for r in rows if r["game_id"] in heads]
        if len(rows) >= 10:
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


def _nfl(conn: Connection, now: datetime) -> dict:
    teams = {r["team_id"]: r for r in q(conn, "SELECT team_id, abbreviation, name, full_name FROM nfl_teams")}
    rows = _upcoming(conn, "nfl", now, 24 * 8, ", market_spread_home")
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


def _soccer(conn: Connection, now: datetime) -> dict:
    """Across every competition we cover; three-way markets, so a leg can also be a draw."""
    teams = {r["team_id"]: r for r in q(conn, "SELECT team_id, name, short_name, abbreviation, color FROM soccer_teams")}
    for hours in (48, 72, 120, 168):
        rows = _upcoming(conn, "soccer", now, hours, ", competition")
        heads = soccer_headlines(conn, [r["game_id"] for r in rows])
        rows = [r for r in rows if r["game_id"] in heads and r["competition"] in BY_COMPETITION]
        if len(rows) >= 12:
            break
    t = lambda tid: {"team_id": tid, "abbr": teams[tid]["abbreviation"], "name": teams[tid]["name"],  # noqa: E731
                     "short": teams[tid]["short_name"] or teams[tid]["name"], "color": teams[tid]["color"]}
    legs = []
    for r in rows:
        h = heads[r["game_id"]]
        g = {"game_id": r["game_id"], "tipoff_utc": r["tipoff_utc"], "home": t(r["home_team_id"]), "away": t(r["away_team_id"])}
        legs.append(_leg("soccer", g, h["pick"], h["pick_probability"], None, None, BY_COMPETITION[r["competition"]].slug))
    fav = sorted((x for x in legs if x["pick"] != "draw"), key=lambda x: -x["p_model"])
    safe_pools = [[x for x in fav if x["p_model"] >= SAFE_MIN["soccer"]], [x for x in fav if 0.50 <= x["p_model"] < SAFE_MIN["soccer"]]]
    close = sorted((x for x in legs if 0.40 <= x["p_model"] < SAFE_MIN["soccer"]), key=lambda x: -x["p_model"])
    return _ladders("soccer", safe_pools, [close])


def build(conn: Connection, now: datetime | None = None) -> dict[str, dict[tuple[str, int], dict | None]]:
    """Fresh combos for every slot from the latest forecasts (nothing is written)."""
    now = now or datetime.now(timezone.utc)
    return {"nba": _nba(conn, now), "nfl": _nfl(conn, now), "soccer": _soccer(conn, now)}


# ---------------------------------------------------------------- grading and persistence

def _leg_result(sport: str, pick: str, g: dict) -> str | None:
    """won / lost / void, or None while the game isn't final. Soccer is graded on the 90-minute result."""
    if g["status"] in ("postponed", "canceled"):
        return "void"
    if g["status"] != "final" or g["home_score"] is None or g["away_score"] is None:
        return None
    h, a = g["home_score"], g["away_score"]
    if sport == "soccer" and g.get("home_score_90") is not None:
        h, a = g["home_score_90"], g["away_score_90"]
    winner = "home" if h > a else "away" if a > h else "draw"
    return "won" if winner == pick else "lost"


def _games(conn: Connection, sport: str, ids: list[int]) -> dict[int, dict]:
    table, _ = GAME_TABLES[sport]
    extra = ", home_score_90, away_score_90" if sport == "soccer" else ""
    return {r["game_id"]: r for r in q(conn, f"SELECT game_id, status, home_score, away_score {extra} FROM {table} WHERE game_id = ANY(:ids)", ids=ids)}


def combo_status(legs: list[dict], started: bool) -> tuple[str, bool]:
    """(status, settled) from the legs' results."""
    results = [x.get("result") for x in legs]
    settled = all(r is not None for r in results)
    if "lost" in results:
        return "lost", settled
    if settled:
        return ("won" if "won" in results else "void"), True
    return ("live" if started else "open"), False


def grade(conn: Connection, now: datetime) -> int:
    """Update leg results and scores of every combo that has started and isn't settled. Returns combos updated."""
    rows = q(conn, "SELECT id, sport, legs FROM combos WHERE settled_at IS NULL AND first_tipoff <= :now", now=now)
    for r in rows:
        games = _games(conn, r["sport"], [x["game_id"] for x in r["legs"]])
        legs = []
        for x in r["legs"]:
            g = games.get(x["game_id"])
            if g:
                x = {**x, "result": _leg_result(r["sport"], x["pick"], g), "game_status": g["status"],
                     "home_score": g["home_score"], "away_score": g["away_score"]}
            legs.append(x)
        status, settled = combo_status(legs, True)
        conn.execute(text("""UPDATE combos SET legs = CAST(:legs AS jsonb), status = :status, updated_at = now(),
                             settled_at = CASE WHEN :settled THEN coalesce(settled_at, now()) END WHERE id = :id"""),
                     {"legs": _json(legs), "status": status, "settled": settled, "id": r["id"]})
    return len(rows)


def _json(v) -> str:
    import json
    return json.dumps(v, default=str)


def refresh(conn: Connection, now: datetime | None = None) -> dict:
    """Grade started combos, then rebuild every slot (see the module docstring). Call inside a transaction."""
    now = now or datetime.now(timezone.utc)
    graded = grade(conn, now)
    fresh = build(conn, now)
    counts = {"graded": graded, "created": 0, "replaced": 0, "kept": 0, "removed": 0}
    for sport in SPORTS:
        for (kind, n), c in fresh[sport].items():
            cur = q(conn, """SELECT id, status, first_tipoff FROM combos WHERE sport = :s AND kind = :k AND n_legs = :n
                             AND status IN ('open', 'live') ORDER BY created_at DESC LIMIT 1""", s=sport, k=kind, n=n)
            cur = cur[0] if cur else None
            if cur and cur["first_tipoff"] <= now:
                counts["kept"] += 1          # in progress: frozen until it's lost or settled
                continue
            if c is None:
                if cur:
                    conn.execute(text("DELETE FROM combos WHERE id = :id"), {"id": cur["id"]})
                    counts["removed"] += 1
                continue
            tips = [x["tipoff_utc"] for x in c["legs"]]
            vals = {"sport": sport, "kind": kind, "n": n, "legs": _json(c["legs"]), "p_model": c["p_model"],
                    "p_market": c["p_market"], "payout": c["payout"], "basis": c["payout_basis"], "edge": c["edge"],
                    "first": min(tips), "last": max(tips)}
            if cur:
                conn.execute(text("""UPDATE combos SET legs = CAST(:legs AS jsonb), p_model = :p_model, p_market = :p_market,
                                     payout = :payout, payout_basis = :basis, edge = :edge, first_tipoff = :first,
                                     last_tipoff = :last, updated_at = now() WHERE id = :id"""), {**vals, "id": cur["id"]})
                counts["replaced"] += 1
            else:
                conn.execute(text("""INSERT INTO combos (sport, kind, n_legs, status, legs, p_model, p_market, payout,
                                     payout_basis, edge, first_tipoff, last_tipoff)
                                     VALUES (:sport, :kind, :n, 'open', CAST(:legs AS jsonb), :p_model, :p_market, :payout,
                                     :basis, :edge, :first, :last)"""), vals)
                counts["created"] += 1
    return counts
