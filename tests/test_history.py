"""Odds-history snapshots: replace-not-duplicate, order kept; Swiss freshness fingerprint."""
from datetime import datetime

import pandas as pd

from worlds.history import record_snapshot
from worlds.swiss import inputs_fingerprint


def _summary(p_title):
    teams = [f"T{i}" for i in range(len(p_title))]
    return pd.DataFrame({"team": teams, "p_knockout": 1.0, "p_semis": 0.5, "p_final": 0.25,
                         "p_title": p_title})


def test_same_label_twice_replaces_rows(tmp_path):
    path = tmp_path / "odds_history.csv"
    record_snapshot("pre-swiss", _summary([0.6, 0.4]), 0, path, datetime(2026, 10, 21))
    record_snapshot("pre-swiss", _summary([0.7, 0.3]), 0, path, datetime(2026, 10, 22))
    h = pd.read_csv(path)
    assert len(h) == 2                                     # no duplicate rows
    assert not h.duplicated(["label", "team"]).any()
    assert h.p_title.tolist() == [0.7, 0.3]                # latest run wins
    assert h.timestamp.iloc[0].startswith("2026-10-22")


def test_replacing_a_label_keeps_snapshot_order(tmp_path):
    path = tmp_path / "odds_history.csv"
    for label, n in [("pre-swiss", 0), ("swiss-r1", 8), ("swiss-r2", 16)]:
        record_snapshot(label, _summary([0.5, 0.5]), n, path)
    record_snapshot("swiss-r1", _summary([0.9, 0.1]), 8, path)
    h = pd.read_csv(path)
    assert list(dict.fromkeys(h.label)) == ["pre-swiss", "swiss-r1", "swiss-r2"]
    assert len(h) == 6
    assert h[h.label == "swiss-r1"].p_title.tolist() == [0.9, 0.1]


def test_fingerprint_changes_with_results():
    base = {"teams": [], "round1": [], "results": [], "swiss": {}, "n_sims": 10, "seed": 1}
    played = base | {"results": [{"round": 1, "winner": "A", "loser": "B"}]}
    assert inputs_fingerprint(base) == inputs_fingerprint(dict(base))
    assert inputs_fingerprint(base) != inputs_fingerprint(played)
