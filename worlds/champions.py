"""Step 2: champion presence / pick / ban / win rates, Fearless game-number usage,
and signature champions + pool depth for the Worlds teams.

Tables written: champ_stats, champ_fearless, team_pools, team_signatures.
"""
import logging

import numpy as np
import pandas as pd

from worlds import db, plots
from worlds.config import load_config
from worlds.plots import plt

log = logging.getLogger(__name__)

POSITIONS = ["top", "jng", "mid", "bot", "sup"]
FEARLESS_PATCHES = 6   # wider than recent_patches: games 4-5 are rare
POOL_MONTHS = 6
MIN_GAMES = 5          # champion stats: min games picked to report a win rate
MIN_SIG_PICKS = 3      # signature champs: min picks by the team
LIFT_PRIOR = 5         # pseudo-games shrinking team pick rate toward global
MIN_LIFT = 1.5         # signature champs: min (shrunk) lift over global pick rate


def wilson(wins, n, z=1.96):
    n = np.asarray(n, float)
    p = np.divide(wins, n, out=np.zeros_like(n), where=n > 0)
    with np.errstate(divide="ignore", invalid="ignore"):  # n == 0 -> nan, masked by caller
        denom = 1 + z**2 / n
        centre = (p + z**2 / (2 * n)) / denom
        half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return centre - half, centre + half


def scope_games(cfg: dict, n_patches: int) -> pd.DataFrame:
    """Major-league + international games on the last n patches (by patch_key)."""
    g = db.read("SELECT * FROM games")
    g = g[g.league.isin(cfg["major_leagues"]) | g.is_international.astype(bool)
          | (g.stage == "main")]
    patches = sorted(g.patch_key.unique())[-n_patches:]
    return g[g.patch_key.isin(patches)]


def champ_stats(games: pd.DataFrame) -> pd.DataFrame:
    ids = tuple(games.gameid)
    picks = db.read(f"SELECT gameid, champion, result FROM player_games "
                    f"WHERE gameid IN ({','.join('?' * len(ids))})", ids, parse_dates=())
    tg = db.read(f"SELECT gameid, ban1, ban2, ban3, ban4, ban5 FROM team_games "
                 f"WHERE gameid IN ({','.join('?' * len(ids))})", ids, parse_dates=())
    bans = tg.melt(id_vars="gameid", value_name="champion").dropna(subset=["champion"])
    n = games.gameid.nunique()

    p = picks.groupby("champion").agg(picks=("gameid", "nunique"), wins=("result", "sum"))
    b = bans.groupby("champion").gameid.nunique().rename("bans")
    s = p.join(b, how="outer").fillna(0).astype(int)
    s["pick_rate"], s["ban_rate"] = s.picks / n, s.bans / n
    s["presence"] = s.pick_rate + s.ban_rate
    s["win_rate"] = np.where(s.picks > 0, s.wins / s.picks.clip(lower=1), np.nan)
    s["wr_lo"], s["wr_hi"] = wilson(s.wins, s.picks)
    s.loc[s.picks < MIN_GAMES, ["win_rate", "wr_lo", "wr_hi"]] = np.nan
    return s.sort_values("presence", ascending=False).reset_index()


def fearless_usage(games: pd.DataFrame) -> pd.DataFrame:
    """Pick rate of each champion by game number within Bo3/Bo5 series."""
    series = db.read("SELECT series_id, best_of FROM series", parse_dates=())
    g = games.merge(series, on="series_id")
    g = g[g.best_of > 1]
    ids = tuple(g.gameid)
    picks = db.read(f"SELECT gameid, champion FROM player_games "
                    f"WHERE gameid IN ({','.join('?' * len(ids))})", ids, parse_dates=())
    picks = picks.merge(g[["gameid", "game_in_series"]], on="gameid")
    games_at = g.groupby("game_in_series").size()
    counts = picks.groupby(["champion", "game_in_series"]).gameid.nunique().unstack(fill_value=0)
    rate = counts.div(games_at, axis=1)
    rate.columns = [f"g{c}" for c in rate.columns]
    # mean game number of picks, relative to all picks: <0 = early-series priority, >0 = saved late
    mean_game = picks.groupby("champion").game_in_series.mean()
    out = rate.join(mean_game.rename("mean_game")).join(counts.sum(axis=1).rename("picks"))
    out["game_shift"] = out.mean_game - picks.game_in_series.mean()
    log.info("fearless: %d Bo3/Bo5 games, games per game number %s",
             len(g), games_at.to_dict())
    return out.reset_index(), games_at


def resolve_teams(cfg: dict) -> pd.DataFrame:
    teams = db.read("SELECT teamid, year, teamname, region FROM teams", parse_dates=())
    latest = teams.sort_values("year").groupby("teamname").last()
    rows = [{"name": t["name"], "teamid": latest.teamid.get(t["oe_name"]), "region": t["region"]}
            for t in cfg["teams"]]
    out = pd.DataFrame(rows)
    missing = out[out.teamid.isna()]
    if len(missing):
        raise ValueError(f"config teams not in DB: {list(missing.name)}")
    return out


def team_pools(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    teams = resolve_teams(cfg)
    pg = db.read("SELECT p.gameid, p.teamid, p.position, p.champion, p.result, g.date, "
                 "g.league, g.is_international FROM player_games p JOIN games g USING (gameid)")
    since = pg.date.max() - pd.DateOffset(months=POOL_MONTHS)
    pg = pg[pg.date >= since]

    # global pick rate per (role, champion): share of team-games in major/intl play
    ref = pg[pg.league.isin(cfg["major_leagues"]) | pg.is_international.astype(bool)]
    ref_games = ref.groupby("position").gameid.count() / 1  # one pick per team-game per role
    global_pr = ref.groupby(["position", "champion"]).size() / ref_games

    pools, sigs = [], []
    for t in teams.itertuples():
        tp = pg[pg.teamid == t.teamid]
        n_games = tp.gameid.nunique()
        for pos in POSITIONS:
            c = tp[tp.position == pos].champion.value_counts()
            share = c / c.sum()
            pools.append({"name": t.name, "role": pos, "games": n_games, "unique": len(c),
                          "effective": float(np.exp(-(share * np.log(share)).sum()))})
        c = tp.groupby(["position", "champion"]).agg(picks=("gameid", "size"), wins=("result", "sum"))
        c = c[c.picks >= MIN_SIG_PICKS].copy()
        g_pr = global_pr.reindex(c.index).fillna(0)
        c["team_rate"] = c.picks / n_games
        c["global_rate"] = g_pr.values
        # shrink team rate toward global with LIFT_PRIOR pseudo-games, then compare
        c["lift"] = ((c.picks + LIFT_PRIOR * c.global_rate) / (n_games + LIFT_PRIOR)) / \
            c.global_rate.clip(lower=0.01)
        # rank by excess pick rate so frequent comfort picks beat rare 3-pick oddities
        c["excess"] = c.team_rate - c.global_rate
        top = c[c.lift >= MIN_LIFT].sort_values("excess", ascending=False).head(5).reset_index()
        sigs.append(top.assign(name=t.name, games=n_games))
    return pd.DataFrame(pools), pd.concat(sigs, ignore_index=True)


# ---------- charts ----------

def plot_presence(stats: pd.DataFrame, patches: str, n_games: int, top: int = 25) -> None:
    s = stats.head(top).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    y = np.arange(len(s))
    ax.barh(y, s.pick_rate * 100, color=plots.SERIES[0], height=0.7, label="Pick rate")
    ax.barh(y, s.ban_rate * 100, left=s.pick_rate * 100 + 0.4, color=plots.SERIES[1],
            height=0.7, label="Ban rate")
    for yi, r in zip(y, s.itertuples()):
        wr = f"  WR {r.win_rate:.0%} (n={r.picks})" if pd.notna(r.win_rate) else f"  n={r.picks}"
        ax.text(r.presence * 100 + 1, yi, f"{r.presence:.0%}{wr}", va="center",
                fontsize=8, color=plots.TEXT_2)
    ax.set_yticks(y, s.champion)
    ax.set_xlabel("% of games (presence = pick + ban)")
    ax.set_xlim(0, 100 * min(1.0, s.presence.max() + 0.35))
    ax.grid(axis="x"); ax.set_axisbelow(True)
    ax.legend(loc="lower right", ncols=2)
    ax.set_title(f"Champion presence: top {top}, patches {patches}")
    fig.text(0.01, -0.01, f"{n_games} major-league and international games. "
             f"WR = win rate when picked (shown at ≥{MIN_GAMES} picks).",
             fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "presence_top.png")


def plot_fearless(fear: pd.DataFrame, stats: pd.DataFrame, games_at: pd.Series,
                  patches: str, top: int = 30) -> None:
    champs = stats.champion.head(top)
    cols = [c for c in fear.columns if c[0] == "g" and c[1:].isdigit()]
    m = fear.set_index("champion").reindex(champs)[cols].fillna(0) * 100
    order = fear.set_index("champion").reindex(champs).game_shift.sort_values().index
    m = m.loc[order]
    fig, ax = plt.subplots(figsize=(6.5, 9))
    vmax = m.values.max()
    ax.imshow(m.values, cmap=plots.SEQUENTIAL, aspect="auto", vmin=0, vmax=vmax)
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            v = m.values[i, j]
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=7.5,
                    color=plots.heat_text_color(v, vmax))
    ax.set_xticks(range(len(cols)),
                  [f"Game {c[1:]}\n(n={games_at[int(c[1:])]})" for c in cols])
    ax.set_yticks(range(len(m)), m.index)
    ax.tick_params(length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_title("Fearless: when in a series each champion gets picked")
    fig.text(0.01, -0.02, f"% of games at that game number where the champion is picked. "
             f"Bo3/Bo5 series, patches {patches}.\n"
             f"Top {top} by presence, sorted from early-series priority (top) to saved-late "
             f"(bottom).", fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "fearless_game_number.png")


def plot_pool_depth(pools: pd.DataFrame) -> None:
    m = pools.pivot(index="name", columns="role", values="effective")[POSITIONS]
    m["avg"] = m.mean(axis=1)
    m = m.sort_values("avg", ascending=False)
    games = pools.groupby("name").games.first().reindex(m.index)
    fig, ax = plt.subplots(figsize=(6.5, 7))
    vals = m[POSITIONS].values
    vmin, vmax = np.floor(vals.min()) - 1, vals.max()  # scale from data min: differences matter
    ax.imshow(vals, cmap=plots.SEQUENTIAL, aspect="auto", vmin=vmin, vmax=vmax)
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            ax.text(j, i, f"{vals[i, j]:.1f}", ha="center", va="center", fontsize=8,
                    color=plots.heat_text_color(vals[i, j] - vmin, vmax - vmin))
        ax.text(len(POSITIONS) - 0.35, i, f"avg {m.avg.iloc[i]:.1f}  ({games.iloc[i]} g)",
                va="center", fontsize=8, color=plots.TEXT)
    ax.set_xticks(range(len(POSITIONS)), [p.upper() for p in POSITIONS])
    ax.set_yticks(range(len(m)), m.index)
    ax.tick_params(length=0)
    ax.set_xlim(-0.5, len(POSITIONS) + 1.2)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_title("Champion pool depth by role: Worlds 2026 teams")
    fig.text(0.01, -0.02, f"Effective pool size = exp(entropy) of each role's picks over the "
             f"last {POOL_MONTHS} months (all leagues).\nHigher = picks spread over more "
             f"champions. Color scale starts at {vmin:.0f}; more games also raise it.", fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "team_pool_depth.png")


def plot_signatures(sigs: pd.DataFrame, cfg: dict) -> None:
    names = [t["name"] for t in cfg["teams"]]
    fig, axes = plt.subplots(4, 4, figsize=(13, 11), sharex=True)
    xmax = sigs.team_rate.max() * 100 * 1.15
    for ax, name in zip(axes.flat, names):
        s = sigs[sigs.name == name].iloc[::-1]
        y = np.arange(len(s))
        ax.barh(y, s.team_rate * 100, color=plots.SERIES[0], height=0.6)
        ax.scatter(s.global_rate * 100, y, marker="|", s=140, color=plots.TEXT, zorder=3,
                   linewidths=2)
        ax.set_yticks(y, [f"{c} ({p[:3]})" for c, p in zip(s.champion, s.position)], fontsize=8)
        ax.set_xlim(0, xmax)
        ax.grid(axis="x"); ax.set_axisbelow(True)
        games = s.games.iloc[0] if len(s) else 0
        ax.set_title(f"{name}  ({games} g)", fontsize=9.5)
    for ax in axes[-1]:
        ax.set_xlabel("% of team's games")
    fig.suptitle("Signature champions: team pick rate (bar) vs. global pick rate (│)",
                 x=0.01, ha="left", fontweight="bold", fontsize=12)
    fig.text(0.01, -0.01, f"Top 5 per team by excess pick rate over global (same role, major + "
             f"international play), last {POOL_MONTHS} months, ≥{MIN_SIG_PICKS} picks, "
             f"lift ≥{MIN_LIFT}.",
             fontsize=8, color=plots.TEXT_2)
    fig.tight_layout()
    plots.save(fig, "signature_champs.png")


def run() -> None:
    cfg = load_config()
    recent = scope_games(cfg, cfg["recent_patches"])
    patches = ", ".join(sorted(recent.patch.unique(), key=float))
    stats = champ_stats(recent)
    fear, games_at = fearless_usage(scope_games(cfg, FEARLESS_PATCHES))
    fear_patches = ", ".join(sorted(scope_games(cfg, FEARLESS_PATCHES).patch.unique(), key=float))
    pools, sigs = team_pools(cfg)

    with db.connect() as con:
        db.write_table(con, "champ_stats", stats)
        db.write_table(con, "champ_fearless", fear)
        db.write_table(con, "team_pools", pools)
        db.write_table(con, "team_signatures", sigs)

    print(f"recent patches {patches}: {recent.gameid.nunique()} games, "
          f"{len(stats)} champions picked or banned")
    print(stats.head(15)[["champion", "picks", "bans", "presence", "win_rate"]]
          .to_string(index=False, float_format="{:.2f}".format))
    f = fear[fear.picks >= 15].sort_values("game_shift")
    print("\nFearless early-series priority:", ", ".join(f.champion.head(8)))
    print("Fearless saved for late games:  ", ", ".join(f.champion.tail(8)[::-1]))
    avg = pools.groupby("name").effective.mean().sort_values(ascending=False)
    print("\npool depth (avg effective pool per role):")
    print(avg.round(1).to_string())

    plot_presence(stats, patches, recent.gameid.nunique())
    plot_fearless(fear, stats, games_at, fear_patches)
    plot_pool_depth(pools)
    plot_signatures(sigs, cfg)
