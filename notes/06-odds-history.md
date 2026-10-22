# Extra: Title odds over time

**Commands:** `python -m worlds knockout --snapshot <label>` and `python -m worlds history`
**Code:** `worlds/history.py`, plus small additions to `worlds/knockout.py`, `worlds/swiss.py` and `worlds/__main__.py`
**Output:** `outputs/odds_history.csv`, `outputs/odds_history.png`

## What we did

We added a way to record how the title odds change as the tournament goes on. Running the knockout simulation with `--snapshot <label>` saves one row per team with the label, the time, the number of results in the config, and each team's chances of advancing from the Swiss stage, reaching the semifinals, reaching the final and winning the title. Labels are free text; the intended ones are `pre-swiss`, `swiss-r1` through `swiss-r5`, and `post-swiss`.

Running the same label again replaces its rows instead of adding a second copy, and the snapshot keeps its original place in the sequence. A correction to `swiss-r2`, for example, stays between `swiss-r1` and `swiss-r3`.

`python -m worlds history` draws the chart from the saved rows. It plots each team's title chance across the snapshots, in the order they were taken:
- The six teams with the highest title chance at the latest snapshot are shown in color, with a label at the end of each line.
- The other teams are grey.
- A team knocked out in the Swiss stage has its line end at 0, marked with an ×.

## Keeping snapshots comparable

**Fixed seed.** The Swiss and knockout simulations both use the fixed seed from the config (2026). Running the same inputs twice gives the same numbers, so a change between two snapshots comes from the results that were added, not from simulation noise. As a check, the `pre-swiss` snapshot reproduced the earlier Step 5 figures exactly; Gen.G is at 51.4% in both.

**A guard against stale Swiss runs.** The knockout simulation builds on the saved Swiss runs. Without a check, someone could add new results, run only the knockout step, and save a snapshot labeled with the new results but computed from the old Swiss runs. To prevent that:
- the Swiss step now saves a fingerprint of everything it depended on: the teams, the round 1 draw, the results, the Swiss rules, the number of runs, the seed and the saved model;
- the chained knockout step refuses to run if that fingerprint no longer matches, and asks for the Swiss step to be rerun.

The guard fired on the first try, because the Swiss runs on disk predated the fingerprint. After rerunning the Swiss step, the snapshot went through.

**Stable colors.** Each highlighted team keeps the same color from snapshot to snapshot. Colors are assigned by the teams' order in the first snapshot, so two teams trading places later doesn't swap their colors. We checked this, along with the elimination markers, by drawing a chart from made-up data with four snapshots and four eliminations. That file was kept outside the project and isn't part of the outputs.

## Current state

There is one snapshot so far, `pre-swiss`, taken with 0 results in the config. Its numbers match the pre-tournament title odds in Step 5: Gen.G 51.4%, Hanwha Life Esports 17.7%, T1 8.3%, Dplus KIA 7.4%, Bilibili Gaming 7.4%.

![Title odds over time](../outputs/odds_history.png)

## Checks
- Saving the same label twice leaves one set of rows, with the newer numbers.
- Replacing a label in the middle of the history keeps the snapshot order.
- The Swiss fingerprint changes when results are added.
- Running the real `pre-swiss` snapshot twice still leaves exactly 16 rows.
- `--snapshot` is rejected with any command other than `knockout` or `all`.
- The full test suite (27 tests) passes.
