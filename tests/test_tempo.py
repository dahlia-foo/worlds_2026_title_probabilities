"""Tempo labels, comp scoring, archetypes, Swiss window and verdict rule."""
import numpy as np
import pandas as pd
import pytest

from worlds.tempo import comp_frame, in_window, load_labels, verdict

LABELS_CFG = {
    "top": {"accel": ["Jayce"], "brake": ["K'Sante"]},
    "jungle": {"accel": ["Lee Sin"], "flex": ["Naafiri"], "spike": ["Vi"]},
    "mid": {"accel": ["Ryze"], "brake": ["Galio"]},
    "bot": {"accel": ["Ezreal"], "spike": ["Jinx"]},
    "support": {"accel": ["Bard"], "brake": ["Nautilus"]},
}


def test_labels_map_roles_and_list_mismatches():
    labels, missing = load_labels(LABELS_CFG, known_champions={"Jayce", "K'Sante", "Lee Sin",
                                                               "Vi", "Ryze", "Galio", "Ezreal",
                                                               "Jinx", "Bard", "Nautilus"})
    assert labels[("jng", "Lee Sin")] == 2 and labels[("sup", "Nautilus")] == 0
    assert labels[("jng", "Vi")] == -1
    assert missing == [("jungle", "flex", "Naafiri")]


def test_conflicting_label_in_same_role_is_an_error():
    bad = {"top": {"accel": ["Jayce"], "brake": ["Jayce"]}}
    with pytest.raises(ValueError, match="two labels"):
        load_labels(bad)


def _picks(game, team, champs):
    return [{"gameid": game, "teamid": team, "position": p, "champion": c}
            for p, c in zip(["top", "jng", "mid", "bot", "sup"], champs)]


def test_comp_score_and_archetypes():
    labels, _ = load_labels(LABELS_CFG)
    picks = pd.DataFrame(
        _picks("g1", "A", ["Jayce", "Lee Sin", "Ryze", "Ezreal", "Bard"])          # all accel
        + _picks("g1", "B", ["K'Sante", "Vi", "Galio", "Ezreal", "Nautilus"])      # archetype B
        + _picks("g2", "C", ["Jayce", "Naafiri", "Galio", "Unknown", "Unknown"]))  # 3 labeled
    c = comp_frame(picks, labels).set_index("teamid")
    assert c.loc["A", "tempo"] == pytest.approx(2.0)
    assert c.loc["A", "archetype"].startswith("A:")
    assert c.loc["B", "tempo"] == pytest.approx((0 - 1 + 0 + 2 + 0) / 5)
    assert c.loc["B", "archetype"].startswith("B:")
    assert np.isnan(c.loc["C", "tempo"])                  # fewer than 4 labeled picks
    assert c.loc["C", "archetype"] == "Other"


def test_window_end_is_exclusive():
    dates = pd.Series(pd.to_datetime(["2026-10-22 23:59", "2026-10-23 00:00",
                                      "2026-11-01 23:59", "2026-11-02 00:00"]))
    w = {"start": "2026-10-23", "end": "2026-11-02"}
    assert in_window(dates, w).tolist() == [False, True, True, False]


def test_verdict_rule():
    assert verdict(0.1, 0.5, 1) == "consistent with"
    assert verdict(-0.5, -0.1, 1) == "not consistent with"
    assert verdict(-0.1, 0.5, 1) == "too few games to tell"
    assert verdict(-0.5, -0.1, -1) == "consistent with"


def test_h6b_counts_and_classification():
    from worlds.tempo import h6b, jungle_type
    assert [jungle_type(x) for x in (2, 1, 0, -1, -2, np.nan)] == \
        ["accel", "accel", "other", "spike", "spike", "other"]
    # g1, g2: accel vs spike jungle; g3: brake vs spike (not a mixed game)
    t = pd.DataFrame({
        "gameid": ["g1", "g1", "g2", "g2", "g3", "g3"],
        "teamid": ["A", "B", "A", "B", "C", "D"],
        "lab_jng": [2, -1, 1, -2, 0, -1],
        "win": [0, 1, 1, 0, 1, 0],
        "p": [0.5] * 6,
        "golddiffat15": [1500, -1500, 1200, -1200, 2000, -2000],
        "golddiffat20": [500, -500, 1100, -1100, np.nan, np.nan],
        "golddiffat25": [np.nan] * 6,
    })
    rows = pd.DataFrame(h6b(t, np.random.default_rng(0), lead=1000)).set_index(["group", "metric"])
    side = "accel/flex jungle side vs spike/hyperscale"
    assert rows.loc[(side, "win rate"), "n"] == 2
    assert rows.loc[(side, "win rate"), "estimate"] == pytest.approx(0.5)
    assert rows.loc[(side, "team gold diff @15"), "estimate"] == pytest.approx(1350)
    # accel teams led by >=1k at 15 twice and converted once; every spike team trailed
    assert rows.loc[("accel jungle, leading >=1k @15", "win rate"), "n"] == 2
    assert rows.loc[("accel jungle, leading >=1k @15", "win rate"), "estimate"] == 0.5
    assert rows.loc[("spike jungle, trailing >=1k @15", "win rate"), "n"] == 3
    assert rows.loc[("other jungle, leading >=1k @15", "win rate"), "n"] == 1
