"""Kickoff weather for NFL and soccer games (Open-Meteo, free, no key) -> weather_venues + game_weather.

Venue -> city from the ESPN match summary (``gameInfo.venue.address``), city -> coordinates from Open-Meteo's
geocoder (stadium names aren't in it; city precision is plenty for weather). Then per venue one call to the
historical archive for every past game, and one forecast call (16 days ahead, 7 back -- the archive lags ~5 days)
for recent / upcoming games. Values are for the kickoff hour (UTC). NFL domes / closed roofs are flagged indoor.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from polypicker.db.models import Base

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/summary"
HOURLY = "temperature_2m,precipitation,snowfall,wind_speed_10m,wind_gusts_10m,relative_humidity_2m"
COUNTRY = {"england": "united kingdom", "scotland": "united kingdom", "wales": "united kingdom",
           "northern ireland": "united kingdom", "usa": "united states", "us": "united states",
           "republic of ireland": "ireland", "czech republic": "czechia", "turkey": "turkiye"}
FIRST_SOCCER_SEASON = 2019
log = logging.getLogger(__name__)


class WeatherVenue(Base):
    __tablename__ = "weather_venues"
    sport: Mapped[str] = mapped_column(String(8), primary_key=True)
    venue: Mapped[str] = mapped_column(String(120), primary_key=True)
    city: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(60))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GameWeather(Base):
    __tablename__ = "game_weather"
    sport: Mapped[str] = mapped_column(String(8), primary_key=True)
    game_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    indoor: Mapped[bool] = mapped_column(Boolean, default=False)
    temp_c: Mapped[float | None] = mapped_column(Float)
    precip_mm: Mapped[float | None] = mapped_column(Float)
    snow_cm: Mapped[float | None] = mapped_column(Float)
    wind_kmh: Mapped[float | None] = mapped_column(Float)
    gust_kmh: Mapped[float | None] = mapped_column(Float)
    humidity: Mapped[float | None] = mapped_column(Float)
    is_forecast: Mapped[bool] = mapped_column(Boolean)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _get(url: str, params: dict, tries: int = 5) -> dict:
    import requests
    for i in range(tries):
        r = requests.get(url, params=params, timeout=60)
        if r.status_code == 429:          # Open-Meteo's per-minute / hourly budget: wait it out
            time.sleep(65 * (i + 1))
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(10 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def _games(engine: Engine, sport: str) -> list[dict]:
    sql = {"nfl": """SELECT game_id, kickoff_utc, stadium AS venue, 'football/nfl' AS path,
                            coalesce(roof IN ('dome', 'closed'), false) AS indoor FROM nfl_games WHERE stadium IS NOT NULL""",
           "soccer": f"""SELECT game_id, kickoff_utc, venue, 'soccer/' || competition AS path, false AS indoor
                         FROM soccer_games WHERE venue IS NOT NULL AND season >= {FIRST_SOCCER_SEASON}
                           AND status <> 'canceled'"""}[sport]
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql))]


def geocode(city: str, country: str | None, get=_get) -> tuple[float, float] | None:
    res = get(GEOCODE, {"name": city, "count": 10, "language": "en"}).get("results") or []
    want = COUNTRY.get((country or "").lower(), (country or "").lower())
    hits = [r for r in res if want and (r.get("country") or "").lower() == want] or ([] if want else res)
    if not hits:
        return None
    best = max(hits, key=lambda r: r.get("population") or 0)
    return best["latitude"], best["longitude"]


def locate_venues(engine: Engine, sport: str, games: list[dict], get=_get) -> dict:
    with engine.connect() as c:
        known = {r.venue for r in c.execute(text("SELECT venue FROM weather_venues WHERE sport = :s"), {"s": sport})}
    latest: dict[str, dict] = {}
    for g in sorted(games, key=lambda g: g["kickoff_utc"]):
        if g["kickoff_utc"] < datetime.now(timezone.utc):
            latest[g["venue"]] = g
    todo = [g for v, g in latest.items() if v not in known]
    found = 0
    for g in todo:
        try:
            addr = ((get(ESPN.format(path=g["path"]), {"event": g["game_id"]}).get("gameInfo") or {}).get("venue") or {}).get("address") or {}
        except Exception as e:  # noqa: BLE001
            log.warning("venue %s: ESPN summary failed: %s", g["venue"], e)
            continue
        city, country = addr.get("city"), addr.get("country")
        ll = geocode(city, country, get) if city else None
        found += ll is not None
        with engine.begin() as c:
            c.execute(insert(WeatherVenue).values(sport=sport, venue=g["venue"][:120], city=city and city[:80],
                                                  country=country and country[:60], lat=ll and ll[0], lon=ll and ll[1])
                      .on_conflict_do_nothing())
        time.sleep(0.2)
    return {"venues_new": len(todo), "venues_located": found}


def kickoff_values(hourly: dict, kickoffs: list[datetime]) -> list[dict | None]:
    idx = {t: i for i, t in enumerate(hourly.get("time", []))}
    out = []
    for ko in kickoffs:
        i = idx.get(ko.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M"))
        if i is None or hourly["temperature_2m"][i] is None:
            out.append(None)
            continue
        v = lambda k: hourly.get(k, [None] * (i + 1))[i]  # noqa: E731
        out.append({"temp_c": v("temperature_2m"), "precip_mm": v("precipitation"), "snow_cm": v("snowfall"),
                    "wind_kmh": v("wind_speed_10m"), "gust_kmh": v("wind_gusts_10m"), "humidity": v("relative_humidity_2m")})
    return out


def sync_weather(engine: Engine, sport: str, *, history: bool = False, get=_get) -> dict:
    """``history``: every past game without archive weather; otherwise games from 7 days ago to 16 days ahead."""
    games = _games(engine, sport)
    st = locate_venues(engine, sport, games, get)
    now = datetime.now(timezone.utc)
    with engine.connect() as c:
        coords = {r.venue: (r.lat, r.lon) for r in c.execute(text(
            "SELECT venue, lat, lon FROM weather_venues WHERE sport = :s AND lat IS NOT NULL"), {"s": sport})}
        done = {r.game_id for r in c.execute(text("SELECT game_id FROM game_weather WHERE sport = :s AND NOT is_forecast"), {"s": sport})}
    by_venue: dict[str, list[dict]] = defaultdict(list)
    for g in games:
        if g["venue"] in coords and g["game_id"] not in done:
            by_venue[g["venue"]].append(g)
    rows, calls = [], 0
    archive_end = (now - timedelta(days=6)).date()
    failed = 0
    order = {"soccer/eng.1": 0, "soccer/esp.1": 1, "soccer/ger.1": 1, "soccer/ita.1": 1, "soccer/fra.1": 1}
    if not history:   # the daily path: every venue's 16-day forecast, BATCH venues per request
        calls, failed = _forecast_batched(rows, sport, by_venue, coords, now, get)
        _save(engine, rows)
        return {"sport": sport, **st, "venues_with_games": len(by_venue), "api_calls": calls, "venues_failed": failed}
    for venue, gs in sorted(by_venue.items(), key=lambda kv: min(order.get(g["path"], 2) for g in kv[1])):
        lat, lon = coords[venue]
        try:
            calls += _venue_rows(rows, sport, venue, gs, lat, lon, now, archive_end, history, get)
        except Exception as e:  # noqa: BLE001  (one venue's failure mustn't stop the rest; it's retried next run)
            log.warning("weather %s %s failed: %s", sport, venue, e)
            failed += 1
        if len(rows) >= 2000:
            _save(engine, rows); rows = []
    _save(engine, rows)
    return {"sport": sport, **st, "venues_with_games": len(by_venue), "api_calls": calls, "venues_failed": failed}


BATCH = 50   # Open-Meteo takes comma-separated coordinates and answers with one result per location, in order


def _recent(gs: list[dict], now: datetime) -> list[dict]:
    return [g for g in gs if now - timedelta(days=7) <= g["kickoff_utc"] <= now + timedelta(days=16)]


def _forecast_rows(rows: list, sport: str, recent: list[dict], hourly: dict, now: datetime) -> None:
    for g, v in zip(recent, kickoff_values(hourly, [g["kickoff_utc"] for g in recent])):
        if v:   # a game already played is no longer a forecast (the archive replaces it later)
            rows.append({"sport": sport, "game_id": g["game_id"], "kickoff_utc": g["kickoff_utc"], "indoor": g["indoor"],
                         **v, "is_forecast": g["kickoff_utc"] > now, "fetched_at": now})


def _forecast_batched(rows: list, sport: str, by_venue: dict[str, list[dict]], coords: dict, now: datetime,
                      get) -> tuple[int, int]:
    """Same data as one forecast call per venue, in ceil(venues / BATCH) requests. A failed batch is retried
    venue by venue so one bad location can't drop the other 49."""
    todo = [(v, _recent(gs, now)) for v, gs in by_venue.items()]
    todo = [(v, r) for v, r in todo if r]
    calls = failed = 0
    params = {"hourly": HOURLY, "timezone": "UTC", "past_days": 7, "forecast_days": 16}
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            res = get(FORECAST, {**params, "latitude": ",".join(f"{coords[v][0]:.4f}" for v, _ in chunk),
                                 "longitude": ",".join(f"{coords[v][1]:.4f}" for v, _ in chunk)})
            calls += 1
            res = res if isinstance(res, list) else [res]
            if len(res) != len(chunk):
                raise ValueError(f"{len(res)} results for {len(chunk)} locations")
        except Exception as e:  # noqa: BLE001
            log.warning("weather %s batch failed (%s); retrying venue by venue", sport, e)
            res = []
            for v, _ in chunk:
                try:
                    res.append(get(FORECAST, {**params, "latitude": coords[v][0], "longitude": coords[v][1]}))
                    calls += 1
                except Exception as e2:  # noqa: BLE001
                    log.warning("weather %s %s failed: %s", sport, v, e2)
                    res.append(None)
                    failed += 1
        for (v, recent), r in zip(chunk, res):
            if r:
                _forecast_rows(rows, sport, recent, r.get("hourly") or {}, now)
    return calls, failed


def _venue_rows(rows: list, sport: str, venue: str, gs: list[dict], lat: float, lon: float, now: datetime,
                archive_end: date, history: bool, get) -> int:
    calls = 0
    past = [g for g in gs if g["kickoff_utc"].date() <= archive_end] if history else []
    recent = [g for g in gs if now - timedelta(days=7) <= g["kickoff_utc"] <= now + timedelta(days=16)
              and g["kickoff_utc"].date() > (archive_end if history else date.min)]
    # Open-Meteo bills a request per 14 days of data, so fetch windows of <= 14 days around actual games
    # instead of one multi-year range (a 6-year range costs ~150 calls; a season of home games ~20).
    for win in _windows(sorted(past, key=lambda g: g["kickoff_utc"])):
        s, e = win[0]["kickoff_utc"].date(), win[-1]["kickoff_utc"].date()
        h = get(ARCHIVE, {"latitude": lat, "longitude": lon, "start_date": s.isoformat(), "end_date": e.isoformat(),
                          "hourly": HOURLY, "timezone": "UTC"}).get("hourly") or {}
        calls += 1
        for g, v in zip(win, kickoff_values(h, [g["kickoff_utc"] for g in win])):
            if v:
                rows.append({"sport": sport, "game_id": g["game_id"], "kickoff_utc": g["kickoff_utc"], "indoor": g["indoor"],
                             **v, "is_forecast": False, "fetched_at": now})
        time.sleep(0.15)
    if recent:
        h = get(FORECAST, {"latitude": lat, "longitude": lon, "hourly": HOURLY, "timezone": "UTC",
                           "past_days": 7, "forecast_days": 16}).get("hourly") or {}
        calls += 1
        for g, v in zip(recent, kickoff_values(h, [g["kickoff_utc"] for g in recent])):
            if v:   # a game already played is no longer a forecast (the archive replaces it later)
                rows.append({"sport": sport, "game_id": g["game_id"], "kickoff_utc": g["kickoff_utc"], "indoor": g["indoor"],
                             **v, "is_forecast": g["kickoff_utc"] > now, "fetched_at": now})
    if past or recent:
        time.sleep(1.0)
    return calls


def _windows(games: list[dict], days: int = 13) -> list[list[dict]]:
    out: list[list[dict]] = []
    for g in games:
        if out and (g["kickoff_utc"].date() - out[-1][0]["kickoff_utc"].date()).days <= days:
            out[-1].append(g)
        else:
            out.append([g])
    return out


def _save(engine: Engine, rows: list[dict]) -> None:
    if not rows:
        return
    with engine.begin() as c:
        stmt = insert(GameWeather).values(rows)
        c.execute(stmt.on_conflict_do_update(index_elements=["sport", "game_id"], set_={
            k: stmt.excluded[k] for k in ("kickoff_utc", "indoor", "temp_c", "precip_mm", "snow_cm", "wind_kmh", "gust_kmh",
                                          "humidity", "is_forecast", "fetched_at")}))
