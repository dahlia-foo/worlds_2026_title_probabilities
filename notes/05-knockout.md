# Step 5: Knockout stage and title odds

**Command:** `python -m worlds knockout` (chained) or `python -m worlds knockout --fixed`
**Code:** `worlds/knockout.py`
**Output:** `outputs/sim_results.csv`, `outputs/title_odds.png`, and the SQLite table `knockout_summary`

## What we did

We simulated the eight-team knockout stage (quarterfinals, semifinals and final, all best-of-five) to turn the Swiss forecast into title odds. For every team, the output gives the chance of reaching the knockout stage, the semifinals and the final, and of winning the title.

The simulation has two modes.

**Chained (the default)** continues each of the 10,000 Swiss runs from Step 4. For each run, it takes the eight teams that advanced, draws a quarterfinal bracket from their Swiss records, and plays it out once. These title odds therefore include the uncertainty about who gets out of the Swiss stage in the first place. This is the mode to use before the knockout bracket is known.

**Fixed (`--fixed`)** plays the real bracket 10,000 times, read from a `knockout.bracket` section in the config. This is the mode to use once the Swiss stage is over and the quarterfinal draw has happened. The bracket is written as four pairs in bracket order: the winners of the first two pairs meet in one semifinal, and the winners of the last two in the other. The config doesn't have this section yet. Until it's added, fixed mode stops with a message explaining what's needed.

## The quarterfinal draw

In chained mode, each run's bracket follows the Worlds draw rules:
- The two 3-0 teams are placed in opposite halves of the bracket, and each plays a 3-2 team.
- One 3-1 team, drawn at random, plays the remaining 3-2 team. The other two 3-1 teams play each other.
- Teams from the same region can meet, and so can teams that already played each other in the Swiss stage.

Which 3-2 team each 3-0 team draws, and which half each of the remaining quarterfinals lands in, is random in each run.

## Pre-tournament title odds

These come from chained mode, using the Swiss forecast from Step 4 and ratings as of 2026-10-18.

![Title odds](../outputs/title_odds.png)

| Team | Knockout | Semifinal | Final | Title |
|---|---|---|---|---|
| Gen.G | 95.7% | 82.0% | 66.1% | **51.4%** |
| Hanwha Life Esports | 87.5% | 61.3% | 36.2% | **17.7%** |
| T1 | 81.1% | 48.4% | 22.6% | **8.3%** |
| Dplus KIA | 78.0% | 45.2% | 20.4% | **7.4%** |
| Bilibili Gaming | 80.2% | 46.3% | 21.5% | **7.4%** |
| G2 Esports | 68.3% | 32.5% | 12.8% | **3.7%** |
| Anyone's Legend | 66.1% | 29.5% | 10.1% | **2.6%** |
| Karmine Corp | 53.2% | 18.2% | 4.9% | **0.9%** |
| Top Esports | 37.7% | 9.8% | 1.8% | 0.2% |
| Invictus Gaming | 32.3% | 7.2% | 1.2% | 0.2% |
| Team Liquid | 38.3% | 8.2% | 1.3% | 0.1% |
| Team Secret Whales | 22.7% | 3.4% | 0.4% | <0.1% |
| Movistar KOI | 22.8% | 3.7% | 0.4% | <0.1% |
| LYON | 19.8% | 2.6% | 0.2% | 0.0% |
| LØS | 14.6% | 1.5% | 0.1% | 0.0% |
| CTBC Flying Oyster | 1.9% | <0.1% | 0.0% | 0.0% |

The knockout column matches each team's chance to advance from the Swiss stage in Step 4, because chained mode starts from the same 10,000 runs.

A title chance of 0.0% means the team never won in 10,000 runs, not that it can't win.

### Findings

**Gen.G wins the title in just over half of the runs (51.4%).** Gen.G gets past each stage more often than any other team:
- reaches the knockouts: 96%
- reaches the semifinal, if in the knockouts: 86%
- wins its semifinal: 81%
- wins the final, if it gets there: 78%

The best-of-five format adds to Gen.G's edge. Against Hanwha Life, the second-best team, Gen.G is a 60% favorite in a single game. That becomes 65% in a best-of-three and 69% in a best-of-five. Against T1 or Bilibili Gaming, a 66% single-game edge becomes about 78% in a best-of-five.

**Hanwha Life Esports is the clear second favorite (17.7%).** Behind it, T1, Dplus KIA and Bilibili Gaming are almost level at 7–8% each.

**The title is very likely to stay in Korea.** An LCK team wins in 84.8% of runs. LPL teams win in 10.4% and LEC teams in 4.6%. Teams from the LCS, LCP and CBLOL win in just over 0.1% of runs combined.

**The odds are concentrated at the top.** The top two teams win 69% of the time between them, and the top five 92%. Only eight teams win in more than 0.5% of runs.

## Caveats
- **Gen.G's title chance is probably somewhat overstated.** In the Worlds 2025 backtest (Step 3), games where Elo gave one side more than an 80% chance were won less often than predicted. A team that is a heavy favorite in every round will have that error compound across three best-of-five series.
- **Ratings don't move during the event.** The model uses each team's rating as of Oct 18. A team that improves during the Swiss stage won't be rated higher here until the source data is refreshed and Steps 1, 3 and 4 are rerun.
- **Games within a series are treated as independent.** Step 3 found this holds reasonably well under Fearless draft, but best-of-five series reach games 4 and 5, where the champion pool is most depleted. That's the part of a series we have the least data on.
- **Small probabilities are noisy.** With 10,000 runs, a 50% figure is accurate to about ±1 point and a 1% figure to about ±0.2 points. Values below 0.1% are essentially "almost never".

## Checks
- Title probabilities sum to 1.0000. Final probabilities sum to 2, semifinal probabilities to 4, and knockout probabilities to 8.
- Unit tests (`tests/test_knockout.py`):
  - title probabilities sum to 1 in both chained and fixed mode;
  - every drawn bracket follows the draw rules: 3-0 teams in opposite halves against 3-2 teams, one 3-1 vs 3-2, and one 3-1 vs 3-1;
  - a Swiss outcome without exactly two 3-0, three 3-1 and three 3-2 teams is rejected;
  - with overwhelming strength gaps, the strongest team always wins;
  - a missing, malformed, unknown-team or duplicate-team fixed bracket raises a clear error;
  - each team's chances never increase from one round to the next (title ≤ final ≤ semifinal ≤ knockout).
- **Fixed mode on the real model.** We ran it end to end with a made-up bracket held in memory, so neither the config nor the saved outputs were changed. Title probabilities summed to 1, and Gen.G came out at 52%, close to its chained figure.
- The full test suite (24 tests) passes. Each mode takes about 2 seconds.
