"""Championship forecasts: NBA title, Super Bowl, every soccer competition (built by ``polypicker futures``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Connection

from polypicker.api.deps import get_conn, q
from polypicker.api.queries import meta

router = APIRouter(tags=["futures"])

ORDER = ["nba", "nfl", "epl", "laliga", "seriea", "bundesliga", "ligue1", "ucl", "uel", "libertadores", "sudamericana", "ecuador"]


@router.get("/futures")
def futures(conn: Connection = Depends(get_conn)):
    """Latest simulation per competition. Each entry: favourite first, every team's title chance, and the
    stats behind the pick. Probabilities come from season simulations of our game models, not betting odds."""
    rows = q(conn, """SELECT DISTINCT ON (competition) competition, sport, season, simulations, payload, generated_at
                      FROM futures_forecasts ORDER BY competition, generated_at DESC""")
    rows.sort(key=lambda r: ORDER.index(r["competition"]) if r["competition"] in ORDER else len(ORDER))
    data = [{"competition": r["competition"], "sport": r["sport"], "season": r["season"], "simulations": r["simulations"],
             "generated_at": r["generated_at"], **{k: v for k, v in r["payload"].items() if k != "generated_at"}} for r in rows]
    return {"data": data, "meta": meta()}
