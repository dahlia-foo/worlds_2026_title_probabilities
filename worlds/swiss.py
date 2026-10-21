"""Step 4: Swiss stage Monte Carlo.

16 teams, advance at `wins_to_advance` wins, out at `losses_to_eliminate` losses. Round 1 uses the
config `round1` draw; later rounds draw random pairs within each W-L group, avoiding rematches
(and same-region pairs before `same_region_allowed_from_round`). Known `results` are applied as
played: those pairs and winners are fixed, and the rest of each group is drawn at random.

Outputs: outputs/swiss_results.csv, outputs/swiss_records.png, and SQLite tables
swiss_summary (per team) and swiss_sims (per simulation run, for the chained knockout step).
"""
import logging
from collections import Counter

import numpy as np
import pandas as pd

from worlds import db, plots
from worlds.config import OUTPUTS_DIR, load_config
from worlds.plots import plt
from worlds.series import p_series

log = logging.getLogger(__name__)

N_ROUNDS = 5


# ---------- results validation ----------

def validate_results(names: list[str], round1: list, results: list[dict], rules: dict) -> dict:
    """Check config results and return {round: [(winner_idx, loser_idx), ...]}.

    Results for round r are only accepted when every match of earlier rounds is known, so each
    team's record at the start of a round is unambiguous.
    """
    idx = {n: i for i, n in enumerate(names)}
    by_round: dict[int, list] = {}
    for r in results:
        for key in ("round", "winner", "loser"):
            if key not in r:
                raise ValueError(f"result {r} is missing '{key}'")
        for team in (r["winner"], r["loser"]):
            if team not in idx:
                raise ValueError(f"result {r}: unknown team {team!r} (not in config teams)")
        by_round.setdefault(int(r["round"]), []).append((idx[r["winner"]], idx[r["loser"]]))

    r1 = {frozenset((idx[a], idx[b])) for a, b in round1}
    wins, losses = np.zeros(len(names), int), np.zeros(len(names), int)
    played: set = set()
    for rnd in range(1, N_ROUNDS + 1):
        matches = by_round.get(rnd, [])
        active = [i for i in range(len(names)) if wins[i] < rules["wins_to_advance"]
                  and losses[i] < rules["losses_to_eliminate"]]
        seen = Counter(t for m in matches for t in m)
        if dup := [names[t] for t, c in seen.items() if c > 1]:
            raise ValueError(f"round {rnd}: teams listed in more than one result: {dup}")
        for w, l in matches:
            pair = frozenset((w, l))
            if rnd == 1 and pair not in r1:
                raise ValueError(f"round 1 result {names[w]} vs {names[l]} is not a round1 pair")
            if w not in active or l not in active:
                raise ValueError(f"round {rnd}: {names[w]} or {names[l]} already finished Swiss")
            if (wins[w], losses[w]) != (wins[l], losses[l]):
                raise ValueError(f"round {rnd}: {names[w]} ({wins[w]}-{losses[w]}) and "
                                 f"{names[l]} ({wins[l]}-{losses[l]}) have different records")
            if rules.get("no_rematches") and pair in played:
                log.warning("round %d: %s vs %s is a rematch", rnd, names[w], names[l])
        later = [r for r in by_round if r > rnd]
        if later and len(matches) < len(active) // 2:
            raise ValueError(f"results for round {min(later)} given, but round {rnd} has only "
                             f"{len(matches)} of {len(active) // 2} results")
        for w, l in matches:
            wins[w] += 1
            losses[l] += 1
            played.add(frozenset((w, l)))
    return by_round


# ---------- simulation ----------

def _pair(pool: list[int], played: set, region: list[str], allow_same_region: bool,
          rng: np.random.Generator) -> list[tuple[int, int]]:
    """Random pairing of an even pool. Tries hard constraints first, then relaxes them."""
    order = [pool[i] for i in rng.permutation(len(pool))]

    def ok_strict(a, b):
        return frozenset((a, b)) not in played and (allow_same_region or region[a] != region[b])

    def ok_rematch(a, b):
        return frozenset((a, b)) not in played

    def match(rem, ok):
        if not rem:
            return []
        a = rem[0]
        for b in rem[1:]:
            if ok(a, b):
                rest = match([t for t in rem[1:] if t != b], ok)
                if rest is not None:
                    return [(a, b)] + rest
        return None

    for ok in (ok_strict, ok_rematch, lambda a, b: True):
        pairs = match(order, ok)
        if pairs is not None:
            return pairs
    raise RuntimeError("unreachable: unconstrained pairing always succeeds")


def simulate_swiss(names, region, round1, results_by_round, p_game, rules, n_sims, seed):
    """Monte Carlo of the Swiss stage.

    p_game[i, j] = P(team i beats team j in one game). Returns (wins, losses, opponents) where
    wins/losses are (n_sims, n_teams) arrays and opponents[s][i] is team i's opponent list.
    """
    rng = np.random.default_rng(seed)
    n = len(names)
    idx = {nm: i for i, nm in enumerate(names)}
    r1_pairs = [(idx[a], idx[b]) for a, b in round1]
    need_w, need_l = rules["wins_to_advance"], rules["losses_to_eliminate"]
    bo1 = set(rules.get("bo1_rounds", []))
    region_from = rules.get("same_region_allowed_from_round", 1)
    p_by_bo = {1: p_game, 3: p_series(p_game, 3), 5: p_series(p_game, 5)}

    all_w = np.zeros((n_sims, n), int)
    all_l = np.zeros((n_sims, n), int)
    all_opp = []
    for s in range(n_sims):
        w, l = np.zeros(n, int), np.zeros(n, int)
        opp = [[] for _ in range(n)]
        played: set = set()
        for rnd in range(1, N_ROUNDS + 1):
            active = [i for i in range(n) if w[i] < need_w and l[i] < need_l]
            if not active:
                break
            known = results_by_round.get(rnd, [])
            fixed = {frozenset(m): m[0] for m in known}          # pair -> winner
            if rnd == 1:
                pairs = r1_pairs
            else:
                taken = {t for m in known for t in m}
                pairs = [tuple(m) for m in known]
                pools: dict = {}
                for i in active:
                    if i not in taken:
                        pools.setdefault((w[i], l[i]), []).append(i)
                for pool in pools.values():
                    pairs += _pair(pool, played, region, rnd >= region_from, rng)
            p = p_by_bo[1 if rnd in bo1 else 3]
            for a, b in pairs:
                key = frozenset((a, b))
                winner = fixed.get(key)
                if winner is None:
                    winner = a if rng.random() < p[a, b] else b
                loser = b if winner == a else a
                w[winner] += 1
                l[loser] += 1
                opp[a].append(b)
                opp[b].append(a)
                played.add(key)
        all_w[s], all_l[s] = w, l
        all_opp.append(opp)
    return all_w, all_l, all_opp


def summarize(names, wins, losses, rules) -> pd.DataFrame:
    need_w = rules["wins_to_advance"]
    rows = []
    for i, name in enumerate(names):
        rec = Counter(zip(wins[:, i], losses[:, i]))
        n = len(wins)
        row = {"team": name, "p_advance": float((wins[:, i] >= need_w).mean())}
        for wl in [(3, 0), (3, 1), (3, 2), (2, 3), (1, 3), (0, 3)]:
            row[f"p_{wl[0]}-{wl[1]}"] = rec.get(wl, 0) / n
        row["exp_wins"] = float(wins[:, i].mean())
        rows.append(row)
    return pd.DataFrame(rows).sort_values("p_advance", ascending=False, ignore_index=True)


# ---------- chart ----------

ADV = {"3-0": "#184f95", "3-1": "#3987e5", "3-2": "#9ec5f4"}   # blue arm, dark = best
ELIM = {"2-3": "#f4b6b5", "1-3": "#e34948", "0-3": "#a32d2c"}  # red arm, dark = worst


def plot_records(summary: pd.DataFrame, n_sims: int, n_results: int) -> None:
    s = summary.iloc[::-1]
    y = np.arange(len(s))
    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    left = np.zeros(len(s))
    for rec, color in ADV.items():
        v = s[f"p_{rec}"].values * 100
        ax.barh(y, v, left=left, color=color, height=0.68, edgecolor=plots.SURFACE, lw=1,
                label=rec)
        left += v
    right = np.zeros(len(s))
    for rec, color in ELIM.items():
        v = s[f"p_{rec}"].values * 100
        ax.barh(y, -v, left=-right, color=color, height=0.68, edgecolor=plots.SURFACE, lw=1,
                label=rec)
        right += v
    for yi, (adv, out) in enumerate(zip(left, right)):
        ax.text(adv + 1.5, yi, f"{adv:.0f}%", va="center", fontsize=8.5, color=plots.TEXT)
    ax.axvline(0, color=plots.TEXT, lw=1)
    ax.set_yticks(y, s.team)
    ax.set_xlim(-105, 112)
    ticks = [-100, -50, 0, 50, 100]
    ax.set_xticks(ticks, [f"{abs(t)}%" for t in ticks])
    ax.grid(axis="x"); ax.set_axisbelow(True)
    ax.text(-2, len(s) - 0.2, "← eliminated", ha="right", fontsize=8.5, color=plots.TEXT_2)
    ax.text(2, len(s) - 0.2, "advances →", ha="left", fontsize=8.5, color=plots.TEXT_2)
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles, labels, ncols=6, loc="upper center", bbox_to_anchor=(0.5, -0.08),
              title="Final Swiss record", title_fontsize=8.5)
    ax.set_title("Worlds 2026 Swiss stage: chance to advance and final record")
    cond = f"conditioned on {n_results} played matches" if n_results else "pre-tournament"
    fig.text(0.01, -0.06, f"{n_sims:,} simulations, {cond}. Sorted by chance to advance.",
             fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "swiss_records.png")


# ---------- entry point ----------

def game_prob_matrix(cfg: dict) -> tuple[list[str], list[str], np.ndarray]:
    from worlds.champions import resolve_teams
    from worlds.model import load_predictor, predict_neutral

    teams = resolve_teams(cfg)
    pred = load_predictor()
    n = len(teams)
    a, b = np.repeat(np.arange(n), n), np.tile(np.arange(n), n)
    p = predict_neutral(pred["model"], pred["state"], teams.teamid.values[a],
                        teams.teamid.values[b]).reshape(n, n)
    np.fill_diagonal(p, 0.5)
    log.info("model as of %s", pred["as_of"])
    return list(teams.name), list(teams.region), p


def run() -> None:
    cfg = load_config()
    rules = cfg["swiss"]
    names, region, p = game_prob_matrix(cfg)
    results = cfg.get("results") or []
    by_round = validate_results(names, cfg["round1"], results, rules)
    wins, losses, opps = simulate_swiss(names, region, cfg["round1"], by_round, p, rules,
                                        cfg["n_sims"], cfg["seed"])

    summary = summarize(names, wins, losses, rules)
    OUTPUTS_DIR.mkdir(exist_ok=True)
    summary.to_csv(OUTPUTS_DIR / "swiss_results.csv", index=False, float_format="%.4f")
    sims = pd.DataFrame({
        "sim": np.repeat(np.arange(len(wins)), len(names)),
        "team": names * len(wins),
        "wins": wins.ravel(), "losses": losses.ravel(),
        "opponents": [",".join(names[j] for j in o) for opp in opps for o in opp]})
    with db.connect() as con:
        db.write_table(con, "swiss_summary", summary)
        db.write_table(con, "swiss_sims", sims)

    print(f"{cfg['n_sims']:,} Swiss simulations, {len(results)} known results "
          f"(seed {cfg['seed']})")
    show = summary.copy()
    for c in show.columns[1:-1]:
        show[c] = (show[c] * 100).map("{:.1f}%".format)
    print(show.to_string(index=False, float_format="{:.2f}".format))
    print("saved outputs/swiss_results.csv")
    plot_records(summary, cfg["n_sims"], len(results))
