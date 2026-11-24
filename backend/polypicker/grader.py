"""Combo grader: a user builds a combo (parlay) from games we forecast and we grade it.

A leg is one game's outcome: ``{sport}-{game_id}-{pick}``, e.g. ``nba-22600123-home`` or ``soccer-704512-draw``.
Only game outcomes (moneyline / 1X2) are gradeable in v1, one leg per game (same-game legs are correlated, so their
chances can't simply be multiplied), across NBA, NFL and every soccer competition we cover.

For a combo we report:

* **chance**: the product of our leg probabilities (legs are different games, treated as independent). Once games
  finish, won legs count as certain and a lost leg makes the combo dead;
* **fair odds**: American odds that would be break-even at our chance;
* **value**: with the user's book odds, the expected return per $1 (EV = chance x decimal odds - 1); without them,
  our chance against the market's (margin-free) price for the same legs;
* **grade**: a letter from the chance, moved one step up for clear value and one down for a clearly bad price;
* **weakest leg**, what the chance would be without it, and a stronger replacement from the current safer combos.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from scipy.stats import norm
from sqlalchemy import Connection

from polypicker.api.deps import q
from polypicker.api.queries import headlines as nba_headlines
from polypicker.api.routers.nfl import headlines as nfl_headlines
from polypicker.api.routers.soccer import headlines as soccer_headlines
from polypicker.combos import NFL_MARKET_SIGMA, _leg_result, combo_status
from polypicker.soccer.leagues import BY_COMPETITION

MAX_LEGS = 12
PICKS = {"nba": ("home", "away"), "nfl": ("home", "away"), "soccer": ("home", "draw", "away")}
# chance tiers -> letter (checked top-down); +/- inside a tier, see _letter
TIERS = [(0.50, "A"), (0.35, "B"), (0.20, "C"), (0.10, "D"), (0.0, "F")]
LETTERS = "FDCBA"
VALUE_UP = 0.05       # EV / edge at least +5%: one step up
VALUE_DOWN = -0.20    # EV / edge -20% or worse: one step down (a typical parlay's built-in margin is ~-10% to -25%)
SWAP_MIN_GAIN = 0.05  # a replacement leg must be at least 5 points likelier than the weakest leg


class LegError(ValueError):
    """A leg the grader can't use (bad format, unknown game, no forecast, ...). Message is user-facing."""


# ---------------------------------------------------------------- parsing and pure maths

def parse_legs(raw: str) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int]] = set()
    for tok in (x.strip() for x in raw.split(",")):
        if not tok:
            continue
        parts = tok.split("-")
        if len(parts) != 3 or parts[0] not in PICKS or not parts[1].isdigit():
            raise LegError(f"Invalid leg '{tok}'. Use sport-gameid-pick, e.g. nba-123-home.")
        sport, gid, pick = parts[0], int(parts[1]), parts[2]
        if pick not in PICKS[sport]:
            raise LegError(f"'{pick}' isn't a valid pick for {sport.upper()} (use {', '.join(PICKS[sport])}).")
        if (sport, gid) in seen:
            raise LegError("Only one pick per game: same-game legs move together, so their chances can't be multiplied.")
        seen.add((sport, gid))
        out.append((sport, gid, pick))
    if not out:
        raise LegError("Add at least one leg.")
    if len(out) > MAX_LEGS:
        raise LegError(f"At most {MAX_LEGS} legs.")
    return out


def parse_american(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    s = raw.strip().replace(" ", "")
    try:
        v = int(s.lstrip("+"))
    except ValueError:
        raise LegError("Odds must be American odds like +650 or -120.") from None
    if abs(v) < 100:
        raise LegError("American odds are +100 or more, or -100 or less.")
    return v


def american_to_decimal(a: int) -> float:
    return 1 + (a / 100 if a > 0 else 100 / -a)


def american_from_p(p: float) -> int | None:
    """Break-even American odds for probability ``p`` (None at 0 or 1)."""
    if p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def _letter(chance: float, value: float | None) -> str:
    base = next(i for i, (lo, _) in enumerate(TIERS) if chance >= lo)
    idx = LETTERS.index(TIERS[base][1])
    if value is not None:
        idx += 1 if value >= VALUE_UP else -1 if value <= VALUE_DOWN else 0
    idx = max(0, min(len(LETTERS) - 1, idx))
    letter = LETTERS[idx]
    if letter == "F":
        return "F"
    # +/- by where the chance sits in its own tier (A tops out at 70%+)
    lo = TIERS[base][0]
    hi = 0.70 if base == 0 else TIERS[base - 1][0]
    pos = min(1.0, (chance - lo) / (hi - lo))
    return letter + ("+" if pos >= 2 / 3 else "-" if pos < 1 / 3 else "")


def score(legs: list[dict], odds: int | None) -> dict:
    """Combo maths from graded legs (each with p_model, p_market or None, result or None)."""
    live = [x for x in legs if x["result"] != "void"]
    p_pre = math.prod(x["p_model"] for x in live) if live else 1.0
    dead = any(x["result"] == "lost" for x in legs)
    p_now = 0.0 if dead else math.prod(1.0 if x["result"] == "won" else x["p_model"] for x in live)
    priced = live and all(x["p_market"] is not None for x in live)
    p_mkt = math.prod(x["p_market"] for x in live) if priced else None

    decimal = american_to_decimal(odds) if odds is not None else None
    ev = p_pre * decimal - 1 if decimal else None
    edge = p_pre / p_mkt - 1 if p_mkt else None
    value, basis = (ev, "odds") if ev is not None else (edge, "market") if edge is not None else (None, None)
    verdict = None
    if value is not None:
        verdict = "good" if value >= VALUE_UP else "bad" if value <= VALUE_DOWN else "fair"

    open_legs = [x for x in live if x["result"] is None]
    weakest = min(open_legs, key=lambda x: x["p_model"]) if len(live) >= 2 and open_legs else None
    return {
        "chance": round(p_now, 6), "chance_pregame": round(p_pre, 6), "dead": dead,
        "p_market": round(p_mkt, 6) if p_mkt is not None else None,
        "fair_odds": american_from_p(p_pre), "market_odds": american_from_p(p_mkt) if p_mkt else None,
        "book_odds": odds, "book_implied": round(1 / decimal, 4) if decimal else None,
        "ev": round(ev, 4) if ev is not None else None, "edge": round(edge, 4) if edge is not None else None,
        "value_basis": basis, "verdict": verdict,
        # $1 returns: at the user's odds if given, else at the market's margin-free price, else at our fair odds
        "payout": round(decimal if decimal else (1 / p_mkt if p_mkt else 1 / p_pre), 2) if p_pre > 0 else None,
        "payout_basis": "odds" if decimal else "market" if p_mkt else "model",
        "grade": "F" if dead else _letter(p_pre, value),
        "weakest": ({"key": weakest["key"], "p_model": weakest["p_model"],
                     "chance_without": round(p_pre / weakest["p_model"], 6)} if weakest else None),
    }


# ---------------------------------------------------------------- lookups

def _teams(conn: Connection, table: str, soccer: bool = False) -> dict[int, dict]:
    cols = "team_id, name, short_name, abbreviation, color" if soccer else "team_id, abbreviation, name, full_name"
    out = {}
    for r in q(conn, f"SELECT {cols} FROM {table}"):
        if soccer:
            out[r["team_id"]] = {"team_id": r["team_id"], "abbr": r["abbreviation"], "name": r["name"],
                                 "short": r["short_name"] or r["name"], "color": r["color"]}
        else:
            out[r["team_id"]] = {"team_id": r["team_id"], "abbr": r["abbreviation"], "name": r["full_name"], "short": r["name"]}
    return out


def _probs_nba(conn: Connection, ids: list[int]) -> dict[int, dict]:
    games = q(conn, """SELECT game_id, tipoff_utc, home_team_id, away_team_id, status, home_score, away_score
                       FROM games WHERE game_id = ANY(:ids)""", ids=ids)
    heads = nba_headlines(conn, ids)
    pm = {r["game_id"]: r["p"] for r in q(conn, """SELECT game_id, coalesce(p_home_live, p_home_pregame) p FROM polymarket_nba
                                                  WHERE game_id = ANY(:ids)""", ids=ids) if r["p"] is not None}
    book = {r["game_id"]: r["p_home"] for r in q(conn, """SELECT DISTINCT ON (game_id) game_id, p_home FROM nba_odds
                                                         WHERE game_id = ANY(:ids) AND p_home IS NOT NULL
                                                         ORDER BY game_id, fetched_at DESC""", ids=ids)}
    out = {}
    for g in games:
        h = heads.get(g["game_id"])
        if not h:
            continue
        mk = pm.get(g["game_id"], book.get(g["game_id"]))
        out[g["game_id"]] = {**g, "league": None, "p": {"home": h["p_home_win"], "away": 1 - h["p_home_win"]},
                             "mkt": {"home": mk, "away": 1 - mk} if mk is not None else None,
                             "market_source": "Polymarket" if g["game_id"] in pm else "Sportsbook" if mk is not None else None}
    return out


def _probs_nfl(conn: Connection, ids: list[int]) -> dict[int, dict]:
    games = q(conn, """SELECT game_id, kickoff_utc AS tipoff_utc, home_team_id, away_team_id, status, home_score, away_score,
                              market_spread_home FROM nfl_games WHERE game_id = ANY(:ids)""", ids=ids)
    heads = nfl_headlines(conn, ids)
    out = {}
    for g in games:
        h = heads.get(g["game_id"])
        if not h:
            continue
        s = g.pop("market_spread_home")
        mk = float(norm.sf(0, s, NFL_MARKET_SIGMA)) if s is not None else None
        out[g["game_id"]] = {**g, "league": None, "p": {"home": h["p_home_win"], "away": 1 - h["p_home_win"]},
                             "mkt": {"home": mk, "away": 1 - mk} if mk is not None else None,
                             "market_source": "Betting spread" if mk is not None else None}
    return out


def _probs_soccer(conn: Connection, ids: list[int]) -> dict[int, dict]:
    games = q(conn, """SELECT game_id, kickoff_utc AS tipoff_utc, home_team_id, away_team_id, status, home_score, away_score,
                              home_score_90, away_score_90, competition FROM soccer_games WHERE game_id = ANY(:ids)""", ids=ids)
    heads = soccer_headlines(conn, ids)
    odds = {r["game_id"]: r for r in q(conn, "SELECT game_id, p_home, p_draw, p_away FROM soccer_odds WHERE game_id = ANY(:ids)", ids=ids)}
    out = {}
    for g in games:
        h = heads.get(g["game_id"])
        lg = BY_COMPETITION.get(g["competition"])
        if not h or not lg or "p_home" not in h:
            continue
        o = odds.get(g["game_id"])
        out[g["game_id"]] = {**g, "league": lg.slug,
                             "p": {"home": h["p_home"], "draw": h.get("p_draw", 0.0), "away": h.get("p_away", 0.0)},
                             "mkt": {"home": o["p_home"], "draw": o["p_draw"], "away": o["p_away"]} if o else None,
                             "market_source": "Sportsbooks" if o else None}
    return out


PROBS = {"nba": _probs_nba, "nfl": _probs_nfl, "soccer": _probs_soccer}
TEAMS = {"nba": ("teams", False), "nfl": ("nfl_teams", False), "soccer": ("soccer_teams", True)}


def lookup(conn: Connection, legs: list[tuple[str, int, str]]) -> tuple[list[dict], list[dict]]:
    """(legs we could price, legs we couldn't with a reason). Each priced leg carries display info and its result."""
    by_sport: dict[str, list[int]] = {}
    for sport, gid, _ in legs:
        by_sport.setdefault(sport, []).append(gid)
    games = {s: PROBS[s](conn, ids) for s, ids in by_sport.items()}
    teams = {s: _teams(conn, TEAMS[s][0], TEAMS[s][1]) for s in by_sport}
    ok, bad = [], []
    for sport, gid, pick in legs:
        key = f"{sport}-{gid}-{pick}"
        g = games[sport].get(gid)
        if not g:
            bad.append({"key": key, "reason": "not_covered"})
            continue
        home, away = teams[sport][g["home_team_id"]], teams[sport][g["away_team_id"]]
        result = _leg_result(sport, pick, g)
        tip = g["tipoff_utc"]
        ok.append({
            "key": key, "sport": sport, "league": g["league"], "game_id": gid, "pick": pick,
            "team": home if pick == "home" else away if pick == "away" else None, "home": home, "away": away,
            "tipoff_utc": tip.isoformat() if isinstance(tip, datetime) else tip, "game_status": g["status"],
            "home_score": g["home_score"], "away_score": g["away_score"], "result": result,
            "p_model": round(g["p"][pick], 4),
            "p_market": round(g["mkt"][pick], 4) if g["mkt"] and g["mkt"].get(pick) is not None else None,
            "market_source": g["market_source"],
            "href": f"/soccer/{g['league']}/games/{gid}" if sport == "soccer" else f"/{sport}/games/{gid}",
        })
    return ok, bad


def _swap(conn: Connection, legs: list[dict], weakest: dict) -> dict | None:
    """A likelier leg from the current safer combos (same sport first) for a game not already in the combo."""
    taken = {(x["sport"], x["game_id"]) for x in legs}
    rows = q(conn, "SELECT sport, legs FROM combos WHERE kind = 'safe' AND status = 'open'")
    cands: list[tuple[str, int, str]] = []
    for r in rows:
        for x in r["legs"]:
            k = (r["sport"], x["game_id"], x["pick"])
            if (k[0], k[1]) not in taken and k not in cands:
                cands.append(k)
    if not cands:
        return None
    priced, _ = lookup(conn, cands)
    now = datetime.now(timezone.utc).isoformat()
    w = next(x for x in legs if x["key"] == weakest["key"])
    pool = [x for x in priced if x["game_status"] == "scheduled" and x["tipoff_utc"] > now
            and x["p_model"] >= w["p_model"] + SWAP_MIN_GAIN]
    if not pool:
        return None
    best = max(pool, key=lambda x: (x["sport"] == w["sport"], x["p_model"]))
    return {"leg": best, "chance_with": round(weakest["chance_without"] * best["p_model"], 6)}


def grade(conn: Connection, raw_legs: str, raw_odds: str | None = None) -> dict:
    parsed = parse_legs(raw_legs)
    odds = parse_american(raw_odds)
    legs, bad = lookup(conn, parsed)
    if not legs:
        return {"legs": [], "unavailable": bad, "combo": None}
    s = score(legs, odds)
    status, settled = combo_status(legs, any(x["game_status"] != "scheduled" for x in legs))
    s["status"], s["settled"] = status, settled
    s["n_legs"] = len(legs)
    s["swap"] = _swap(conn, legs, s["weakest"]) if s["weakest"] and not s["dead"] else None
    return {"legs": legs, "unavailable": bad, "combo": s}
