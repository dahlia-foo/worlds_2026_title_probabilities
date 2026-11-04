# Step 7: Tempo analysis (acceleration theory)

**Command:** `python -m worlds tempo`
**Code:** `worlds/tempo.py`, plus a small change to `worlds/load.py`
**Output:** `outputs/tempo_results.csv`, `tempo_gold_curves.csv`, `tempo_ksante.csv`, `tempo_team_profiles.csv`, and three charts: `tempo_effects.png`, `tempo_gold_curves.png`, `tempo_team_profiles.png`

## What we did

We used Korean analyst Baram's acceleration theory (가속도 이론) to look at the Worlds 2026 Swiss stage. His Worlds 2026 chart labels each champion, role by role, as accel (+2), flex (+1), brake (0), spike (-1) or hyperscale (-2). These labels went into the config as `tempo_labels`.

The analysis uses **only the 51 Swiss games**: Worlds games from 2026-10-23 up to, but not including, 2026-11-02 UTC. That excludes the four knockout games already in the data. We planned a comparison against recent major-league patches but dropped it. The major leagues never played the Worlds patch (16.20), and the point was to describe this tournament, not to test the theory in general.

Team strength is the main confounder: strong teams win more and build gold leads whatever they pick. To control for it, we rebuilt each team's Elo before every game from the full match history, using the model's saved settings. Results are then reported as "wins above Elo", the share of games won minus the share expected from the ratings.

Every result carries a sample size and a 95% bootstrap confidence interval. The verdict is "consistent with" if the interval sits entirely on the side the theory predicts, "not consistent with" if it sits entirely on the other side, and "too few games to tell" otherwise.

## Data preparation
- **One label didn't match the data:** the jungle flex `Nafiri` should be `Naafiri`, and we fixed it in the config. Every other name, including the newer champions Locke and Zaahen, matched.
- **Coverage:** the labels cover 98.2% of Swiss picks. The 9 unlabeled picks were off-role choices such as Jayce jungle, Trundle top and Diana mid.
- **Gold columns:** the database only stored gold difference at 15 minutes. We added 10, 20 and 25 minutes to the loader and rebuilt the database.
- **Comp tempo score:** a team's comp score is the mean label of its picks, requiring at least 4 of the 5 to be labeled.

## Findings

| Question | Evidence (Swiss, n) | Verdict |
|---|---|---|
| H1: does the faster comp win more? | Won 23 of 48 (47.9%); -0.7 points vs Elo (CI -14.1 to +12.7) | too few games to tell |
| H3: tempo jungle + accel mid (A) | 8-11 in 19 comps; -7.7 points vs Elo (CI -23.3 to +7.8) | too few games to tell |
| H3: scaling jungle + accel bot + brake top (B) | 2-3 in 5 comps; -7.8 points (CI -52.5 to +36.9) | too few games to tell |
| H6: accel junglers ahead at 10 min | +25 gold vs spike junglers (CI -230 to +277), 32 vs 39 games | too few games to tell |
| H6: accel junglers fade by 25 min | gained 687 gold *more* than spike junglers from 10 to 25 (CI -30 to +1,390) | too few games to tell |
| K'Sante | 6-8 vs 6.7 expected wins; 4-5 into accel tops | too few games to tell |

**The jungle curves are the most interesting result.** Qiyana went from +287 gold at 10 minutes to +1,187 at 25 (13 games). Lee Sin went from -106 to +213 (19 games). Vi and Nocturne slipped slightly behind their opponents. So the accel junglers didn't fade; Qiyana's lead kept growing. The interval still includes zero, though, so this leans against the theory without being clear evidence.

**Team profiles.** All eight knockout teams leaned fast, with comp averages from +0.5 (Invictus Gaming) to +1.0 (Top Esports). Dplus KIA played an accel mid in all four of its Swiss games.

## Two decisions made along the way

**The H6 verdicts use Elo-adjusted gold.** Gold leads grow with team strength: about 720 gold of extra growth from 10 to 25 minutes per unit of Elo log-odds in these games. The teams playing accel and spike junglers had nearly identical average strength (51.2% vs 50.9% expected win chance), so the adjustment changes little. We still use it for the verdicts, as the plan specified. Raw numbers are kept in the CSV alongside.

**H6 uses 20,000 bootstrap resamples instead of 2,000.** The lower end of the "accel junglers gain more" interval sits within a few dozen gold of zero. With 2,000 resamples it landed on +2 in one run and -12 in another, which flipped the verdict between "not consistent with" and "too few games to tell". With 20,000 resamples it settled at about -20 to -30 across five different seeds, so the verdict is "too few games to tell". We describe the result as leaning against the theory, not contradicting it.

## Checks
- Unit tests (`tests/test_tempo.py`):
  - the role names map correctly (`jungle` to `jng`, `support` to `sup`), and unknown names are listed;
  - giving a champion two labels in the same role is rejected;
  - comp scores are computed correctly, and a comp with fewer than 4 labeled picks gets no score;
  - archetype A and B comps are classified correctly;
  - the Swiss window excludes a game at exactly 2026-11-02 00:00 UTC;
  - each verdict rule gives the right answer.
- The games per team match each team's actual Swiss path. Gen.G went 3-0 with one Bo3, so 5 games. LYON went 3-2 with three Bo3s, so 11 games.
- The full test suite (38 tests) passes, and the analysis takes about 5 seconds.
