"""Combos endpoint: the home page's multi-leg picks, as stored and graded by ``polypicker.combos`` (every data job
refreshes them). See that module for the selection rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection

from polypicker.api.deps import cached, get_conn, q
from polypicker.api.queries import meta
from polypicker import grader
from polypicker.combos import KINDS, SIZES, SPORTS

router = APIRouter(tags=["combos"])
RECENT_DAYS = 7
RECENT_PER_SPORT = 12


def _out(r: dict, now: datetime) -> dict:
    started = r["first_tipoff"] <= now
    status = r["status"]
    if status == "open" and started:
        status = "live"      # a leg tipped off since the last job ran
    return {"id": r["id"], "kind": r["kind"], "n_legs": r["n_legs"], "status": status, "settled": r["settled_at"] is not None,
            "legs": r["legs"], "p_model": r["p_model"], "p_market": r["p_market"], "payout": r["payout"],
            "payout_basis": r["payout_basis"], "edge": r["edge"], "first_tipoff": r["first_tipoff"],
            "last_tipoff": r["last_tipoff"], "updated_at": r["updated_at"]}


@router.get("/combos")
@cached(ttl=60)
def combos(conn: Connection = Depends(get_conn)):
    """Per sport: the current combo for each slot (safer/riskier × 3/4/5 legs) and recently decided combos."""
    now = datetime.now(timezone.utc)
    current = q(conn, """SELECT DISTINCT ON (sport, kind, n_legs) * FROM combos WHERE status IN ('open', 'live')
                         ORDER BY sport, kind, n_legs, created_at DESC""")
    recent = q(conn, """SELECT * FROM combos WHERE status IN ('won', 'lost', 'void') AND last_tipoff >= :since
                        ORDER BY first_tipoff DESC, n_legs""", since=now - timedelta(days=RECENT_DAYS))
    by_slot = {(r["sport"], r["kind"], r["n_legs"]): r for r in current}
    data = {}
    for s in SPORTS:
        data[s] = {"combos": [_out(by_slot[(s, k, n)], now) if (s, k, n) in by_slot else {"kind": k, "n_legs": n, "status": "none"}
                              for k in KINDS for n in SIZES],
                   "recent": [_out(r, now) for r in recent if r["sport"] == s][:RECENT_PER_SPORT]}
    return {"data": data,
            "meta": meta(note="Legs are from different games and treated as independent. Payouts use market prices "
                              "with the bookmaker margin removed (real payouts are lower), or our own odds where no "
                              "market price exists. Informational only; not betting advice.")}


@router.get("/combos/grade")
@cached(ttl=60)
def grade_combo(legs: str = Query(..., max_length=600, description="Comma-separated sport-gameid-pick, e.g. nba-123-home,soccer-456-draw"),
                odds: str | None = Query(None, max_length=8, description="Your book's American odds for the whole combo, e.g. +650"),
                conn: Connection = Depends(get_conn)):
    """Combo grader: our chance that every leg wins, fair odds, value against your odds or the market, a letter
    grade, the weakest leg and a stronger replacement. Game outcomes only (moneyline / 1X2), one leg per game."""
    try:
        data = grader.grade(conn, legs, odds)
    except grader.LegError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"data": data,
            "meta": meta(note="Legs are from different games and treated as independent. Chances are model estimates; "
                              "informational only, not betting advice.")}
