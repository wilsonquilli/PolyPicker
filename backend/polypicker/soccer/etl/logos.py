"""Club logos for the web app, downloaded once per club from ESPN's logo CDN.

ESPN serves every club's logo at ``a.espncdn.com/i/teamlogos/soccer/500/{espn_team_id}.png``, the same id our soccer
data already uses, so cup clubs (Champions League, Europa League, Libertadores...) need no manual matching.
``sync_logos`` finds clubs playing in each competition's current season that have no logo file yet, downloads only
those, trims the transparent border, resizes to 256 px WebP and writes them to ``web/public/logos/soccer/{id}.webp``.
New clubs appear when a season starts or a cup draw adds teams, so in practice each logo is fetched once.

The web app reads ``web/lib/soccer-logos.json`` (list of ids with a logo file) to decide between the logo and the
colored abbreviation badge. Visitors load the files from our own site, never from ESPN.

ESPN's CDN is unofficial and undocumented (no key, no stated terms), like the rest of our ESPN data.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from pathlib import Path

from sqlalchemy import Engine, text

from polypicker.soccer.leagues import LEAGUES

log = logging.getLogger(__name__)

LOGO_URL = "https://a.espncdn.com/i/teamlogos/soccer/500/{id}.png"
SIZE = 256
REPO = Path(__file__).resolve().parents[4]            # backend/polypicker/soccer/etl/logos.py -> repo root
LOGO_DIR = REPO / "web" / "public" / "logos" / "soccer"
MANIFEST = REPO / "web" / "lib" / "soccer-logos.json"


def current_clubs(engine: Engine) -> dict[int, str]:
    """Club id -> name for every club in each competition's current season."""
    clubs: dict[int, str] = {}
    with engine.connect() as c:
        for lg in LEAGUES.values():
            rows = c.execute(text("""
                SELECT DISTINCT t.team_id, t.name FROM soccer_games g
                JOIN soccer_teams t ON t.team_id IN (g.home_team_id, g.away_team_id)
                WHERE g.competition = :c AND g.season = :s"""), {"c": lg.competition, "s": lg.current_season()}).all()
            clubs.update({r[0]: r[1] for r in rows})
    return clubs


def to_webp(png: bytes) -> bytes:
    """Trim the transparent border (logos fill the badge evenly), fit into SIZE x SIZE, encode as WebP."""
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    box = im.getchannel("A").getbbox()
    if box:
        im = im.crop(box)
    im.thumbnail((SIZE, SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(im, ((SIZE - im.width) // 2, (SIZE - im.height) // 2))
    out = io.BytesIO()
    canvas.save(out, "WEBP", quality=88, method=6)
    return out.getvalue()


def write_manifest(logo_dir: Path = LOGO_DIR, manifest: Path = MANIFEST) -> list[int]:
    ids = sorted(int(p.stem) for p in logo_dir.glob("*.webp") if re.fullmatch(r"\d+", p.stem))
    manifest.write_text(json.dumps(ids) + "\n")
    return ids


def sync_logos(engine: Engine, refresh: bool = False, fetch=None, logo_dir: Path = LOGO_DIR,
               manifest: Path = MANIFEST, pause: float = 0.1) -> dict:
    """Download missing logos (all of them with ``refresh``) and rewrite the manifest."""
    if fetch is None:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "PolyPicker/1.0 (club logos, fetched once per club)"

        def fetch(team_id: int) -> bytes | None:
            r = session.get(LOGO_URL.format(id=team_id), timeout=30)
            return r.content if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") else None

    logo_dir.mkdir(parents=True, exist_ok=True)
    clubs = current_clubs(engine)
    todo = [t for t in clubs if refresh or not (logo_dir / f"{t}.webp").exists()]
    added, missing, failed = [], [], []
    for team_id in todo:
        try:
            png = fetch(team_id)
            if png is None:
                missing.append(clubs[team_id])
                continue
            (logo_dir / f"{team_id}.webp").write_bytes(to_webp(png))
            added.append(clubs[team_id])
        except Exception as e:  # noqa: BLE001  (one bad image must not stop the rest)
            log.warning("logo %s (%s) failed: %s", team_id, clubs[team_id], e)
            failed.append(clubs[team_id])
        time.sleep(pause)
    ids = write_manifest(logo_dir, manifest)
    return {"clubs_in_current_seasons": len(clubs), "downloaded": len(added), "no_logo_at_espn": missing,
            "failed": failed, "logos_total": len(ids)}
