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
  champions.py  step `champs`: champion stats, Fearless usage, team pools/signatures + charts
  plots.py      matplotlib style; colors = dataviz skill reference palette (light mode)
  features.py   one chronological pass: pre-game team features per game + final team state
  model.py      step `model`: Elo tuning, Worlds 2025 backtest, final model -> model.joblib
  series.py     game p -> Bo1/Bo3/Bo5 series p (independent games)
  swiss.py      step `swiss`: Swiss-stage Monte Carlo, conditioned on config results
tests/          pytest, synthetic fixtures only
config/worlds2026.yaml   teams (oe_name = exact OE teamname), Swiss rules, round1, results, settings
data/raw/       *_oe.csv (gitignored)    data/processed/worlds.db (gitignored)
outputs/        charts and CSVs
notes/          one human-readable report per finished step (01-data.md, 02-champions.md, ...)
```

After finishing a step, add `notes/0N-<step>.md`. Write it as a short project report: what was done, the decisions made, the key findings with real numbers, and the checks run. Link charts as `../outputs/*.png`.

## Steps
| step   | module          | output |
|--------|-----------------|--------|
| `data` | `worlds/load.py` | `games`, `series`, `team_games`, `player_games`, `teams` tables |
| `champs` | `worlds/champions.py` | `champ_stats`, `champ_fearless`, `team_pools`, `team_signatures` tables; `outputs/presence_top.png`, `fearless_game_number.png`, `team_pool_depth.png`, `signature_champs.png` |
| `model` | `worlds/model.py` | `data/processed/model.joblib`, plus the `team_state`, `game_features` and `backtest_results` tables; `outputs/backtest_calibration.png`, `elo_worlds_teams.png` |

| `swiss` | `worlds/swiss.py` | `swiss_summary` and `swiss_sims` tables; `outputs/swiss_results.csv`, `swiss_records.png` |

Planned: Step 5, `knockout [--fixed]`. Chained mode reads each simulation's Swiss outcome from `swiss_sims` and produces title odds. Fixed mode reads the real quarterfinal matchups from a config `knockout.bracket` section. `all` runs every registered step.

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

## Champion notes (step `champs`)
- **Scope:** major leagues (config `major_leagues`), cross-region event games and Worlds main-event games, on the last `recent_patches` patches (sorted by `patch_key`).
- **Presence** = (games picked + games banned) / games. A win rate is shown at ≥5 picks, with a Wilson CI.
- **Fearless:** 2026 major-league Bo3/Bo5 series never repeat a champion, for either team, so this is hard Fearless. The heatmap shows each champion's pick rate by game number over the last 6 patches; games 4–5 are rare.
- **Team pools** cover the last 6 months, all leagues.
  - Effective pool = exp(entropy) of picks per role. It rises with games played.
  - Signature champions = top excess pick rate (team rate − global rate for the same role), with ≥3 picks and shrunk lift ≥1.5.

## Model notes (step `model`)
- **Elo** = team component + region offset, on the 400-point scale. Region offsets move only on `is_international` games outside `OFFSET_EXCLUDE_LEAGUES` (EM, WSCI); those events link minor regions only to each other.
- **Season regression** at the year boundary: each team component is regressed toward its region mean by `season_regression`, and region offsets shrink by a quarter of that amount.
- K and region K are tuned on games before the cutoff only.
- **Training data** are major-league and cross-region games from 2025-03-01 on (`_scope`), with every game mirrored so both team orderings appear. `GameModel.predict` enforces p(A,B) = 1 − p(B,A).
- **Backtest:** Worlds 2025 main-event games, scored with each team's state frozen at the cutoff (`build(until=cutoff)`).
- **Step 4 entry point:** `model.load_predictor()` returns `{model, state, ...}`. `model.predict_neutral(model, state, a_ids, b_ids)` gives the single-game probability averaged over sides.

## Swiss notes (step `swiss`)
- `simulate_swiss()` is pure: no database access, it takes a probability matrix. `game_prob_matrix()` builds that matrix from the saved model.
- **Pairing:** round 1 uses `round1`. Later rounds draw random pairs within each W-L group, with no rematches, and no same-region pairs before `same_region_allowed_from_round`. Constraints are relaxed in order only if a group can't otherwise be paired.
- **Match format:** rounds in `bo1_rounds` are Bo1; all later rounds are Bo3.
- **Results:** config `results` fix those pairs and winners. `validate_results()` rejects results for round r unless every match of earlier rounds is entered, and also rejects unknown teams, records that don't match, or a round-1 result that isn't a `round1` pair.
- **Saved runs:** `swiss_sims` has one row per (sim, team) with wins, losses and the ordered opponents (comma-joined). The chained knockout step reads these.

## Tests
`.venv/bin/python -m pytest -q`
