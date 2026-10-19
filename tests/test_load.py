"""Step 1 cleaning on a tiny synthetic OE-format fixture (never the real CSV)."""
import pandas as pd

from worlds.load import PLAYER_COLS, POSITIONS, TEAM_COLS, clean

WORLDS_START = {2026: pd.Timestamp("2026-10-15")}


def _game(gameid, date, game, league, blue, red, blue_wins, n_players=10):
    rows = []
    for side, team, won in [("Blue", blue, blue_wins), ("Red", red, not blue_wins)]:
        base = dict.fromkeys(TEAM_COLS + PLAYER_COLS) | dict(
            gameid=gameid, datacompleteness="complete", league=league, split="Summer",
            playoffs=0, date=pd.Timestamp(date), game=game, patch="16.20",
            gamelength=1800, side=side, teamname=team, teamid=f"id:{team}",
            result=int(won), kills=10, deaths=5, golddiffat15=500.0,
            xpdiffat15=0.0, csdiffat15=0.0)
        rows.append({**base, "position": "team"})
        for i, pos in enumerate(POSITIONS):
            rows.append({**base, "position": pos, "champion": f"{team}-champ{i}",
                         "playername": f"{team}-{pos}"})
    return rows[: 2 + n_players]  # truncating drops player rows -> malformed game


def _fixture():
    rows = (_game("g1", "2026-06-01 10:00", 1, "LCK", "T1", "Gen.G", True)
            + _game("g2", "2026-06-01 11:00", 2, "LCK", "Gen.G", "T1", True)
            + _game("g3", "2026-06-08 10:00", 1, "LCK", "T1", "Gen.G", False)
            + _game("bad", "2026-06-09 10:00", 1, "LCK", "T1", "Gen.G", True, n_players=9))
    return pd.DataFrame(rows)


def test_clean_counts_winner_and_series():
    t = clean(_fixture(), WORLDS_START)
    g = t["games"].set_index("gameid")

    assert list(g.index.sort_values()) == ["g1", "g2", "g3"]  # malformed game dropped
    assert len(t["team_games"]) == 6 and len(t["player_games"]) == 30
    assert g.loc["g1", "winner_teamid"] == "id:T1"
    assert g.loc["g2", "winner_teamid"] == "id:Gen.G"

    # g1+g2 form one series (side swap, same day); g3 a week later is a new one
    assert g.loc["g1", "series_id"] == g.loc["g2", "series_id"] != g.loc["g3", "series_id"]
    assert list(g.loc[["g1", "g2"], "game_in_series"]) == [1, 2]
    assert not g.is_international.any()

    # missing team-row picks are filled from player champions in role order
    tg = t["team_games"].set_index(["gameid", "side"])
    assert tg.loc[("g1", "Blue"), "pick1"] == "T1-champ0"
