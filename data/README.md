# Data files (not committed)

The raw datasets are too large for git (about 1.8 GB, several files over GitHub's 100 MB limit). They are
NBA.com-derived and aren't redistributed until licensing is cleared (spec §6.4).

Download **"Historical NBA Data and Player Box Scores"** by Eoin Moore from Kaggle:
https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores

Place these files in this folder:

| File | Used for |
|---|---|
| `Games.csv` | game results |
| `TeamStatistics.csv` | team box scores |
| `PlayerStatistics.csv` | player box scores |
| `Players.csv` | player bios |
| `TeamHistories.csv` | team names / abbreviations |
| `LeagueSchedule24_25.csv`, `LeagueSchedule25_26.csv`, `LeagueSchedule26_27.csv` | schedules (upcoming games) |
| `PlayByPlay.parquet` | only to recover two games stored as 0-0 |
| `TeamStatisticsExtended.csv`, `PlayerStatisticsExtended.csv` | not used yet |

Then run `polypicker ingest` from `backend/` (see `backend/README.md`).
