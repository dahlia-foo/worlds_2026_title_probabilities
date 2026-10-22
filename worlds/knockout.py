"""Step 5: knockout-stage Monte Carlo (8 teams, Bo5).

Modes:
  chained (default)  For every Swiss run saved by Step 4 (`swiss_sims`), draw a quarterfinal
                     bracket from that run's records and play it once -> title odds that include
                     Swiss uncertainty.
  fixed (--fixed)    Play the real bracket from config `knockout.bracket` n_sims times.

Quarterfinal draw (chained): the two 3-0 teams go in opposite halves, each against a 3-2 team.
One 3-1 team, drawn at random, faces the remaining 3-2 team, and the other two 3-1 teams face
each other. Same-region matchups and Swiss rematches are allowed.

Bracket order: [[A, B], [C, D], [E, F], [G, H]] -> winners of pairs 1 and 2 meet in one
semifinal, winners of pairs 3 and 4 in the other.

Outputs: outputs/sim_results.csv, outputs/title_odds.png, SQLite table knockout_summary.
"""
import logging

import numpy as np
import pandas as pd

from worlds import db, plots
from worlds.config import OUTPUTS_DIR, load_config
from worlds.plots import plt
from worlds.series import p_series

log = logging.getLogger(__name__)

BEST_OF = 5


def draw_bracket(advanced: list[tuple[int, int]], rng: np.random.Generator) -> list[tuple]:
    """Quarterfinal bracket from [(team_idx, swiss_losses), ...] of the 8 qualifiers."""
    by_losses = {0: [], 1: [], 2: []}
    for team, losses in advanced:
        by_losses[losses].append(team)
    sizes = {k: len(v) for k, v in by_losses.items()}
    if sizes != {0: 2, 1: 3, 2: 3}:
        raise ValueError(f"expected two 3-0, three 3-1 and three 3-2 teams, got {sizes}")
    x0, x1, x2 = (list(rng.permutation(by_losses[k])) for k in (0, 1, 2))
    top = (x0[0], x2[0])            # 3-0 vs 3-2, one in each half
    bottom = (x0[1], x2[1])
    lucky = (x1[0], x2[2])          # the lucky 3-1 team draws the last 3-2 team
    mutual = (x1[1], x1[2])         # the other two 3-1 teams face each other
    if rng.random() < 0.5:
        return [top, lucky, bottom, mutual]
    return [top, mutual, bottom, lucky]


def play_bracket(bracket: list[tuple], p5: np.ndarray, rng: np.random.Generator):
    """Play one bracket. Returns (semifinalists, finalists, champion) as team indices."""
    def win(a, b):
        return a if rng.random() < p5[a, b] else b

    semis = [win(a, b) for a, b in bracket]
    finals = [win(semis[0], semis[1]), win(semis[2], semis[3])]
    return semis, finals, win(*finals)


def simulate(brackets, n_teams: int, p5: np.ndarray, rng: np.random.Generator):
    """Play each bracket once; return per-team counts of QF, semis, final and title."""
    counts = np.zeros((4, n_teams), int)
    n = 0
    for bracket in brackets:
        semis, finals, champ = play_bracket(bracket, p5, rng)
        counts[0, [t for pair in bracket for t in pair]] += 1
        counts[1, semis] += 1
        counts[2, finals] += 1
        counts[3, champ] += 1
        n += 1
    return counts, n


def chained_brackets(names: list[str], sims: pd.DataFrame, rng: np.random.Generator,
                     wins_to_advance: int):
    """One drawn bracket per Swiss run in `swiss_sims`."""
    idx = {n: i for i, n in enumerate(names)}
    adv = sims[sims.wins >= wins_to_advance]
    for _, run in adv.groupby("sim", sort=True):
        yield draw_bracket([(idx[t], l) for t, l in zip(run.team, run.losses)], rng)


def fixed_bracket(names: list[str], cfg: dict) -> list[tuple]:
    bracket = (cfg.get("knockout") or {}).get("bracket")
    if not bracket:
        raise ValueError("fixed mode needs config knockout.bracket: "
                         "[[A, B], [C, D], [E, F], [G, H]] (bracket order)")
    if len(bracket) != 4 or any(len(pair) != 2 for pair in bracket):
        raise ValueError("knockout.bracket must be 4 pairs of 2 teams")
    flat = [t for pair in bracket for t in pair]
    if unknown := [t for t in flat if t not in names]:
        raise ValueError(f"knockout.bracket: unknown teams {unknown} (not in config teams)")
    if len(set(flat)) != 8:
        raise ValueError("knockout.bracket: a team appears more than once")
    idx = {n: i for i, n in enumerate(names)}
    return [(idx[a], idx[b]) for a, b in bracket]


def summarize(names: list[str], counts: np.ndarray, n: int) -> pd.DataFrame:
    out = pd.DataFrame({"team": names, "p_knockout": counts[0] / n, "p_semis": counts[1] / n,
                        "p_final": counts[2] / n, "p_title": counts[3] / n})
    return out.sort_values(["p_title", "p_final", "p_knockout"], ascending=False,
                           ignore_index=True)


def _pct(p: float, decimals: int) -> str:
    """Percent label that never shows a non-zero chance as zero."""
    floor = 10 ** -decimals
    if 0 < p * 100 < floor:
        return f"<{floor:g}%"
    return f"{p * 100:.{decimals}f}%"


def plot_title_odds(summary: pd.DataFrame, mode: str, n: int) -> None:
    s = summary[summary.p_knockout > 0].iloc[::-1]
    y = np.arange(len(s))
    fig, ax = plt.subplots(figsize=(7.5, 0.38 * len(s) + 1.6))
    ax.barh(y, s.p_title * 100, color=plots.SERIES[0], height=0.65)
    for yi, r in zip(y, s.itertuples()):
        ax.text(r.p_title * 100 + 0.6, yi,
                f"{_pct(r.p_title, 1)}   final {_pct(r.p_final, 0)} · "
                f"semis {_pct(r.p_semis, 0)}",
                va="center", fontsize=8, color=plots.TEXT_2)
    ax.set_yticks(y, s.team)
    ax.set_xlim(0, s.p_title.max() * 100 * 1.6)
    ax.grid(axis="x"); ax.set_axisbelow(True)
    ax.set_xlabel("Chance to win Worlds 2026 (%)")
    if mode == "chained":
        ax.set_title("Worlds 2026 title odds")
        note = (f"{n:,} simulations of the Swiss stage, each followed by a drawn knockout "
                f"bracket (Bo5).")
    else:
        ax.set_title("Worlds 2026 title odds: actual knockout bracket")
        note = f"{n:,} simulations of the fixed bracket from config (Bo5)."
    fig.text(0.01, -0.02, note, fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "title_odds.png")


def run(fixed: bool = False, snapshot: str | None = None) -> None:
    from worlds.swiss import check_fresh, game_prob_matrix

    cfg = load_config()
    names, _, p = game_prob_matrix(cfg)
    p5 = p_series(p, BEST_OF)
    rng = np.random.default_rng(cfg["seed"])
    if fixed:
        mode, bracket = "fixed", fixed_bracket(names, cfg)
        counts, n = simulate((bracket for _ in range(cfg["n_sims"])), len(names), p5, rng)
    else:
        mode = "chained"
        check_fresh(cfg)
        sims = db.read("SELECT sim, team, wins, losses FROM swiss_sims", parse_dates=())
        brackets = chained_brackets(names, sims, rng, cfg["swiss"]["wins_to_advance"])
        counts, n = simulate(brackets, len(names), p5, rng)

    summary = summarize(names, counts, n).assign(mode=mode)
    OUTPUTS_DIR.mkdir(exist_ok=True)
    summary.to_csv(OUTPUTS_DIR / "sim_results.csv", index=False, float_format="%.4f")
    with db.connect() as con:
        db.write_table(con, "knockout_summary", summary)

    print(f"knockout ({mode}): {n:,} simulations, P(title) sums to {summary.p_title.sum():.4f}")
    show = summary.drop(columns="mode")
    for c in show.columns[1:]:
        show[c] = (show[c] * 100).map("{:.1f}%".format)
    print(show.to_string(index=False))
    print("saved outputs/sim_results.csv")
    plot_title_odds(summary, mode, n)
    if snapshot:
        from worlds.history import HISTORY_PATH, record_snapshot
        n_results = len(cfg.get("results") or [])
        record_snapshot(snapshot, summary, n_results)
        print(f"snapshot {snapshot!r} ({n_results} results) saved to "
              f"{HISTORY_PATH.relative_to(OUTPUTS_DIR.parent)}")
