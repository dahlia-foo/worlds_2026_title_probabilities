"""Point-in-time team features, built in one chronological pass over all games.

For every game we record each team's state *before* the game, then update it.
The state left after the last game (optionally cut off at a date) is the snapshot
used to predict future matchups.

Per-team state:
  elo     team component + region offset (region offsets move only on international games)
  form    EWMA of results
  resid   EWMA of (result - Elo expectation): beating or missing expectations lately
  gold    EWMA of gold diff at 15 (complete-data games only)
  pool    mean over roles of exp(entropy) of the last POOL_WINDOW picks
"""
import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

from worlds import db
from worlds.config import OFFSET_EXCLUDE_LEAGUES

POSITIONS = ["top", "jng", "mid", "bot", "sup"]
FORM_SPAN = 10
POOL_WINDOW = 40          # team-games
MIN_GOLD_GAMES = 3
POOL_FILL = 8.0           # pool size for teams with no pick history
FEATURES = ["elo_diff", "form_diff", "resid_diff", "gold_diff", "pool_diff", "games_diff",
            "gold_known", "blue"]


@dataclass(frozen=True)
class EloParams:
    k: float = 32.0
    k_region: float = 10.0
    season_regression: float = 0.5
    region_regression_share: float = 0.25   # offsets regress by share * season_regression


def expected(ra, rb):
    return 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0))


def load_inputs() -> dict:
    games = db.read("SELECT gameid, date, year, league, datacompleteness, is_international, "
                    "stage, series_id, game_in_series, blue_teamid, red_teamid, winner_teamid "
                    "FROM games ORDER BY date, gameid")
    gold = db.read("SELECT gameid, teamid, golddiffat15 FROM team_games", parse_dates=())
    picks = db.read("SELECT gameid, teamid, position, champion FROM player_games", parse_dates=())
    teams = db.read("SELECT teamid, year, region FROM teams", parse_dates=())
    return {
        "games": games,
        "gold": {(r.gameid, r.teamid): r.golddiffat15 for r in gold.itertuples()},
        "picks": picks.groupby(["gameid", "teamid"])[["position", "champion"]]
                      .apply(lambda d: dict(zip(d.position, d.champion))).to_dict(),
        "region": {(r.teamid, r.year): r.region for r in teams.itertuples()
                   if isinstance(r.region, str)},
    }


class _Team:
    __slots__ = ("elo", "form", "resid", "gold", "gold_n", "games", "region", "pool")

    def __init__(self):
        self.elo, self.form, self.resid, self.gold = 0.0, 0.5, 0.0, 0.0
        self.gold_n = self.games = 0
        self.region = "UNK"
        self.pool = {p: (deque(maxlen=POOL_WINDOW), Counter()) for p in POSITIONS}

    def pool_size(self) -> float:
        sizes = []
        for q, c in self.pool.values():
            n = len(q)
            if n:
                sizes.append(math.exp(-sum(v / n * math.log(v / n) for v in c.values())))
        return float(np.mean(sizes)) if sizes else np.nan

    def push_picks(self, picks: dict):
        for pos, champ in picks.items():
            if pos not in self.pool:
                continue
            q, c = self.pool[pos]
            if len(q) == q.maxlen:
                old = q[0]
                c[old] -= 1
                if not c[old]:
                    del c[old]
            q.append(champ)
            c[champ] += 1


def _season_regression(teams: dict, offsets: dict, params: EloParams):
    """Shrink team components toward their region mean; shrink offsets toward 0 more lightly."""
    r = params.season_regression
    by_region = defaultdict(list)
    for t in teams.values():
        by_region[t.region].append(t.elo)
    means = {reg: float(np.mean(v)) for reg, v in by_region.items()}
    for t in teams.values():
        m = means[t.region]
        t.elo = m + (1 - r) * (t.elo - m)
    shrink = 1 - r * params.region_regression_share
    for reg in offsets:
        offsets[reg] *= shrink


def build(inputs: dict, params: EloParams = EloParams(), until=None):
    """Return (pre-game feature rows per game, team state snapshot after the last game used)."""
    games = inputs["games"]
    if until is not None:
        games = games[games.date < pd.Timestamp(until)]
    alpha = 2 / (FORM_SPAN + 1)
    teams: dict[str, _Team] = defaultdict(_Team)
    offsets: dict[str, float] = defaultdict(float)
    rows, year = [], None

    for g in games.itertuples(index=False):
        if year is not None and g.year > year:
            _season_regression(teams, offsets, params)
        year = g.year
        a, b = teams[g.blue_teamid], teams[g.red_teamid]
        a.region = inputs["region"].get((g.blue_teamid, g.year), a.region)
        b.region = inputs["region"].get((g.red_teamid, g.year), b.region)
        ra, rb = a.elo + offsets[a.region], b.elo + offsets[b.region]
        p = expected(ra, rb)

        row = {"gameid": g.gameid, "date": g.date, "year": g.year, "league": g.league,
               "is_international": bool(g.is_international), "stage": g.stage,
               "series_id": g.series_id, "game_in_series": g.game_in_series,
               "blue_teamid": g.blue_teamid, "red_teamid": g.red_teamid, "p_elo": p,
               "blue_win": int(g.winner_teamid == g.blue_teamid)}
        for side, t, r in (("b", a, ra), ("r", b, rb)):
            row |= {f"{side}_elo": r, f"{side}_form": t.form, f"{side}_resid": t.resid,
                    f"{side}_gold": t.gold, f"{side}_gold_n": t.gold_n,
                    f"{side}_pool": t.pool_size(), f"{side}_games": t.games}
        rows.append(row)

        s = row["blue_win"]
        delta = s - p
        a.elo += params.k * delta
        b.elo -= params.k * delta
        if g.is_international and g.league not in OFFSET_EXCLUDE_LEAGUES:
            offsets[a.region] += params.k_region * delta
            offsets[b.region] -= params.k_region * delta
        complete = g.datacompleteness == "complete"
        for t, tid, res, d in ((a, g.blue_teamid, s, delta), (b, g.red_teamid, 1 - s, -delta)):
            t.form += alpha * (res - t.form)
            t.resid += alpha * (d - t.resid)
            gd = inputs["gold"].get((g.gameid, tid))
            if complete and gd is not None and not np.isnan(gd):
                t.gold = gd if t.gold_n == 0 else t.gold + alpha * (gd - t.gold)
                t.gold_n += 1
            t.push_picks(inputs["picks"].get((g.gameid, tid), {}))
            t.games += 1

    state = pd.DataFrame([{"teamid": tid, "region": t.region, "elo": t.elo + offsets[t.region],
                           "elo_team": t.elo, "region_offset": offsets[t.region],
                           "form": t.form, "resid": t.resid, "gold": t.gold,
                           "gold_n": t.gold_n, "pool": t.pool_size(), "games": t.games}
                          for tid, t in teams.items()])
    return pd.DataFrame(rows), state, dict(offsets)


def _diffs(a: dict, b: dict, a_blue) -> dict:
    gold_known = (np.asarray(a["gold_n"]) >= MIN_GOLD_GAMES) & \
        (np.asarray(b["gold_n"]) >= MIN_GOLD_GAMES)
    pool_a = np.nan_to_num(np.asarray(a["pool"], float), nan=POOL_FILL)
    pool_b = np.nan_to_num(np.asarray(b["pool"], float), nan=POOL_FILL)
    return {
        "elo_diff": np.asarray(a["elo"]) - np.asarray(b["elo"]),
        "form_diff": np.asarray(a["form"]) - np.asarray(b["form"]),
        "resid_diff": np.asarray(a["resid"]) - np.asarray(b["resid"]),
        "gold_diff": np.where(gold_known, np.asarray(a["gold"]) - np.asarray(b["gold"]), 0.0),
        "pool_diff": pool_a - pool_b,
        "games_diff": np.log1p(np.asarray(a["games"])) - np.log1p(np.asarray(b["games"])),
        "gold_known": gold_known.astype(float),
        "blue": np.broadcast_to(np.asarray(a_blue, float), gold_known.shape).copy(),
    }



def game_matrix(rows: pd.DataFrame, mirror: bool = True) -> tuple[pd.DataFrame, np.ndarray]:
    """Model rows from pre-game features. mirror=True adds the red-side orientation."""
    keys = ["elo", "form", "resid", "gold", "gold_n", "pool", "games"]
    blue = {k: rows[f"b_{k}"].values for k in keys}
    red = {k: rows[f"r_{k}"].values for k in keys}
    X = pd.DataFrame(_diffs(blue, red, 1.0))
    y = rows.blue_win.values
    if mirror:
        X = pd.concat([X, pd.DataFrame(_diffs(red, blue, 0.0))], ignore_index=True)
        y = np.concatenate([y, 1 - y])
    return X[FEATURES], y


def matchup_matrix(state: pd.DataFrame, a_ids, b_ids, a_blue) -> pd.DataFrame:
    """Model rows for arbitrary matchups from a state snapshot."""
    s = state.set_index("teamid")
    a, b = s.loc[list(a_ids)], s.loc[list(b_ids)]
    keys = ["elo", "form", "resid", "gold", "gold_n", "pool", "games"]
    return pd.DataFrame(_diffs({k: a[k].values for k in keys},
                               {k: b[k].values for k in keys}, a_blue))[FEATURES]
