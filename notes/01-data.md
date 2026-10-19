# Step 1: Data loading and cleaning

**Command:** `python -m worlds data`
**Code:** `worlds/load.py`, `worlds/config.py`, `worlds/db.py`
**Output:** `data/processed/worlds.db` (SQLite)

## What we did

We loaded two seasons of Oracle's Elixir match data (`2025_oe.csv` and `2026_oe.csv`) into a SQLite database. Each file holds 12 rows per game: one per player, plus one summary row per team. We split these into five tables:

| Table | Rows | Contents |
|---|---|---|
| `games` | 19,797 | one row per game: date, league, patch, blue/red teams, winner, series id |
| `series` | 9,264 | one row per match: teams, score, inferred best-of |
| `team_games` | 39,594 | one row per team per game: result, gold/XP diff at 15, objectives, picks and bans |
| `player_games` | 197,970 | one row per player per game: champion, role, KDA, CS, damage, gold share |
| `teams` | 647 teams | team id, name, home league and region for each season |

The data runs from 2025-01-11 to 2026-10-18, so it includes the first four days of the Worlds 2026 main event (Oct 15–18). Every game in both files had the expected 12 rows, so no games were dropped for being malformed. Gold difference at 15 minutes is available for 95% of team rows.

All 16 team names in `config/worlds2026.yaml` match the 2026 data exactly.

## Decisions and findings

**Worlds qualifiers are not Worlds games.** The `WLDs` league code also covers regional qualifiers played weeks before the event (Sept 25–27, 2025 and Sept 17–19, 2026). We tag those as `qualifier` and treat them as domestic. Games on or after the main event's start date are tagged `main`. That start date is 2025-10-14 for 2025 and 2026-10-15 for 2026, and the 2025 date doubles as the Step 3 backtest cutoff. This gives 84 main-event games in 2025 and 22 so far in 2026.

**"International" means cross-region, not just "at an international event".** A game counts as international only if it's played in an event league (Worlds, MSI, First Stand, EWC, European Masters and similar) and the two teams come from different regions. Same-region games at Worlds or MSI, such as LCK vs LCK, carry no information about how regions compare, so they're excluded. That leaves 1,500 international games, including 85 of the 106 Worlds main-event games.

**DCGI is an international event.** We expected the 2026 Demacia Cup to be an LPL-only tournament. In fact it includes KT Rolster, FlyQuest, Team Vitality and RED Canids, so it's the *Global Invitational* edition, and its cross-region games count as international. The 2025 Demacia Cup (`DCup`) was LPL-only, and the rule above leaves it domestic.

**Regions changed between seasons.** In 2025 the Americas played as LTA North and LTA South. In 2026 they are LCS and CBLOL. We map the old codes to the new ones so a team keeps the same region across both seasons. A team's region is its most-played non-event league in a given season.

**Patch numbers must be read as text.** The file stores patches with zero padding (`16.20`). Reading them as numbers turns `16.20` into `16.2`, which then sorts before `16.18`. We keep the original string and add a separate numeric sort key.

**Series had to be reconstructed.** Oracle's Elixir has no match id. We grouped games into series by league and team pair, and started a new series whenever the in-series game number reset or 18 hours passed between games. Our first version mixed games from different leagues into the same series. Fixing that brought the share of series with inconsistent game numbers down from 26% to 0.4% (34 series). Those remaining series are missing a game in the source data, so we order their games by date instead.

**Missing team ids.** About 3% of team rows have no `teamid`. For those we use the team name as the key.

## Checks
- Games, team rows ÷ 2 and player rows ÷ 10 all equal 19,797.
- A unit test (`tests/test_load.py`) runs a small synthetic dataset through the cleaning step. It checks that a malformed game is dropped, that winners and series grouping are correct, and that missing picks are filled from player rows.
