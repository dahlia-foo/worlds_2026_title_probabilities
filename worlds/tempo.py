"""Step 7: tempo analysis of the Worlds 2026 Swiss stage (Baram's "acceleration theory").

A descriptive look at the 51 Swiss games only (config `swiss_window`, UTC, end exclusive, so
knockout games are excluded). Champions carry the tempo labels from config `tempo_labels`:
accel +2, flex +1, brake 0, spike -1, hyperscale -2, per role.

Team strength is controlled with each team's pre-game Elo, rebuilt point-in-time from the full
match history using the saved model's Elo settings. "Wins above Elo" = result - Elo win chance.

Analyses: H1 comp tempo vs winning, H3 comp archetypes, H6 jungler gold curves, a K'Sante case
study, and tempo profiles of the 8 knockout teams.

Outputs: outputs/tempo_results.csv, tempo_effects.png, tempo_gold_curves.png,
tempo_team_profiles.png.
"""
import logging

import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from sklearn.linear_model import LogisticRegression

from worlds import db, plots
from worlds.config import OUTPUTS_DIR, load_config
from worlds.plots import plt

log = logging.getLogger(__name__)

SCORE = {"accel": 2, "flex": 1, "brake": 0, "spike": -1, "hyperscale": -2}
ROLE = {"top": "top", "jungle": "jng", "mid": "mid", "bot": "bot", "support": "sup"}
POSITIONS = ["top", "jng", "mid", "bot", "sup"]
MIN_LABELED = 4
N_BOOT = 2000
N_BOOT_H6 = 20000   # H6 CIs sit near 0; fewer resamples made the verdict flip
MIN_H2H = 3
GOLD_MINUTES = [10, 15, 20, 25]
FOCUS_JUNGLERS = ["Lee Sin", "Qiyana", "Vi", "Nocturne"]


# ---------- labels ----------

def load_labels(tempo_cfg: dict, known_champions: set | None = None):
    """Return ({(position, champion): score}, mismatches) from config tempo_labels.

    mismatches lists (role, label, name) for names not seen in the data. A champion given two
    different labels in the same role is an error.
    """
    labels, mismatches = {}, []
    for role, groups in tempo_cfg.items():
        if role not in ROLE:
            raise ValueError(f"tempo_labels: unknown role {role!r} (use {list(ROLE)})")
        for label, names in groups.items():
            if label not in SCORE:
                raise ValueError(f"tempo_labels.{role}: unknown label {label!r}")
            for name in names or []:
                key = (ROLE[role], name)
                if key in labels and labels[key] != SCORE[label]:
                    raise ValueError(f"tempo_labels: {name} has two labels in {role}")
                labels[key] = SCORE[label]
                if known_champions is not None and name not in known_champions:
                    mismatches.append((role, label, name))
    return labels, mismatches


def in_window(dates: pd.Series, window: dict) -> pd.Series:
    """Dates are UTC; start inclusive, end exclusive."""
    return (dates >= pd.Timestamp(window["start"])) & (dates < pd.Timestamp(window["end"]))


def archetype(row) -> str:
    jng, mid, bot, top = (row.get(f"lab_{p}") for p in ("jng", "mid", "bot", "top"))
    if jng is not None and not np.isnan(jng):
        if jng >= 1 and mid == 2:
            return "A: tempo jungle + accel mid"
        if jng <= -1 and bot == 2 and top == 0:
            return "B: scaling jungle + accel bot + brake top"
    return "Other"


def comp_frame(picks: pd.DataFrame, labels: dict) -> pd.DataFrame:
    """One row per (game, team): labels by role, comp tempo score, archetype."""
    p = picks.assign(label=[labels.get((pos, c)) for pos, c in zip(picks.position, picks.champion)])
    wide = p.pivot_table(index=["gameid", "teamid"], columns="position", values="label",
                         aggfunc="first").reindex(columns=POSITIONS)
    wide.columns = [f"lab_{c}" for c in wide.columns]
    champs = p.pivot_table(index=["gameid", "teamid"], columns="position", values="champion",
                           aggfunc="first").reindex(columns=POSITIONS)
    champs.columns = [f"champ_{c}" for c in champs.columns]
    out = wide.join(champs)
    labs = out[[f"lab_{c}" for c in POSITIONS]]
    out["n_labeled"] = labs.notna().sum(axis=1)
    out["tempo"] = labs.mean(axis=1).where(out.n_labeled >= MIN_LABELED)
    out["archetype"] = [archetype(r) for r in out.to_dict("records")]
    return out.reset_index()


# ---------- stats helpers ----------

def wilson(k: float, n: int, z: float = 1.96):
    if n == 0:
        return np.nan, np.nan
    p = k / n
    d = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / d
    return c - h, c + h


def boot_ci(values_by_game: dict, stat, rng, n_boot: int = N_BOOT):
    """Bootstrap CI resampling games. values_by_game: gameid -> array of values for that game."""
    keys = list(values_by_game)
    if not keys:
        return np.nan, np.nan
    stats = []
    for _ in range(n_boot):
        sample = rng.choice(len(keys), len(keys))
        vals = np.concatenate([values_by_game[keys[i]] for i in sample])
        if len(vals):
            stats.append(stat(vals))
    return tuple(np.percentile(stats, [2.5, 97.5]))


def verdict(lo: float, hi: float, direction: int) -> str:
    """direction +1: the theory predicts a positive value. CI on one side decides."""
    if np.isnan(lo):
        return "too few games to tell"
    if (lo > 0 and direction > 0) or (hi < 0 and direction < 0):
        return "consistent with"
    if (hi < 0 and direction > 0) or (lo > 0 and direction < 0):
        return "not consistent with"
    return "too few games to tell"


# ---------- analyses ----------

def h1(comps: pd.DataFrame, games: pd.DataFrame, rng) -> list[dict]:
    """Does the higher-tempo comp win more? Raw, Elo-adjusted, and logistic regression."""
    g = games.merge(comps.add_prefix("b_"), left_on=["gameid", "blue_teamid"],
                    right_on=["b_gameid", "b_teamid"])
    g = g.merge(comps.add_prefix("r_"), left_on=["gameid", "red_teamid"],
                right_on=["r_gameid", "r_teamid"])
    g = g.dropna(subset=["b_tempo", "r_tempo"])
    g["tempo_diff"] = g.b_tempo - g.r_tempo
    d = g[g.tempo_diff != 0]
    hi_blue = d.tempo_diff > 0
    hi_win = np.where(hi_blue, d.blue_win, 1 - d.blue_win)
    hi_p = np.where(hi_blue, d.p_elo, 1 - d.p_elo)
    resid = dict(zip(d.gameid, ([x] for x in hi_win - hi_p)))
    lo, hi = boot_ci(resid, np.mean, rng)
    k, n = int(hi_win.sum()), len(d)
    wl, wh = wilson(k, n)
    rows = [
        {"analysis": "H1", "group": "higher-tempo comp", "metric": "win rate", "n": n,
         "estimate": k / n, "ci_lo": wl, "ci_hi": wh, "verdict": verdict(wl - .5, wh - .5, 1)},
        {"analysis": "H1", "group": "higher-tempo comp", "metric": "wins above Elo", "n": n,
         "estimate": float(np.mean(hi_win - hi_p)), "ci_lo": lo, "ci_hi": hi,
         "verdict": verdict(lo, hi, 1)},
    ]
    # logistic regression on all games with both scores: win ~ Elo logit + tempo diff + side
    elo_logit = np.log(g.p_elo / (1 - g.p_elo))
    X = np.column_stack([elo_logit, g.tempo_diff])
    y = g.blue_win.values

    def coef(idx):
        m = LogisticRegression(C=np.inf, max_iter=1000).fit(X[idx], y[idx])
        return m.coef_[0][1]

    est = coef(np.arange(len(g)))
    boots = [coef(rng.choice(len(g), len(g))) for _ in range(N_BOOT // 4)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    rows.append({"analysis": "H1", "group": "tempo diff (+1)", "metric": "log-odds per +1, "
                 "Elo-controlled", "n": len(g), "estimate": est, "ci_lo": lo, "ci_hi": hi,
                 "verdict": verdict(lo, hi, 1)})
    return rows


def h3(comps: pd.DataFrame, games: pd.DataFrame, rng) -> tuple[list[dict], pd.DataFrame]:
    c = comps.merge(games[["gameid", "blue_teamid", "blue_win", "p_elo"]], on="gameid")
    blue = c.teamid == c.blue_teamid
    c["win"] = np.where(blue, c.blue_win, 1 - c.blue_win)
    c["p"] = np.where(blue, c.p_elo, 1 - c.p_elo)
    c["resid"] = c.win - c.p
    rows = []
    for arch, a in c.groupby("archetype"):
        k, n = int(a.win.sum()), len(a)
        wl, wh = wilson(k, n)
        lo, hi = boot_ci({gid: x.resid.values for gid, x in a.groupby("gameid")}, np.mean, rng)
        direction = {"A": 1, "B": 1}.get(arch[0], 0)
        rows += [
            {"analysis": "H3", "group": arch, "metric": "win rate", "n": n, "estimate": k / n,
             "ci_lo": wl, "ci_hi": wh, "verdict": ""},
            {"analysis": "H3", "group": arch, "metric": "wins above Elo", "n": n,
             "estimate": a.resid.mean(), "ci_lo": lo, "ci_hi": hi,
             "verdict": verdict(lo, hi, direction) if direction else ""},
        ]
    h2h = c[c.archetype.str[0].isin(["A", "B"])].groupby("gameid").archetype.agg(set)
    h2h = h2h[h2h.map(len) == 2]
    a_wins = c[c.gameid.isin(h2h.index) & (c.archetype.str[0] == "A")].win.sum()
    rows.append({"analysis": "H3", "group": "A vs B head-to-head", "metric": "A wins",
                 "n": len(h2h), "estimate": a_wins, "ci_lo": np.nan, "ci_hi": np.nan,
                 "verdict": "too few games to tell" if len(h2h) < MIN_H2H else ""})
    return rows, c


def h6(jungle: pd.DataFrame, labels: dict, rng) -> tuple[list[dict], pd.DataFrame]:
    """Jungler gold diff vs the enemy jungler at 10/15/20/25 min."""
    j = jungle.assign(label=[labels.get(("jng", ch)) for ch in jungle.champion])
    curves = []
    for name, sel in [*[(c, j.champion == c) for c in FOCUS_JUNGLERS],
                      ("accel junglers", j.label == 2), ("spike junglers", j.label == -1)]:
        s = j[sel]
        for m in GOLD_MINUTES:
            v = s[f"golddiffat{m}"].dropna()
            lo, hi = boot_ci({i: [x] for i, x in enumerate(v.values)}, np.mean, rng) \
                if len(v) > 1 else (np.nan, np.nan)
            curves.append({"group": name, "minute": m, "n": len(v), "mean": v.mean(),
                           "ci_lo": lo, "ci_hi": hi, "elo_p": s.p.mean()})
    curves = pd.DataFrame(curves)

    # accel vs spike junglers. Gold leads also track team strength, so verdicts use values
    # with the jungler's team Elo regressed out (linear fit on Elo log-odds across all
    # Swiss junglers); raw differences are reported alongside without a verdict.
    j["gain"] = j.golddiffat25 - j.golddiffat10
    logit = np.log(j.p / (1 - j.p))
    rows = []
    checks = [("@10", "golddiffat10", 1, "gold diff vs enemy jungler"),
              ("@25", "golddiffat25", -1, "gold diff vs enemy jungler"),
              (", gain 10->25", "gain", -1, "gold diff change")]
    for suffix, col, direction, metric in checks:
        ok = j[col].notna()
        slope, icpt = np.polyfit(logit[ok], j.loc[ok, col], 1)
        adj = j[col] - (slope * logit + icpt)
        for kind, values in (("raw", j[col]), ("Elo-adjusted", adj)):
            a = values[(j.label == 2) & ok].values
            b = values[(j.label == -1) & ok].values
            diffs = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean()
                     for _ in range(N_BOOT_H6)]
            lo, hi = np.percentile(diffs, [2.5, 97.5])
            rows.append({"analysis": "H6", "group": f"accel minus spike junglers{suffix}",
                         "metric": f"{metric} ({kind})", "n": f"{len(a)} vs {len(b)}",
                         "estimate": a.mean() - b.mean(), "ci_lo": lo, "ci_hi": hi,
                         "verdict": verdict(lo, hi, direction) if kind != "raw" else ""})
    # head-to-head jungle matchups among focus champions
    opp = j[["gameid", "teamid", "champion", "golddiffat15", "golddiffat25", "win"]]
    pair = opp.merge(opp, on="gameid", suffixes=("", "_opp"))
    pair = pair[(pair.teamid != pair.teamid_opp) & pair.champion.isin(FOCUS_JUNGLERS)
                & pair.champion_opp.isin(FOCUS_JUNGLERS) & (pair.champion < pair.champion_opp)]
    for (a, b), x in pair.groupby(["champion", "champion_opp"]):
        rows.append({"analysis": "H6 head-to-head", "group": f"{a} vs {b}",
                     "metric": f"{a} wins / gold@15 / gold@25", "n": len(x),
                     "estimate": int(x.win.sum()), "ci_lo": x.golddiffat15.mean(),
                     "ci_hi": x.golddiffat25.mean(),
                     "verdict": "" if len(x) >= MIN_H2H else "too few games to tell"})
    return rows, curves


def ksante(comps_with_results: pd.DataFrame, names: dict, rng) -> tuple[list[dict], pd.DataFrame]:
    c = comps_with_results
    k = c[c.champ_top == "K'Sante"].copy()
    opp = c[["gameid", "teamid", "champ_top"]].rename(columns={"teamid": "opp_teamid",
                                                               "champ_top": "opp_top"})
    k = k.merge(opp, on="gameid")
    k = k[k.opp_teamid != k.teamid]
    k["team"] = k.teamid.map(names).fillna(k.teamid)
    k["opponent"] = k.opp_teamid.map(names).fillna(k.opp_teamid)
    table = k[["gameid", "team", "opponent", "opp_top", "win", "p"]].rename(
        columns={"p": "elo_win_chance"})
    lo, hi = boot_ci({g: x.resid.values for g, x in k.groupby("gameid")}, np.mean, rng)
    wl, wh = wilson(int(k.win.sum()), len(k))
    rows = [{"analysis": "K'Sante", "group": "K'Sante (top)", "metric": "win rate", "n": len(k),
             "estimate": k.win.mean(), "ci_lo": wl, "ci_hi": wh, "verdict": ""},
            {"analysis": "K'Sante", "group": "K'Sante (top)", "metric": "wins above Elo",
             "n": len(k), "estimate": k.resid.mean(), "ci_lo": lo, "ci_hi": hi, "verdict": ""}]
    return rows, table


# ---------- charts ----------

DIVERGING = LinearSegmentedColormap.from_list(
    "blue_red", ["#184f95", "#6da7ec", "#f0efec", "#ef8a89", "#a32d2c"])


def plot_effects(res: pd.DataFrame, n_games: int) -> None:
    sel = res[(res.metric == "wins above Elo") & res.analysis.isin(["H1", "H3"])]
    sel = sel.iloc[::-1]
    y = np.arange(len(sel))
    fig, ax = plt.subplots(figsize=(8, 0.6 * len(sel) + 1.8))
    ax.axvline(0, color=plots.TEXT_2, lw=1)
    est, lo, hi = sel.estimate * 100, sel.ci_lo * 100, sel.ci_hi * 100
    ax.errorbar(est, y, xerr=[est - lo, hi - est], fmt="o", color=plots.SERIES[0], ms=7,
                capsize=3, lw=1.6)
    labels = [f"{g}  (n={n} {'games' if a == 'H1' else 'comps'})"
              for a, g, n in zip(sel.analysis, sel.group, sel.n)]
    ax.set_yticks(y, labels)
    for yi, e, h in zip(y, est, hi):
        ax.text(h + 1.2, yi, f"{e:+.1f}", va="center", fontsize=8.5, color=plots.TEXT)
    ax.set_xlabel("Wins above Elo expectation (percentage points, 95% CI)")
    ax.grid(axis="x"); ax.set_axisbelow(True)
    ax.set_title("Tempo and winning at Worlds 2026 Swiss")
    ax.set_ylim(-0.6, len(sel) - 0.4)
    fig.text(0.01, -0.1, f"{n_games} Swiss games. 0 = won exactly as often as team strength "
             f"(Elo) predicts. Descriptive only:\nCIs crossing 0 mean too few games to tell.",
             fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "tempo_effects.png")


def plot_gold(curves: pd.DataFrame, n_games: int) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.axhline(0, color=plots.TEXT_2, lw=1)
    offsets = np.linspace(-0.6, 0.6, len(FOCUS_JUNGLERS))
    for i, (champ, off) in enumerate(zip(FOCUS_JUNGLERS, offsets)):
        c = curves[curves.group == champ]
        x = c.minute + off
        ax.errorbar(x, c["mean"], yerr=[c["mean"] - c.ci_lo, c.ci_hi - c["mean"]],
                    color=plots.SERIES[i], marker="o", ms=6, lw=2, capsize=2.5, elinewidth=1,
                    label=f"{champ} (n={c.n.max()}, {'accel' if i < 2 else 'spike'})")
        last = c.iloc[-1]
        ax.text(last.minute + off + 0.5, last["mean"], champ, va="center", fontsize=8.5,
                color=plots.TEXT)
    ax.set_xticks(GOLD_MINUTES, [f"{m} min" for m in GOLD_MINUTES])
    ax.set_xlim(8, 28.5)
    ax.set_ylabel("Jungler gold diff vs enemy jungler")
    ax.grid(axis="y"); ax.set_axisbelow(True)
    ax.legend(loc="upper left", fontsize=8)
    ax.set_title("Accel vs spike junglers: gold over time, Worlds 2026 Swiss")
    fig.text(0.01, -0.04, f"{n_games} Swiss games. Mean with 95% bootstrap CI; points nudged "
             f"sideways to avoid overlap.\nThe 25-minute point excludes games that ended "
             f"earlier.", fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "tempo_gold_curves.png")


def plot_profiles(prof: pd.DataFrame) -> None:
    cols = POSITIONS + ["comp"]
    m = prof[cols].values.astype(float)
    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.imshow(m, cmap=DIVERGING, vmin=-2, vmax=2, aspect="auto")
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            v = m[i, j]
            ax.text(j, i, "–" if np.isnan(v) else f"{v:+.1f}", ha="center", va="center",
                    fontsize=8.5, color=plots.SURFACE if abs(v) > 1.2 else plots.TEXT)
    ax.set_xticks(range(len(cols)), [c.upper() for c in POSITIONS] + ["COMP"])
    ax.set_yticks(range(len(prof)), [f"{t}  (n={n})" for t, n in zip(prof.team, prof.games)])
    ax.axvline(len(POSITIONS) - 0.5, color=plots.SURFACE, lw=3)
    ax.tick_params(length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_title("Tempo profiles of the 8 knockout teams (Swiss games)")
    fig.text(0.01, -0.04, "Mean tempo label of each team's picks: +2 accel, +1 flex, 0 brake, "
             "-1 spike, -2 hyperscale.\nRed = faster, blue = slower. n = Swiss games played.",
             fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "tempo_team_profiles.png")


# ---------- entry point ----------

def run() -> None:
    from worlds import features as F
    from worlds.champions import resolve_teams
    from worlds.model import load_predictor

    cfg = load_config()
    rng = np.random.default_rng(cfg["seed"])
    games = db.read("SELECT gameid, date, league, blue_teamid, red_teamid, winner_teamid "
                    "FROM games WHERE league = 'WLDs'")
    games = games[in_window(games.date, cfg["swiss_window"])].copy()
    ids = tuple(games.gameid)
    q = ",".join("?" * len(ids))
    picks = db.read(f"SELECT gameid, teamid, position, champion, result, golddiffat10, "
                    f"golddiffat15, golddiffat20, golddiffat25 FROM player_games "
                    f"WHERE gameid IN ({q})", ids, parse_dates=())

    known = set(db.read("SELECT DISTINCT champion FROM player_games",
                        parse_dates=()).champion.dropna())
    labels, mismatches = load_labels(cfg["tempo_labels"], known)
    for role, label, name in mismatches:
        print(f"label mismatch: {role}/{label}: {name!r} is not a champion in the data")
    unlabeled = picks[[(p, c) not in labels for p, c in zip(picks.position, picks.champion)]]
    print(f"{len(games)} Swiss games; labels cover {1 - len(unlabeled) / len(picks):.1%} of "
          f"picks; unlabeled: " + ", ".join(f"{c} ({p})" for p, c in
                                           zip(unlabeled.position, unlabeled.champion)))

    # pre-game Elo (point-in-time, full history) as the strength control
    rows, _, _ = F.build(F.load_inputs(), load_predictor()["params"])
    games = games.merge(rows[["gameid", "p_elo", "blue_win"]], on="gameid")

    comps = comp_frame(picks, labels)
    res = h1(comps, games, rng)
    h3_rows, comps_r = h3(comps, games, rng)
    res += h3_rows
    jungle = picks[picks.position == "jng"].merge(
        comps_r[["gameid", "teamid", "win", "p"]], on=["gameid", "teamid"])
    h6_rows, curves = h6(jungle, labels, rng)
    res += h6_rows
    teams = resolve_teams(cfg)
    names = dict(zip(teams.teamid, teams.name))
    k_rows, k_table = ksante(comps_r, names, rng)
    res += k_rows
    res = pd.DataFrame(res)

    ko = [t for pair in cfg["knockout"]["bracket"] for t in pair]
    ko_ids = teams.set_index("name").teamid.reindex(ko)
    prof = comps_r[comps_r.teamid.isin(ko_ids)].copy()
    prof["team"] = prof.teamid.map(names)
    prof = prof.groupby("team").agg(
        **{p: (f"lab_{p}", "mean") for p in POSITIONS}, comp=("tempo", "mean"),
        games=("gameid", "nunique")).sort_values("comp", ascending=False).reset_index()

    OUTPUTS_DIR.mkdir(exist_ok=True)
    res.to_csv(OUTPUTS_DIR / "tempo_results.csv", index=False, float_format="%.4f")
    curves.to_csv(OUTPUTS_DIR / "tempo_gold_curves.csv", index=False, float_format="%.3f")
    k_table.to_csv(OUTPUTS_DIR / "tempo_ksante.csv", index=False, float_format="%.3f")
    prof.to_csv(OUTPUTS_DIR / "tempo_team_profiles.csv", index=False, float_format="%.2f")

    pd.set_option("display.width", 160)
    print(res.to_string(index=False, float_format="{:.3f}".format))
    print("\nJungler gold curves:\n" + curves.to_string(index=False, float_format="{:.0f}".format))
    print("\nK'Sante games:\n" + k_table.to_string(index=False))
    print("\nTeam profiles:\n" + prof.to_string(index=False, float_format="{:+.2f}".format))
    plot_effects(res, len(games))
    plot_gold(curves, len(games))
    plot_profiles(prof)
