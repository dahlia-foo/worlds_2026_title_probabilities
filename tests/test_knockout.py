"""Knockout Monte Carlo: probability sums, quarterfinal draw rules, bracket validation."""
import numpy as np
import pandas as pd
import pytest

from worlds.knockout import (chained_brackets, draw_bracket, fixed_bracket, simulate,
                             summarize)
from worlds.series import p_series

NAMES = [f"T{i}" for i in range(16)]


def _p5(strength):
    s = np.asarray(strength, float)
    p = 1 / (1 + np.exp(-(s[:, None] - s[None, :])))
    np.fill_diagonal(p, 0.5)
    return p_series(p, 5)


P5 = _p5(np.linspace(1.5, -1.5, 16))
# Swiss qualifiers: T0,T1 went 3-0; T2-T4 3-1; T5-T7 3-2
QUALIFIERS = [(0, 0), (1, 0), (2, 1), (3, 1), (4, 1), (5, 2), (6, 2), (7, 2)]


def _swiss_sims(n_runs, rng):
    """Fake swiss_sims rows: random 8 qualifiers per run with a legal record split."""
    rows = []
    for s in range(n_runs):
        order = rng.permutation(16)
        losses = [0, 0, 1, 1, 1, 2, 2, 2]
        for k, t in enumerate(order):
            if k < 8:
                rows.append((s, NAMES[t], 3, losses[k]))
            else:
                rows.append((s, NAMES[t], [2, 2, 2, 1, 1, 1, 0, 0][k - 8], 3))
    return pd.DataFrame(rows, columns=["sim", "team", "wins", "losses"])


def test_title_probabilities_sum_to_one_chained():
    rng = np.random.default_rng(0)
    sims = _swiss_sims(500, rng)
    counts, n = simulate(chained_brackets(NAMES, sims, rng, 3), 16, P5, rng)
    s = summarize(NAMES, counts, n)
    assert n == 500
    assert s.p_title.sum() == pytest.approx(1.0)
    assert s.p_final.sum() == pytest.approx(2.0)
    assert s.p_semis.sum() == pytest.approx(4.0)
    assert s.p_knockout.sum() == pytest.approx(8.0)
    assert (s.p_title <= s.p_final).all() and (s.p_final <= s.p_semis).all()
    assert (s.p_semis <= s.p_knockout).all()


def test_title_probabilities_sum_to_one_fixed():
    cfg = {"knockout": {"bracket": [["T0", "T7"], ["T3", "T4"], ["T1", "T6"], ["T2", "T5"]]}}
    bracket = fixed_bracket(NAMES, cfg)
    rng = np.random.default_rng(1)
    counts, n = simulate((bracket for _ in range(2000)), 16, P5, rng)
    s = summarize(NAMES, counts, n).set_index("team")
    assert s.p_title.sum() == pytest.approx(1.0)
    assert s.loc[["T0", "T1", "T2", "T3", "T4", "T5", "T6", "T7"], "p_knockout"].eq(1).all()
    assert s.drop(index=[f"T{i}" for i in range(8)]).p_knockout.eq(0).all()
    # T0 and T3 share a semifinal path; T0 and T1 can only meet in the final
    assert s.loc["T0", "p_title"] > s.loc["T7", "p_title"]


def test_draw_bracket_rules():
    rng = np.random.default_rng(2)
    losses = dict(QUALIFIERS)
    for _ in range(300):
        b = draw_bracket(QUALIFIERS, rng)
        assert sorted(t for pair in b for t in pair) == list(range(8))
        recs = [tuple(sorted((losses[a], losses[c]))) for a, c in b]
        # 3-0 teams lead pair 1 (top half) and pair 3 (bottom half), each vs a 3-2 team
        assert recs[0] == (0, 2) and recs[2] == (0, 2)
        assert sorted([recs[1], recs[3]]) == [(1, 1), (1, 2)]


def test_draw_bracket_rejects_bad_record_split():
    bad = [(0, 0), (1, 0), (2, 0), (3, 1), (4, 1), (5, 2), (6, 2), (7, 2)]
    with pytest.raises(ValueError, match="expected two 3-0"):
        draw_bracket(bad, np.random.default_rng(0))


def test_strongest_team_always_wins_with_certain_outcomes():
    p5 = _p5(np.arange(16, 0, -1) * 25.0)
    rng = np.random.default_rng(3)
    counts, n = simulate((draw_bracket(QUALIFIERS, rng) for _ in range(50)), 16, p5, rng)
    assert counts[3, 0] == n


@pytest.mark.parametrize("bracket, msg", [
    (None, "needs config knockout.bracket"),
    ([["T0", "T1"], ["T2", "T3"], ["T4", "T5"]], "4 pairs"),
    ([["T0", "T1"], ["T2", "T3"], ["T4", "T5"], ["T6", "Nobody"]], "unknown teams"),
    ([["T0", "T1"], ["T2", "T3"], ["T4", "T5"], ["T6", "T0"]], "more than once"),
])
def test_fixed_bracket_validation(bracket, msg):
    cfg = {"knockout": {"bracket": bracket} if bracket else {}}
    with pytest.raises(ValueError, match=msg):
        fixed_bracket(NAMES, cfg)
