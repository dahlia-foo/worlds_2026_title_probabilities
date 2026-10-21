"""Swiss Monte Carlo invariants and conditioning, on a synthetic 16-team field."""
from collections import Counter

import numpy as np
import pytest

from worlds.swiss import best_of, simulate_swiss, validate_results

RULES = {"wins_to_advance": 3, "losses_to_eliminate": 3, "bo1_rounds": [1, 2],
         "no_rematches": True, "same_region_allowed_from_round": 2}
NAMES = [f"T{i}" for i in range(16)]
REGION = ["A", "B", "C", "D"] * 4
ROUND1 = [[f"T{i}", f"T{i + 8}"] for i in range(8)]


def _p(strength):
    s = np.asarray(strength, float)
    p = 1 / (1 + np.exp(-(s[:, None] - s[None, :])))
    np.fill_diagonal(p, 0.5)
    return p


def _sim(results=(), n=400, strength=None, seed=1):
    by_round = validate_results(NAMES, ROUND1, list(results), RULES)
    p = _p(strength if strength is not None else np.linspace(1, -1, 16))
    return simulate_swiss(NAMES, REGION, ROUND1, by_round, p, RULES, n, seed)


def test_swiss_invariants():
    wins, losses, opps = _sim()
    for s in range(len(wins)):
        rec = Counter(zip(wins[s], losses[s]))
        assert rec == {(3, 0): 2, (3, 1): 3, (3, 2): 3, (2, 3): 3, (1, 3): 3, (0, 3): 2}
        assert (wins[s] >= 3).sum() == 8
        for i, o in enumerate(opps[s]):
            assert len(o) == wins[s, i] + losses[s, i] <= 5
            assert len(set(o)) == len(o)                     # no rematches


def test_best_of_rules():
    assert best_of(1, 0, 0, RULES) == 1 and best_of(2, 1, 0, RULES) == 1   # Bo1 rounds
    assert best_of(3, 2, 0, RULES) == 3 and best_of(3, 0, 2, RULES) == 3   # advance / eliminate
    assert best_of(3, 1, 1, RULES) == 1                                    # 1-1 stays Bo1
    assert best_of(4, 2, 1, RULES) == 3 and best_of(4, 1, 2, RULES) == 3
    assert best_of(5, 2, 2, RULES) == 3


def test_deterministic_with_certain_outcomes():
    # huge strength gaps: the strongest team always goes 3-0
    wins, losses, _ = _sim(strength=np.arange(16, 0, -1) * 25.0, n=50)
    assert np.all(wins[:, 0] == 3) and np.all(losses[:, 0] == 0)


def test_conditioning_on_results():
    r1 = [{"round": 1, "winner": f"T{i + 8}", "loser": f"T{i}"} for i in range(8)]
    r2 = [{"round": 2, "winner": "T8", "loser": "T9"}]
    wins, losses, opps = _sim(r1 + r2, n=200)
    # round 1 upsets are forced: T0..T7 all start 0-1, T8..T15 start 1-0
    assert all(opps[s][i][0] == i + 8 for s in range(len(wins)) for i in range(8))
    assert np.all(losses[:, :8] >= 1) and np.all(wins[:, 8:] >= 1)
    # the round-2 result is played in every simulation, with the given winner
    assert all(NAMES.index("T9") == opps[s][8][1] for s in range(len(wins)))
    assert np.all(wins[:, 8] >= 2)


@pytest.mark.parametrize("results, msg", [
    ([{"round": 1, "winner": "T0", "loser": "T1"}], "not a round1 pair"),
    ([{"round": 1, "winner": "T0", "loser": "Nobody"}], "unknown team"),
    ([{"round": 2, "winner": "T0", "loser": "T1"}], "has only 0 of 8"),
    ([{"round": 1, "winner": "T0", "loser": "T8"}, {"round": 1, "winner": "T1", "loser": "T9"},
      {"round": 1, "winner": "T2", "loser": "T10"}, {"round": 1, "winner": "T3", "loser": "T11"},
      {"round": 1, "winner": "T4", "loser": "T12"}, {"round": 1, "winner": "T5", "loser": "T13"},
      {"round": 1, "winner": "T6", "loser": "T14"}, {"round": 1, "winner": "T7", "loser": "T15"},
      {"round": 2, "winner": "T0", "loser": "T8"}], "different records"),
])
def test_invalid_results_raise(results, msg):
    with pytest.raises(ValueError, match=msg):
        validate_results(NAMES, ROUND1, results, RULES)
