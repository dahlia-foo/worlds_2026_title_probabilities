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
- **Match format:** rounds 1–2 are best-of-one. From round 3 onward, any match that can send a team through or knock it out is a best-of-three. The round-3 matches between 1-1 teams decide neither, so they stay best-of-one. We first ran every round-3 to round-5 match as a best-of-three and corrected this once the rule was confirmed. The extra best-of-one adds a little upset risk: Gen.G's chance to advance moved from 96.3% to 95.7%, and the weakest teams gained slightly.
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

As a sanity check, we added a hypothetical result in which Invictus Gaming beats Gen.G in round 1. Gen.G's chance of advancing dropped from 96% to 89%, its chance of going 3-0 fell to zero, and Invictus rose from 32% to 54%. This check was run before the best-of-one fix, but the fix doesn't change how results are applied.

## Pre-tournament forecast

No Swiss matches have been played yet (the stage starts Oct 23), so this is the pre-tournament forecast.

![Swiss records](../outputs/swiss_records.png)

| Team | Advance | 3-0 | 3-1 | 3-2 | 2-3 | 1-3 | 0-3 |
|---|---|---|---|---|---|---|---|
| Gen.G | 95.7% | 48.9% | 32.5% | 14.3% | 2.6% | 1.4% | 0.4% |
| Hanwha Life Esports | 87.5% | 30.2% | 35.0% | 22.2% | 7.3% | 4.1% | 1.1% |
| T1 | 81.1% | 24.6% | 32.2% | 24.3% | 10.8% | 6.6% | 1.6% |
| Bilibili Gaming | 80.2% | 23.5% | 31.6% | 25.2% | 11.5% | 6.6% | 1.7% |
| Dplus KIA | 78.0% | 17.8% | 31.6% | 28.5% | 11.8% | 7.4% | 2.8% |
| G2 Esports | 68.3% | 12.2% | 26.3% | 29.8% | 16.1% | 10.8% | 4.9% |
| Anyone's Legend | 66.1% | 12.4% | 25.3% | 28.5% | 18.0% | 11.4% | 4.5% |
| Karmine Corp | 53.2% | 7.6% | 19.3% | 26.2% | 22.4% | 16.8% | 7.6% |
| Team Liquid | 38.3% | 7.0% | 13.7% | 17.6% | 30.1% | 24.7% | 6.9% |
| Top Esports | 37.7% | 3.8% | 13.6% | 20.3% | 24.1% | 24.6% | 13.6% |
| Invictus Gaming | 32.3% | 3.0% | 10.9% | 18.4% | 25.2% | 25.9% | 16.7% |
| Movistar KOI | 22.8% | 2.2% | 7.4% | 13.1% | 25.6% | 30.4% | 21.2% |
| Team Secret Whales | 22.7% | 3.0% | 7.9% | 11.8% | 30.5% | 30.8% | 16.0% |
| LYON | 19.8% | 2.4% | 7.0% | 10.3% | 29.5% | 32.3% | 18.5% |
| LØS | 14.6% | 1.0% | 5.0% | 8.6% | 22.5% | 33.2% | 29.6% |
| CTBC Flying Oyster | 1.9% | 0.3% | 0.7% | 0.9% | 12.1% | 32.9% | 53.1% |

**Observations**
- **Gen.G is a heavy favorite to advance (96%)** and goes 3-0 in about half of the runs.
- **All four LCK teams are in the top five.** Between them, the LCK teams average 3.4 of the 8 knockout places.
- **The cut line falls around Karmine Corp (53%).** Team Liquid (38.3%) and Top Esports (37.7%) are nearly tied, but with different profiles. Liquid's favorable round-1 matchup against CTBC Flying Oyster (77%) gives it more 3-0 runs (7.0% vs 3.8%). Top Esports opens against Hanwha Life. It goes 0-3 more often (13.6% vs 6.9%), but it also finishes 3-2 more often (20.3% vs 17.6%).
- **CTBC Flying Oyster advances in only 1.9% of runs.** It's the only Worlds team rated below zero, and it goes 0-3 more often than not.

## Checks
- The advance probabilities add up to 8.00 teams, as they must.
- Unit tests (`tests/test_swiss.py`), on a synthetic 16-team field:
  - every run ends with exactly two 3-0 teams, three 3-1, three 3-2, three 2-3, three 1-3 and two 0-3;
  - no team plays more than five matches, and there are no rematches;
  - with overwhelming strength gaps, the best team always goes 3-0;
  - best-of-one and best-of-three are assigned correctly by round and record;
  - results that are entered are respected in every run;
  - each kind of invalid result raises a clear error.
- The full test suite (15 tests) passes, and the simulation takes about 2 seconds.
