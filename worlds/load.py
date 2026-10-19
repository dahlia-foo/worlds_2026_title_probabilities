"""Step 1: Oracle's Elixir CSVs -> cleaned SQLite tables.

OE layout: 12 rows per game (10 players + 2 rows with position == "team").
Tables written: games, series, team_games, player_games, teams.
"""
import logging

import pandas as pd

from worlds import db
from worlds.config import EVENT_LEAGUES, LEAGUE_NAMES, RAW_DIR, load_config, region_of

log = logging.getLogger(__name__)

GAME_COLS = ["gameid", "datacompleteness", "league", "split", "playoffs", "date", "game",
             "patch", "gamelength"]
ROW_COLS = ["position", "side", "teamname", "teamid", "result", "kills", "deaths",
            "golddiffat15", "xpdiffat15", "csdiffat15"]
TEAM_COLS = ["firstdragon", "firstbaron", "firsttower", "dragons", "barons", "towers",
             "totalgold"] + [f"ban{i}" for i in range(1, 6)] + [f"pick{i}" for i in range(1, 6)]
PLAYER_COLS = ["playername", "playerid", "champion", "assists", "cspm", "dpm", "damageshare",
               "earnedgoldshare", "vspm"]
STR_COLS = ["gameid", "datacompleteness", "league", "split", "patch", "position", "side",
            "teamname", "teamid", "playername", "playerid", "champion"] + TEAM_COLS[7:]
POSITIONS = ["top", "jng", "mid", "bot", "sup"]
SERIES_GAP = pd.Timedelta(hours=18)


def read_raw(paths=None) -> pd.DataFrame:
    paths = sorted(paths or RAW_DIR.glob("*_oe.csv"))
    if not paths:
        raise FileNotFoundError(f"no *_oe.csv files in {RAW_DIR}")
    cols = GAME_COLS + ROW_COLS + TEAM_COLS + PLAYER_COLS
    frames = []
    for p in paths:
        df = pd.read_csv(p, usecols=cols, dtype={c: "string" for c in STR_COLS},
                         parse_dates=["date"])
        log.info("read %s: %d rows", p.name, len(df))
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _valid_games(df: pd.DataFrame) -> pd.DataFrame:
    """Keep games with exactly 2 team rows (one per side, one winner) and 10 player rows."""
    df = df.drop_duplicates(subset=["gameid", "position", "side"])
    is_team = df.position == "team"
    g = df.assign(is_team=is_team, win=is_team & (df.result == 1)).groupby("gameid").agg(
        n_team=("is_team", "sum"), n=("is_team", "size"), wins=("win", "sum"),
        sides=("side", "nunique"), no_team=("teamkey", lambda s: s.isna().any()))
    ok = (g.n_team == 2) & (g.n == 12) & (g.wins == 1) & (g.sides == 2) & ~g.no_team
    if (~ok).any():
        log.warning("dropping %d malformed games", (~ok).sum())
    return df[df.gameid.isin(g.index[ok])]


def _teams(team_rows: pd.DataFrame) -> pd.DataFrame:
    """One row per (teamid, year): latest name, home league (most-played non-event league), region."""
    t = team_rows.sort_values("date")
    dom = t[~t.league.isin(EVENT_LEAGUES)]
    home = (dom.groupby(["teamkey", "year"]).league
            .agg(lambda s: s.value_counts().index[0]).rename("home_league"))
    out = t.groupby(["teamkey", "year"]).agg(teamname=("teamname", "last"),
                                              games=("gameid", "nunique"))
    out = out.join(home).reset_index()
    # Teams that only played events in a year inherit their home league from another year.
    fallback = out.dropna(subset=["home_league"]).groupby("teamkey").home_league.last()
    out["home_league"] = out.home_league.fillna(out.teamkey.map(fallback))
    out["region"] = out.home_league.map(region_of, na_action="ignore")
    return out.rename(columns={"teamkey": "teamid"})


def _assign_series(games: pd.DataFrame) -> pd.DataFrame:
    """Group games into series: same league + team pair, OE game number increasing, close in time."""
    g = games.sort_values("date").copy()
    g["pair"] = [" | ".join(sorted(p)) for p in zip(g.blue_teamid, g.red_teamid)]
    grp = g.groupby(["league", "pair"], sort=False)
    new = (grp.game.diff().fillna(-1) <= 0) | (grp.date.diff().fillna(SERIES_GAP) >= SERIES_GAP)
    # Number series within each league + pair (a global cumsum would merge interleaved series).
    g["_k"] = new.groupby([g.league, g.pair], sort=False).cumsum()
    g["series_id"] = g.groupby(["league", "pair", "_k"], sort=False).ngroup()

    # Use OE's game number where it's 1..n within the series; otherwise fall back to date order.
    order = g.groupby("series_id").cumcount() + 1
    reliable = g.groupby("series_id").game.transform(lambda s: list(s) == list(range(1, len(s) + 1)))
    g["game_in_series"] = g.game.where(reliable, order)
    n_bad = g.loc[~reliable, "series_id"].nunique()
    log.info("series: %d total, %d with unreliable OE game numbers (date order used)",
             g.series_id.nunique(), n_bad)
    return g.drop(columns=["pair", "_k"])


def _series_table(games: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sid, s in games.groupby("series_id", sort=False):
        a, b = sorted({s.blue_teamid.iloc[0], s.red_teamid.iloc[0]})
        wa, wb = (s.winner_teamid == a).sum(), (s.winner_teamid == b).sum()
        rows.append({"series_id": sid, "league": s.league.iloc[0], "date": s.date.iloc[0],
                     "year": s.year.iloc[0], "is_international": s.is_international.iloc[0],
                     "team_a": a, "team_b": b, "wins_a": wa, "wins_b": wb,
                     "best_of": 2 * max(wa, wb) - 1, "winner": a if wa > wb else b})
    return pd.DataFrame(rows)


def clean(raw: pd.DataFrame, worlds_start: dict) -> dict[str, pd.DataFrame]:
    df = raw.copy()
    df["teamkey"] = df.teamid.fillna("name:" + df.teamname)
    df["year"] = df.date.dt.year
    df = _valid_games(df)

    team_rows = df[df.position == "team"].copy()
    player_rows = df[df.position.isin(POSITIONS)].copy()
    teams = _teams(team_rows)
    region = teams.set_index(["teamid", "year"]).region

    # games: one row per game, blue/red pivoted from the team rows
    blue = team_rows[team_rows.side == "Blue"].set_index("gameid")
    red = team_rows[team_rows.side == "Red"].set_index("gameid")
    games = blue[GAME_COLS[1:] + ["year"]].copy()
    games["blue_teamid"], games["red_teamid"] = blue.teamkey, red.teamkey.reindex(blue.index)
    games["winner_teamid"] = blue.teamkey.where(blue.result == 1, games.red_teamid)
    games = games.reset_index()
    games["league_name"] = games.league.map(LEAGUE_NAMES).fillna(games.league)
    games["patch_key"] = games.patch.astype(float)

    # International = event league + teams from different regions.
    # WLDs before the main event = regional qualifiers.
    start = games.year.map(worlds_start)
    games["stage"] = None
    games.loc[(games.league == "WLDs") & (games.date < start), "stage"] = "qualifier"
    games.loc[(games.league == "WLDs") & (games.date >= start), "stage"] = "main"
    rb = pd.Series(list(zip(games.blue_teamid, games.year))).map(region)
    rr = pd.Series(list(zip(games.red_teamid, games.year))).map(region)
    games["blue_region"], games["red_region"] = rb.values, rr.values
    games["is_international"] = (rb.notna() & rr.notna() & (rb != rr)).values & \
        games.league.isin(EVENT_LEAGUES) & (games.stage.fillna("") != "qualifier")
    games = _assign_series(games)
    series = _series_table(games)

    # team_games: fill missing draft picks from player rows (role order)
    team_games = team_rows.rename(columns={"teamkey": "tid"})
    champs = player_rows.pivot_table(index=["gameid", "side"], columns="position",
                                     values="champion", aggfunc="first")[POSITIONS]
    champs.columns = [f"pick{i}" for i in range(1, 6)]
    tg = team_games.set_index(["gameid", "side"])
    tg[champs.columns] = tg[champs.columns].fillna(champs.reindex(tg.index))
    team_games = tg.reset_index()
    team_games = team_games[["gameid", "tid", "teamname", "side", "result", "kills", "deaths",
                             "golddiffat15", "xpdiffat15", "csdiffat15"] + TEAM_COLS]
    team_games = team_games.rename(columns={"tid": "teamid"})

    player_games = player_rows.rename(columns={"teamkey": "tid"})[
        ["gameid", "tid", "side", "playername", "playerid", "position", "champion", "result",
         "kills", "deaths", "assists", "cspm", "dpm", "damageshare", "earnedgoldshare", "vspm",
         "golddiffat15", "xpdiffat15", "csdiffat15"]].rename(columns={"tid": "teamid"})

    return {"games": games, "series": series, "team_games": team_games,
            "player_games": player_games, "teams": teams}


def check_config_teams(teams: pd.DataFrame, cfg: dict) -> None:
    names = set(teams.teamname)
    for t in cfg["teams"]:
        if t["oe_name"] not in names:
            import difflib
            log.warning("config oe_name %r not found; closest: %s", t["oe_name"],
                        difflib.get_close_matches(t["oe_name"], names, n=3))


def summarize(tables: dict) -> None:
    g, tg, pg = tables["games"], tables["team_games"], tables["player_games"]
    print(f"games={len(g)}  team_games={len(tg)}  player_games={len(pg)}  "
          f"series={len(tables['series'])}  teams={tables['teams'].teamid.nunique()}")
    print(f"date range: {g.date.min():%Y-%m-%d} .. {g.date.max():%Y-%m-%d}")
    print(f"gold@15 available: {tg.golddiffat15.notna().mean():.1%} of team rows")
    print(f"international games: {g.is_international.sum()}")
    by = g.groupby(["league", "year"]).size().unstack(fill_value=0)
    intl = g[g.is_international].groupby("league").size().rename("intl")
    by = by.join(intl).fillna(0).astype(int)
    print("\ngames per league/year (top 25 by total):")
    print(by.assign(total=by.sum(axis=1) - by.intl).nlargest(25, "total").to_string())
    w = g[g.league == "WLDs"].groupby(["year", "stage"]).size()
    print("\nWLDs games by stage:\n" + w.to_string())


def run() -> None:
    cfg = load_config()
    tables = clean(read_raw(), cfg["worlds_start"])
    check_config_teams(tables["teams"], cfg)
    with db.connect() as con:
        for name, df in tables.items():
            db.write_table(con, name, df)
    summarize(tables)
