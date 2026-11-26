from polypicker.soccer.etl.clubelo import assign


def test_one_to_one_assignment_settles_short_names():
    espn = {1: ["Nottingham Forest", "Nottm Forest"], 2: ["Manchester City", "Man City"], 3: ["Manchester United", "Man Utd"],
            4: ["Brighton & Hove Albion", "Brighton"], 5: ["Wolverhampton Wanderers", "Wolves"]}
    got = assign(["Forest", "Man City", "Man United", "Brighton", "Wolves"], espn)
    assert got == {"Forest": 1, "Man City": 2, "Man United": 3, "Brighton": 4, "Wolves": 5}


def test_spanish_short_names():
    espn = {10: ["Athletic Club", "Athletic"], 11: ["Atlético Madrid", "Atlético"], 12: ["Real Madrid"], 13: ["Real Sociedad"]}
    got = assign(["Bilbao", "Atletico", "Real Madrid", "Sociedad"], espn)
    assert got == {"Bilbao": 10, "Atletico": 11, "Real Madrid": 12, "Sociedad": 13}


def test_rows_adapts_soccerdata_frames():
    import pandas as pd

    from polypicker.soccer.etl.clubelo import _rows
    snap = pd.DataFrame({"team": ["Arsenal"], "rank": [1.0], "country": ["ENG"], "level": [1], "elo": [2050.5],
                         "from": pd.to_datetime(["2026-11-18"]), "to": pd.to_datetime(["2026-11-22"])}).set_index("team")
    hist = pd.DataFrame({"from": pd.to_datetime(["2026-11-18"]), "team": ["Arsenal"], "country": ["ENG"], "level": [1],
                         "elo": [2050.5], "to": pd.to_datetime(["2026-11-22"])}).set_index("from")
    want = [{"Club": "Arsenal", "Country": "ENG", "Level": "1", "Elo": 2050.5, "From": "2026-11-18", "To": "2026-11-22"}]
    assert _rows(snap) == want and _rows(hist) == want
