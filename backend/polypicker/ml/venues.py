"""NBA arena locations for travel features: latitude, longitude, standard UTC offset, altitude (m).

Team arenas by team id; neutral sites (international games, NBA Cup in Las Vegas) by the
``arena_city`` the feed reports. Unknown cities fall back to the home team's arena.
"""

from __future__ import annotations

import math

Venue = tuple[float, float, float, float]  # lat, lon, utc_offset_hours (standard time), altitude_m

ARENAS: dict[int, Venue] = {
    1610612737: (33.757, -84.396, -5, 320),    # ATL State Farm Arena
    1610612738: (42.366, -71.062, -5, 5),      # BOS TD Garden
    1610612751: (40.683, -73.975, -5, 10),     # BKN Barclays Center
    1610612766: (35.225, -80.839, -5, 230),    # CHA Spectrum Center
    1610612741: (41.881, -87.674, -6, 180),    # CHI United Center
    1610612739: (41.496, -81.688, -5, 200),    # CLE Rocket Arena
    1610612742: (32.790, -96.810, -6, 140),    # DAL American Airlines Center
    1610612743: (39.749, -105.008, -7, 1609),  # DEN Ball Arena
    1610612765: (42.341, -83.055, -5, 180),    # DET Little Caesars Arena
    1610612744: (37.768, -122.388, -8, 5),     # GSW Chase Center
    1610612745: (29.751, -95.362, -6, 15),     # HOU Toyota Center
    1610612754: (39.764, -86.156, -5, 220),    # IND Gainbridge Fieldhouse
    1610612746: (33.945, -118.343, -8, 30),    # LAC Intuit Dome
    1610612747: (34.043, -118.267, -8, 80),    # LAL Crypto.com Arena
    1610612763: (35.138, -90.051, -6, 80),     # MEM FedExForum
    1610612748: (25.781, -80.188, -5, 2),      # MIA Kaseya Center
    1610612749: (43.045, -87.917, -6, 180),    # MIL Fiserv Forum
    1610612750: (44.979, -93.276, -6, 260),    # MIN Target Center
    1610612740: (29.949, -90.082, -6, 2),      # NOP Smoothie King Center
    1610612752: (40.751, -73.993, -5, 10),     # NYK Madison Square Garden
    1610612760: (35.463, -97.515, -6, 365),    # OKC Paycom Center
    1610612753: (28.539, -81.384, -5, 30),     # ORL Kia Center
    1610612755: (39.901, -75.172, -5, 10),     # PHI Wells Fargo Center
    1610612756: (33.446, -112.071, -7, 340),   # PHX Footprint Center
    1610612757: (45.532, -122.667, -8, 15),    # POR Moda Center
    1610612758: (38.580, -121.500, -8, 10),    # SAC Golden 1 Center
    1610612759: (29.427, -98.438, -6, 200),    # SAS Frost Bank Center
    1610612761: (43.643, -79.379, -5, 80),     # TOR Scotiabank Arena
    1610612762: (40.768, -111.901, -7, 1288),  # UTA Delta Center
    1610612764: (38.898, -77.021, -5, 20),     # WAS Capital One Arena
}

NEUTRAL: dict[str, Venue] = {
    "las vegas": (36.103, -115.178, -8, 610),
    "mexico city": (19.404, -99.096, -6, 2240),
    "paris": (48.839, 2.379, 1, 40),
    "berlin": (52.507, 13.443, 1, 35),
    "london": (51.503, 0.003, 0, 10),
    "abu dhabi": (24.467, 54.600, 4, 5),
    "austin": (30.288, -97.733, -6, 150),
    "seattle": (47.622, -122.354, -8, 50),
    "kansas city": (39.097, -94.580, -6, 270),
}

ALTITUDE_M = 1000.0  # venues above this count as "altitude" (Denver, Salt Lake City, Mexico City)


def venue(home_team_id: int, arena_city: str | None) -> Venue | None:
    city = (arena_city or "").split(",")[0].strip().lower()
    return NEUTRAL.get(city) or ARENAS.get(home_team_id)


def km(a: Venue, b: Venue) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))
