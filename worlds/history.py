"""Title odds over time.

`python -m worlds knockout --snapshot <label>` appends one row per team to
outputs/odds_history.csv. Re-running a label replaces its rows in place, so snapshot order is kept.
`python -m worlds history` draws outputs/odds_history.png from that file.

The Swiss and knockout sims both use the fixed config seed, so differences between snapshots
come from the results entered in the config, not from simulation randomness.
"""
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from worlds import plots
from worlds.config import OUTPUTS_DIR
from worlds.plots import plt

HISTORY_PATH = OUTPUTS_DIR / "odds_history.csv"
COLUMNS = ["label", "timestamp", "n_results_in_config", "team", "p_advance", "p_semis",
           "p_final", "p_title"]
N_HIGHLIGHT = 6
GREY = "#c3c2bd"


def record_snapshot(label: str, summary: pd.DataFrame, n_results: int,
                    path: Path = HISTORY_PATH, now: datetime | None = None) -> pd.DataFrame:
    """Write one row per team for `label`, replacing any earlier rows with the same label."""
    label = label.strip()
    if not label:
        raise ValueError("snapshot label must not be empty")
    rows = pd.DataFrame({
        "label": label,
        "timestamp": (now or datetime.now()).isoformat(timespec="seconds"),
        "n_results_in_config": n_results,
        "team": summary.team.values,
        "p_advance": summary.p_knockout.values,
        "p_semis": summary.p_semis.values,
        "p_final": summary.p_final.values,
        "p_title": summary.p_title.values,
    })[COLUMNS]
    if path.exists():
        old = pd.read_csv(path, dtype={"label": str})
        labels = list(dict.fromkeys(old.label))
        if label not in labels:
            labels.append(label)
        parts = [rows if lb == label else old[old.label == lb] for lb in labels]
        out = pd.concat(parts, ignore_index=True)
    else:
        out = rows
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, float_format="%.4f")
    return out


def _spread(values: list[float], min_gap: float) -> list[float]:
    """Nudge label positions apart so direct labels don't overlap."""
    order = np.argsort(values)
    pos = np.array(values, float)[order]
    for i in range(1, len(pos)):
        pos[i] = max(pos[i], pos[i - 1] + min_gap)
    out = np.empty_like(pos)
    out[order] = pos
    return list(out)


def plot_history(hist: pd.DataFrame) -> None:
    labels = list(dict.fromkeys(hist.label))
    x_of = {lb: i for i, lb in enumerate(labels)}
    n_res = hist.groupby("label", sort=False).n_results_in_config.first()
    latest = hist[hist.label == labels[-1]].sort_values("p_title", ascending=False)
    top = list(latest.team.head(N_HIGHLIGHT))
    # color follows the team, not its current rank: slots ordered by rank in the first snapshot
    first = hist[hist.label == labels[0]].set_index("team").p_title
    top = sorted(top, key=lambda t: -first.get(t, -1.0))

    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    ymax = hist.p_title.max() * 100
    ends = []
    for team in [t for t in hist.team.unique() if t not in top] + top[::-1]:
        h = hist[hist.team == team].assign(x=lambda d: d.label.map(x_of)).sort_values("x")
        out = h[h.p_advance == 0]
        if len(out):  # eliminated in Swiss: draw down to 0 at that snapshot, then stop
            h = h[h.x <= out.x.iloc[0]]
        x, y = h.x.values, h.p_title.values * 100
        highlight = team in top
        color = plots.SERIES[top.index(team)] if highlight else GREY
        ax.plot(x, y, color=color, lw=2.2 if highlight else 1.1, marker="o",
                ms=6 if highlight else 3.5, zorder=3 if highlight else 2,
                markeredgecolor=plots.SURFACE, markeredgewidth=1.2 if highlight else 0)
        if len(out):
            ax.scatter([x[-1]], [0], marker="x", s=70, linewidths=2.2, zorder=5, clip_on=False,
                       color=color if highlight else plots.TEXT_2)
        if highlight:
            ends.append((team, x[-1], y[-1], color))

    for (team, x, y, color), ly in zip(ends, _spread([e[2] for e in ends], ymax * 0.045)):
        ax.text(x + 0.12, ly, f"{team}  {y:.1f}%", va="center", fontsize=8.5,
                color=plots.TEXT)
        ax.plot([x + 0.02, x + 0.1], [y, ly], color=color, lw=1)

    ax.set_xticks(range(len(labels)),
                  [f"{lb}\n({n_res[lb]} results)" for lb in labels])
    ax.set_xlim(-0.4, len(labels) - 1 + 1.4)
    ax.set_ylim(0, ymax * 1.12)
    ax.set_ylabel("Chance to win Worlds 2026 (%)")
    ax.grid(axis="y"); ax.set_axisbelow(True)
    ax.set_title("Worlds 2026 title odds over time")
    fig.text(0.01, -0.02, f"Top {N_HIGHLIGHT} teams at the latest snapshot in color, others in "
             f"grey. × = eliminated in the Swiss stage (title chance drops to 0).\n"
             f"Fixed simulation seed: changes between snapshots come from entered results.",
             fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "odds_history.png")


def run() -> None:
    if not HISTORY_PATH.exists():
        raise FileNotFoundError("no snapshots yet: run `python -m worlds knockout "
                                "--snapshot <label>` first")
    hist = pd.read_csv(HISTORY_PATH, dtype={"label": str})
    labels = list(dict.fromkeys(hist.label))
    print(f"{len(labels)} snapshot(s): {', '.join(labels)}")
    plot_history(hist)
