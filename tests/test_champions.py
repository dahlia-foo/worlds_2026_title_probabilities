import numpy as np

from worlds.champions import wilson


def test_wilson_bounds_contain_estimate_and_narrow_with_n():
    lo, hi = wilson(np.array([5, 50]), np.array([10, 100]))
    assert np.all(lo < 0.5) and np.all(hi > 0.5)
    assert (hi[1] - lo[1]) < (hi[0] - lo[0])


def test_wilson_zero_games_is_nan():
    lo, hi = wilson(np.array([0]), np.array([0]))
    assert np.isnan(lo[0]) and np.isnan(hi[0])
