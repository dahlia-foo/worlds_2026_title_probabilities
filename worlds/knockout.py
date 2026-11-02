"""Step 5: knockout-stage Monte Carlo (8 teams, Bo5).

Modes:
  chained (default)  For every Swiss run saved by Step 4 (`swiss_sims`), draw a quarterfinal
                     bracket from that run's records and play it once -> title odds that include
                     Swiss uncertainty.
  fixed (--fixed)    Play the real bracket from config `knockout.bracket` n_sims times. Played
                     series in `knockout.results` ({winner, loser, score}) are locked in; a
                     result is accepted only once the match it belongs to is decided by earlier
                     results (quarterfinals first, then semifinals, then the final).

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


def play_bracket(bracket: list[tuple], p5: np.ndarray, rng: np.random.Generator,
                 fixed: dict | None = None):
    """Play one bracket. Returns (semifinalists, finalists, champion) as team indices.

    fixed maps frozenset({a, b}) -> winner for series already played. One random number is drawn
    per series either way, so locking in a result doesn't shift the rest of the random stream.
    """
    fixed = fixed or {}

    def win(a, b):
        r = rng.random()
        return fixed.get(frozenset((a, b)), a if r < p5[a, b] else b)

    semis = [win(a, b) for a, b in bracket]
    finals = [win(semis[0], semis[1]), win(semis[2], semis[3])]
    return semis, finals, win(*finals)


def simulate(brackets, n_teams: int, p5: np.ndarray, rng: np.random.Generator,
             fixed: dict | None = None):
    """Play each bracket once; return per-team counts of QF, semis, final and title."""
    counts = np.zeros((4, n_teams), int)
    n = 0
    for bracket in brackets:
        semis, finals, champ = play_bracket(bracket, p5, rng, fixed)
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


def knockout_results(names: list[str], bracket: list[tuple], results: list[dict]) -> dict:
    """Validate config knockout.results against the bracket; return {frozenset(pair): winner}.

    Results are replayed round by round, so a semifinal result needs both of its quarterfinals
    entered first, and the final needs both semifinals. Each half of the bracket can be ahead
    of the other.
    """
    idx = {n: i for i, n in enumerate(names)}
    pending = []
    for r in results or []:
        for key in ("winner", "loser"):
            if r.get(key) not in idx:
                raise ValueError(f"knockout result {r}: unknown or missing {key}")
        pending.append((idx[r["winner"]], idx[r["loser"]], r))
    fixed: dict = {}
    stage: list = list(bracket)      # a match is None until both of its feeders are decided
    while stage:
        pairs = {frozenset(p) for p in stage if p is not None}
        for w, l, r in list(pending):
            if frozenset((w, l)) in pairs:
                fixed[frozenset((w, l))] = w
                pending.remove((w, l, r))
        if len(stage) == 1:
            break
        winners = [fixed.get(frozenset(p)) if p is not None else None for p in stage]
        stage = [(winners[i], winners[i + 1]) if None not in winners[i:i + 2] else None
                 for i in range(0, len(winners), 2)]
    if pending:
        bad = [f"{names[w]} vs {names[l]}" for w, l, _ in pending]
        raise ValueError(f"knockout results {bad} don't match a bracket match that is decided "
                         f"by the earlier results; enter earlier rounds first")
    return fixed


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


def plot_title_odds(summary: pd.DataFrame, mode: str, n: int, stage: str | None = None,
                    n_swiss: int = 0, n_knockout: int = 0) -> None:
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
    ax.set_title("Worlds 2026 title odds" + (f": {stage}" if stage else ""))
    if mode == "chained":
        note = (f"{n:,} simulations of the Swiss stage, each followed by a drawn knockout "
                f"bracket (Bo5). {n_swiss} of 33 Swiss results entered.")
    else:
        note = (f"{n:,} simulations of the actual knockout bracket (Bo5). "
                f"{n_knockout} of 7 knockout results entered.")
    fig.text(0.01, -0.02, note, fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "title_odds.png")


def run(fixed: bool = False, snapshot: str | None = None) -> None:
    from worlds.swiss import check_fresh, game_prob_matrix

    cfg = load_config()
    names, _, p = game_prob_matrix(cfg)
    p5 = p_series(p, BEST_OF)
    rng = np.random.default_rng(cfg["seed"])
    ko_results = (cfg.get("knockout") or {}).get("results") or []
    if fixed:
        mode, bracket = "fixed", fixed_bracket(names, cfg)
        locked = knockout_results(names, bracket, ko_results)
        counts, n = simulate((bracket for _ in range(cfg["n_sims"])), len(names), p5, rng,
                             locked)
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
    n_swiss = len(cfg.get("results") or [])
    n_ko = len(ko_results) if fixed else 0
    plot_title_odds(summary, mode, n, snapshot, n_swiss, n_ko)
    if snapshot:
        from worlds.history import HISTORY_PATH, archive_snapshot, record_snapshot
        record_snapshot(snapshot, summary, n_swiss + n_ko)
        folder = archive_snapshot(snapshot)
        print(f"snapshot {snapshot!r} ({n_swiss + n_ko} results) saved to "
              f"{HISTORY_PATH.relative_to(OUTPUTS_DIR.parent)} and "
              f"{folder.relative_to(OUTPUTS_DIR.parent)}/")
