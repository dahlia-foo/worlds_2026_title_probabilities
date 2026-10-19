# LoL Worlds 2026 predictor

Predicts the Worlds 2026 winner from Oracle's Elixir (OE) match data.
Python 3.13, `.venv/`, SQLite. Run steps with `.venv/bin/python -m worlds <step>`.

## Rules
- **Never read or cat `data/raw/*.csv`** (~78MB each). Inspect them only with pandas in a script, and print small summaries (columns, `value_counts`, shapes).
- **Don't overwrite `config/worlds2026.yaml`.** The user maintains the teams, `round1` and `results` sections. Pipeline settings sit at the bottom of the file.
- **Features must be point-in-time.** A game's features may only use games strictly before it.

## Layout
```
worlds/
  __main__.py   CLI dispatcher: STEPS maps step name -> module with run()
  config.py     paths, LEAGUE_NAMES, EVENT_LEAGUES, region map, load_config()
  db.py         SQLite connect / write_table (adds indexes) / read
  load.py       step `data`: OE CSVs -> SQLite
tests/          pytest, synthetic fixtures only
config/worlds2026.yaml   teams (oe_name = exact OE teamname), Swiss rules, round1, results, settings
data/raw/       *_oe.csv (gitignored)    data/processed/worlds.db (gitignored)
outputs/        charts and CSVs
```

## Steps
| step   | module          | output |
|--------|-----------------|--------|
| `data` | `worlds/load.py` | `games`, `series`, `team_games`, `player_games`, `teams` tables |

Planned: `champs` (champion analysis), `model` (Elo + LR/XGBoost with a Worlds 2025 backtest), `sim` (Swiss + knockout Monte Carlo), and `all` (runs every step).

## Data notes (step `data`)
- OE has 12 rows per game: 10 players and 2 `position=="team"` rows. Games that don't match this shape are dropped.
- `teamid` is the team key. When it's null, the key falls back to `"name:" + teamname`.
- `patch` must be read as a string. Values are zero-padded (`16.20`), and reading them as floats turns that into `16.2`.
- A team's **region** is its most-played non-event league that year, mapped via `REGION_OF_LEAGUE` (2025 `LTA N`/`LTA S` → `LCS`/`CBLOL`).
- **`is_international`** is true when the game is in an `EVENT_LEAGUES` league and the two teams' regions differ.
  - Same-region games at MSI or Worlds don't count.
  - DCGI (Demacia Cup Global Invitational) is cross-region, so its cross-region games count as international.
- **WLDs `stage`:** games before `worlds_start[year]` are `qualifier` (regional qualifiers); games on or after it are `main`. The Worlds 2025 backtest cutoff is 2025-10-14.
- **Series:** OE has no series id. A series is grouped by league and unordered team pair, and a new one starts when OE's `game` number doesn't increase or there is a gap of 18h or more.
  - `game_in_series` = OE's `game` when it reads 1..n; otherwise date order is used. That fallback covers about 0.4% of series, which are missing games in OE.
  - `series.best_of` is inferred from the winner's game count.

## Tests
`.venv/bin/python -m pytest -q`
