"""SQLite helpers."""
import sqlite3

import pandas as pd

from worlds.config import DB_PATH

INDEXES = {
    "games": ["gameid", "date", "series_id"],
    "team_games": ["gameid", "teamid"],
    "player_games": ["gameid", "teamid", "champion"],
    "teams": ["teamid"],
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def write_table(con: sqlite3.Connection, name: str, df: pd.DataFrame) -> None:
    df.to_sql(name, con, if_exists="replace", index=False)
    for col in INDEXES.get(name, []):
        con.execute(f"CREATE INDEX IF NOT EXISTS ix_{name}_{col} ON {name}({col})")
    con.commit()


def read(sql: str, params=None, parse_dates=("date",)) -> pd.DataFrame:
    with connect() as con:
        df = pd.read_sql(sql, con, params=params)
    for col in parse_dates:
        if col in df:
            df[col] = pd.to_datetime(df[col])
    return df
