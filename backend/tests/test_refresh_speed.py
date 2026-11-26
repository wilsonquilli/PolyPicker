"""Speed-ups must not change the data: batched weather and parallel ESPN date fetches give the same rows."""

from datetime import date, datetime, timedelta, timezone

from polypicker.etl.issues import IssueLog
from polypicker.soccer.etl.espn import EspnSoccerSource
from polypicker.weather import _forecast_batched


def _hourly(start: datetime, temp: float) -> dict:
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
    return {"hourly": {"time": times, "temperature_2m": [temp] * 48, "precipitation": [0.0] * 48,
                       "snowfall": [0.0] * 48, "wind_speed_10m": [5.0] * 48, "wind_gusts_10m": [9.0] * 48,
                       "relative_humidity_2m": [70.0] * 48}}


def test_weather_batches_venues_and_keeps_order():
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    start = now.replace(hour=0)
    venues = {f"V{i}": [{"game_id": i, "kickoff_utc": now + timedelta(hours=6), "indoor": False}] for i in range(120)}
    coords = {v: (float(i), float(-i)) for i, v in enumerate(venues)}
    calls = []

    def get(url, params):
        lats = str(params["latitude"]).split(",")
        calls.append(len(lats))
        out = [_hourly(start, float(lat)) for lat in lats]   # temperature = latitude, so order is checkable
        return out if len(out) > 1 else out[0]

    rows: list = []
    n, failed = _forecast_batched(rows, "soccer", venues, coords, now, get)
    assert (n, failed) == (3, 0) and calls == [50, 50, 20]
    assert {r["game_id"]: r["temp_c"] for r in rows} == {i: float(i) for i in range(120)}
    assert all(r["is_forecast"] for r in rows)


def test_weather_batch_failure_falls_back_per_venue():
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    venues = {f"V{i}": [{"game_id": i, "kickoff_utc": now + timedelta(hours=1), "indoor": False}] for i in range(3)}
    coords = {v: (float(i), 0.0) for i, v in enumerate(venues)}

    def get(url, params):
        lat = str(params["latitude"])
        if "," in lat:
            raise RuntimeError("batch rejected")
        if lat == "1.0":
            raise RuntimeError("bad venue")
        return _hourly(now.replace(hour=0), float(lat))

    rows: list = []
    n, failed = _forecast_batched(rows, "nfl", venues, coords, now, get)
    assert failed == 1 and sorted(r["game_id"] for r in rows) == [0, 2]


def test_parallel_collect_matches_serial():
    def board(path, params):
        d = params["dates"]
        return {"events": [{"id": d}]}

    dates = [date(2026, 11, 1) + timedelta(days=i) for i in range(9)]
    seen = {}
    for workers in (1, 4):
        src = EspnSoccerSource(None, "uefa.europa", get=board, pause=0, workers=workers)
        import polypicker.soccer.etl.espn as espn
        orig = espn.parse_scoreboard_event
        espn.parse_scoreboard_event = lambda ev, comp: ({"game_id": int(ev["id"])}, [])
        try:
            games, _ = src.collect(dates, IssueLog())
        finally:
            espn.parse_scoreboard_event = orig
        seen[workers] = [g["game_id"] for g in games]
        assert src.summary["dates"] == 9
    assert seen[1] == seen[4] == [int(d.strftime("%Y%m%d")) for d in dates]
