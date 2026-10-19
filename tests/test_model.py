"""Series math, Elo invariants and point-in-time features on synthetic games."""
import numpy as np
import pandas as pd
import pytest

from worlds import features as F
from worlds.series import p_series


def test_series_formulas():
    assert p_series(0.5, 3) == pytest.approx(0.5)
    assert p_series(0.5, 5) == pytest.approx(0.5)
    p = np.linspace(0.01, 0.99, 50)
    for bo in (3, 5):
        s = p_series(p, bo)
        assert np.all(np.diff(s) > 0)                        # monotonic
        assert np.allclose(s + p_series(1 - p, bo), 1)       # symmetric
    fav = p[p > 0.5]
    assert np.all(p_series(fav, 5) > p_series(fav, 3))      # Bo5 sharper than Bo3
    assert np.all(p_series(fav, 3) > fav)


def _inputs(results, years=None, intl=None):
    """results: list of (blue, red, blue_wins). Teams A,B are LCK; C is LPL."""
    n = len(results)
    years = years or [2025] * n
    intl = intl or [False] * n
    games = pd.DataFrame({
        "gameid": [f"g{i}" for i in range(n)],
        "date": pd.date_range("2025-03-01", periods=n, freq="D"),
        "year": years, "league": ["LCK"] * n, "datacompleteness": "complete",
        "is_international": intl, "stage": None, "series_id": range(n), "game_in_series": 1,
        "blue_teamid": [b for b, _, _ in results], "red_teamid": [r for _, r, _ in results],
        "winner_teamid": [b if w else r for b, r, w in results]})
    region = {(t, y): ("LPL" if t == "C" else "LCK") for t in "ABC" for y in (2025, 2026)}
    return {"games": games, "gold": {}, "picks": {}, "region": region}


def test_elo_is_zero_sum_and_offsets_move_only_on_intl():
    res = [("A", "B", True), ("B", "A", True), ("A", "B", True)]
    _, state, offsets = F.build(_inputs(res), F.EloParams(k=24, k_region=20))
    assert state.elo_team.sum() == pytest.approx(0)
    assert all(v == 0 for v in offsets.values())

    _, _, offsets = F.build(_inputs([("A", "C", True)], intl=[True]), F.EloParams(k_region=20))
    assert offsets["LCK"] == pytest.approx(10) and offsets["LPL"] == pytest.approx(-10)


def test_season_regression_formula():
    res = [("A", "B", True)] * 5 + [("A", "B", True)]
    params = F.EloParams(k=24, k_region=0, season_regression=0.5)
    rows, _, _ = F.build(_inputs(res, years=[2025] * 5 + [2026]), params)
    before = F.build(_inputs(res[:5]), params)[1].set_index("teamid").elo_team
    mean = before.mean()
    expected = mean + 0.5 * (before["A"] - mean)
    assert rows.iloc[-1].b_elo == pytest.approx(expected)


def test_features_are_point_in_time():
    res = [("A", "B", True), ("B", "A", False), ("A", "B", True), ("B", "A", True)]
    flipped = res[:-1] + [("B", "A", False)]
    r1, _, _ = F.build(_inputs(res))
    r2, _, _ = F.build(_inputs(flipped))
    cols = [c for c in r1.columns if c[:2] in ("b_", "r_") or c == "p_elo"]
    pd.testing.assert_frame_equal(r1[cols], r2[cols])   # last result can't leak into features
