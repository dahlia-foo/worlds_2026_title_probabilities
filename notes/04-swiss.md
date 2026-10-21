# Step 4: Swiss stage simulation

**Command:** `python -m worlds swiss`
**Code:** `worlds/swiss.py`
**Output:** `outputs/swiss_results.csv`, `outputs/swiss_records.png`, and the SQLite tables `swiss_summary` and `swiss_sims`

## What we did

We simulated the Worlds 2026 Swiss stage 10,000 times using the Step 3 model. Each run plays out the five rounds of the Swiss stage. A team advances with 3 wins and is eliminated with 3 losses. Every run ends with eight teams through to the knockout stage.

From those runs we estimate two things for each team: the chance it advances, and the chance of each final record (3-0, 3-1, 3-2, 2-3, 1-3 or 0-3). Each run's records and opponents are also saved, so that the knockout simulation in the next step can start from them.

## How a run works

- **Round 1** uses the official draw from the config.
- **Later rounds** pair teams with the same win-loss record at random. Teams that have already met are never paired again. Two teams from the same region are allowed to meet from round 2 onward, as the config sets. If no valid pairing exists within a group, these constraints are relaxed. In the 10,000 runs no rematch ever happened.
- **Match format:** rounds 1–2 are best-of-one. Rounds 3–5 are best-of-three, as the config assumes; this is still to be confirmed against the official rules.
- **Win chances** come from the Step 3 model with ratings as of 2026-10-18. They are averaged over blue and red side, since the side choice changes during a series. A best-of-three uses the series formula from Step 3.

## Conditioning on real results

As matches are played, they can be added to the `results` section of the config, and the simulation treats them as fixed. Those pairings and winners are locked in, and only the rest of each round's draw is simulated.

The simulation refuses results it can't interpret unambiguously:
- a team name that doesn't exist
- a round-1 result that isn't one of the official round-1 pairings
- two teams paired with different records
- results for a round when an earlier round is still incomplete

That last rule keeps every team's record at the start of a round known, rather than guessed.

The ratings themselves don't update when results are added. They only change after the source data is refreshed and Steps 1 and 3 are rerun.

As a sanity check, we added a hypothetical result in which Invictus Gaming beats Gen.G in round 1. Gen.G's chance of advancing dropped from 96% to 89%, its chance of going 3-0 fell to zero, and Invictus rose from 32% to 54%.

## Pre-tournament forecast

No Swiss matches have been played yet (the stage starts Oct 23), so this is the pre-tournament forecast.

![Swiss records](../outputs/swiss_records.png)

| Team | Advance | 3-0 | 3-1 | 3-2 | 2-3 | 1-3 | 0-3 |
|---|---|---|---|---|---|---|---|
| Gen.G | 96.3% | 48.9% | 34.6% | 12.9% | 2.3% | 1.1% | 0.4% |
| Hanwha Life Esports | 88.8% | 30.2% | 36.8% | 21.7% | 6.8% | 3.3% | 1.1% |
| T1 | 81.8% | 24.6% | 32.9% | 24.4% | 10.8% | 5.7% | 1.6% |
| Bilibili Gaming | 81.1% | 23.5% | 32.5% | 25.1% | 11.7% | 5.5% | 1.7% |
| Dplus KIA | 78.7% | 17.8% | 32.5% | 28.4% | 12.0% | 6.5% | 2.8% |
| G2 Esports | 69.5% | 12.2% | 26.7% | 30.6% | 16.0% | 9.6% | 4.9% |
| Anyone's Legend | 66.7% | 12.4% | 25.7% | 28.7% | 18.2% | 10.6% | 4.5% |
| Karmine Corp | 53.9% | 7.6% | 19.3% | 27.0% | 22.8% | 15.8% | 7.6% |
| Top Esports | 37.3% | 3.8% | 12.5% | 21.0% | 24.9% | 24.2% | 13.6% |
| Team Liquid | 37.1% | 7.0% | 12.6% | 17.5% | 30.7% | 25.3% | 6.9% |
| Invictus Gaming | 31.6% | 3.0% | 10.1% | 18.5% | 26.0% | 25.7% | 16.7% |
| Movistar KOI | 22.1% | 2.2% | 6.7% | 13.1% | 25.8% | 31.0% | 21.2% |
| Team Secret Whales | 21.5% | 3.0% | 6.8% | 11.7% | 30.3% | 32.2% | 16.0% |
| LYON | 18.3% | 2.4% | 5.9% | 10.0% | 29.2% | 34.0% | 18.5% |
| LØS | 13.7% | 1.0% | 4.1% | 8.6% | 22.4% | 34.3% | 29.6% |
| CTBC Flying Oyster | 1.5% | 0.3% | 0.5% | 0.7% | 10.2% | 35.3% | 53.1% |

**Observations**
- **Gen.G is a heavy favorite to advance (96%)** and goes 3-0 in about half of the runs.
- **All four LCK teams are in the top five.** Between them, the LCK teams average 3.5 of the 8 knockout places.
- **The cut line falls around Karmine Corp (54%).** Top Esports and Team Liquid are nearly tied at 37%, but with different profiles. Liquid's favorable round-1 matchup against CTBC Flying Oyster (77%) gives it more 3-0 runs (7.0% vs 3.8%). Top Esports opens against Hanwha Life. It goes 0-3 more often (13.6% vs 6.9%), but it also finishes 3-2 more often (21.0% vs 17.5%).
- **CTBC Flying Oyster advances in only 1.5% of runs.** It's the only Worlds team rated below zero, and it goes 0-3 more often than not.

## Checks
- The advance probabilities add up to 8.00 teams, as they must.
- Unit tests (`tests/test_swiss.py`), on a synthetic 16-team field:
  - every run ends with exactly two 3-0 teams, three 3-1, three 3-2, three 2-3, three 1-3 and two 0-3;
  - no team plays more than five matches, and there are no rematches;
  - with overwhelming strength gaps, the best team always goes 3-0;
  - results that are entered are respected in every run;
  - each kind of invalid result raises a clear error.
- The full test suite (14 tests) passes, and the simulation takes about 2 seconds.
