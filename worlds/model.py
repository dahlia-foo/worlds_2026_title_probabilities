"""Step 3: game win-probability models + Worlds 2025 backtest.

1. Tune Elo K / region K on pre-Worlds-2025 games; report the season-regression effect on early 2026.
2. Backtest on Worlds 2025 main event with team state frozen at the cutoff:
   Elo-only, logistic regression (Elo + features), XGBoost.
3. Refit the chosen model on all data and save it with the current team state.
"""
import itertools
import logging

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from worlds import db, plots
from worlds import features as F
from worlds.config import PROCESSED_DIR, load_config
from worlds.plots import plt
from worlds.series import p_series

log = logging.getLogger(__name__)

MODEL_PATH = PROCESSED_DIR / "model.joblib"
TRAIN_START = pd.Timestamp("2025-03-01")   # ~2 months of Elo burn-in
K_GRID = [24, 32, 48]
K_REGION_GRID = [0, 10, 20]
REGRESSION_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]
MONOTONE = {"elo_diff": 1, "form_diff": 1, "resid_diff": 1, "gold_diff": 1}


def _scope(rows: pd.DataFrame, cfg: dict) -> pd.Series:
    """Major-league + cross-region games: the models train and are scored on these only."""
    return rows.league.isin(cfg["major_leagues"]) | rows.is_international


def _ll(y, p):
    return log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])


# ---------- models ----------

class GameModel:
    """Wraps a classifier on F.FEATURES; enforces symmetry p(A,B) = 1 - p(B,A)."""

    def __init__(self, kind: str):
        self.kind = kind
        self.clf = None

    def fit(self, X: pd.DataFrame, y: np.ndarray, dates: pd.Series | None = None):
        if self.kind == "elo":
            return self
        if self.kind == "lr_elo":
            self.cols = ["elo_diff", "blue"]
            self.clf = make_pipeline(StandardScaler(), LogisticRegression(C=1.0))
            self.clf.fit(X[self.cols], y)
        elif self.kind == "lr":
            self.cols = F.FEATURES
            self.clf = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000))
            self.clf.fit(X[self.cols], y)
        elif self.kind == "xgb":
            self.cols = F.FEATURES
            # time-based validation split (last 15% of dates) for early stopping
            cut = dates.quantile(0.85)
            tr, va = (dates < cut).values, (dates >= cut).values
            self.clf = XGBClassifier(
                n_estimators=2000, learning_rate=0.02, max_depth=3, min_child_weight=20,
                subsample=0.8, colsample_bytree=0.8, reg_lambda=5.0, eval_metric="logloss",
                early_stopping_rounds=100,
                monotone_constraints=tuple(MONOTONE.get(c, 0) for c in self.cols))
            self.clf.fit(X[tr][self.cols], y[tr], eval_set=[(X[va][self.cols], y[va])],
                         verbose=False)
            log.info("xgb: best iteration %d", self.clf.best_iteration)
        return self

    def _raw(self, X):
        if self.kind == "elo":
            return 1 / (1 + 10 ** (-X.elo_diff.values / 400))
        return self.clf.predict_proba(X[self.cols])[:, 1]

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        flipped = X.copy()
        for c in ["elo_diff", "form_diff", "resid_diff", "gold_diff", "pool_diff", "games_diff"]:
            flipped[c] = -X[c]
        flipped["blue"] = 1 - X["blue"]
        return 0.5 * (self._raw(X) + 1 - self._raw(flipped))


def predict_neutral(model: GameModel, state: pd.DataFrame, a_ids, b_ids) -> np.ndarray:
    """P(A beats B in one game), averaged over sides (side alternates within a series)."""
    pa = model.predict(F.matchup_matrix(state, a_ids, b_ids, 1.0))
    pb = model.predict(F.matchup_matrix(state, a_ids, b_ids, 0.0))
    return 0.5 * (pa + pb)


# ---------- Elo tuning ----------

def tune_elo(inputs: dict, cfg: dict, cutoff: pd.Timestamp) -> tuple[F.EloParams, pd.DataFrame]:
    """Grid over K and region K; score = Elo log loss on major/intl games Jun 2025 -> cutoff."""
    res = []
    for k, kr in itertools.product(K_GRID, K_REGION_GRID):
        params = F.EloParams(k=k, k_region=kr, season_regression=cfg["season_regression"])
        rows, _, _ = F.build(inputs, params, until=cutoff)
        m = rows[_scope(rows, cfg) & (rows.date >= "2025-06-01")]
        i = m[m.is_international]
        res.append({"k": k, "k_region": kr, "logloss": _ll(m.blue_win, m.p_elo),
                    "intl_logloss": _ll(i.blue_win, i.p_elo), "n": len(m), "n_intl": len(i)})
    # near-ties on overall log loss are broken by international log loss: Worlds is cross-region
    res = pd.DataFrame(res)
    res = res.assign(_r=res.logloss.round(3)).sort_values(["_r", "intl_logloss"]).drop(columns="_r")
    best = res.iloc[0]
    return F.EloParams(k=best.k, k_region=best.k_region,
                       season_regression=cfg["season_regression"]), res


def regression_check(inputs: dict, cfg: dict, params: F.EloParams) -> pd.DataFrame:
    """Season regression only matters after the 2025->2026 boundary: score early-2026 games."""
    res = []
    for r in REGRESSION_GRID:
        p = F.EloParams(k=params.k, k_region=params.k_region, season_regression=r)
        rows, _, _ = F.build(inputs, p, until="2026-04-01")
        m = rows[_scope(rows, cfg) & (rows.year == 2026)]
        res.append({"season_regression": r, "logloss_jan_mar_2026": _ll(m.blue_win, m.p_elo),
                    "n": len(m)})
    return pd.DataFrame(res)


def fearless_check(rows: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Does the pre-series favourite win later games of a series as often as game 1?"""
    series = db.read("SELECT series_id, best_of FROM series", parse_dates=())
    r = rows[rows.league.isin(cfg["major_leagues"]) & (rows.year == 2026)].merge(series)
    r = r[r.best_of > 1]
    first = r[r.game_in_series == 1].set_index("series_id")
    fav = pd.Series(np.where(first.b_elo >= first.r_elo, first.blue_teamid, first.red_teamid),
                    index=first.index, name="fav")
    r = r.join(fav, on="series_id").dropna(subset=["fav"])
    r["fav_win"] = np.where(r.blue_win == 1, r.blue_teamid, r.red_teamid) == r.fav
    return r.groupby("game_in_series").fav_win.agg(["mean", "size"])


# ---------- backtest ----------

def backtest(inputs: dict, cfg: dict, params: F.EloParams, cutoff: pd.Timestamp):
    rows, _, _ = F.build(inputs, params)
    pre_state = F.build(inputs, params, until=cutoff)[1]
    train = rows[(rows.date >= TRAIN_START) & (rows.date < cutoff) & _scope(rows, cfg)]
    X, y = F.game_matrix(train)
    dates = pd.concat([train.date, train.date], ignore_index=True)

    test = rows[(rows.league == "WLDs") & (rows.stage == "main") & (rows.year == cutoff.year)]
    # frozen features: each team's state at the cutoff, not its in-tournament updates
    Xt = F.matchup_matrix(pre_state, test.blue_teamid, test.red_teamid, 1.0)
    yt = test.blue_win.values

    series = db.read("SELECT * FROM series", parse_dates=())
    ts = series[series.series_id.isin(test.series_id)]

    models, preds, out = {}, {}, []
    for kind in ["elo", "lr_elo", "lr", "xgb"]:
        m = GameModel(kind).fit(X, y, dates)
        p = m.predict(Xt)
        q = predict_neutral(m, pre_state, ts.team_a, ts.team_b)
        ps = np.array([p_series(qi, bo) for qi, bo in zip(q, ts.best_of)])
        ys = (ts.winner == ts.team_a).astype(int).values
        models[kind], preds[kind] = m, p
        out.append({"model": kind, "logloss": _ll(yt, p), "brier": brier_score_loss(yt, p),
                    "accuracy": ((p > 0.5) == yt).mean(), "series_logloss": _ll(ys, ps),
                    "series_accuracy": ((ps > 0.5) == ys).mean()})
    out.append({"model": "coin_flip", "logloss": np.log(2), "brier": 0.25,
                "accuracy": np.nan, "series_logloss": np.log(2), "series_accuracy": np.nan})
    return pd.DataFrame(out), preds, yt, len(train), len(ts)


def plot_calibration(preds: dict, y: np.ndarray, n_bins: int = 5) -> None:
    labels = {"elo": "Elo only", "lr": "Logistic regression", "xgb": "XGBoost"}
    fig, ax = plt.subplots(figsize=(5.5, 5.2))
    ax.plot([0, 1], [0, 1], color=plots.GRID, lw=1.5, ls="--", zorder=1)
    for i, (kind, label) in enumerate(labels.items()):
        p = preds[kind]
        bins = pd.qcut(p, n_bins, labels=False, duplicates="drop")
        d = pd.DataFrame({"p": p, "y": y, "bin": bins}).groupby("bin").mean()
        ax.plot(d.p, d.y, marker="o", ms=6, lw=2, color=plots.SERIES[i], label=label, zorder=3)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted P(blue side wins)")
    ax.set_ylabel("Observed blue win rate")
    ax.grid(True); ax.set_axisbelow(True)
    ax.legend(loc="upper left")
    ax.set_title("Worlds 2025 backtest: calibration")
    fig.text(0.01, -0.03, f"{len(y)} main-event games, team state frozen at 2025-10-14. "
             f"{n_bins} equal-count bins (~{len(y) // n_bins} games each).",
             fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "backtest_calibration.png")


def plot_ratings(state: pd.DataFrame, cfg: dict) -> None:
    from worlds.champions import resolve_teams
    t = resolve_teams(cfg).merge(state, on="teamid", suffixes=("", "_db"))
    t = t.sort_values("elo")
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    y = np.arange(len(t))
    ax.barh(y, t.elo, color=plots.SERIES[0], height=0.65)
    for yi, r in zip(y, t.itertuples()):
        ax.text(max(r.elo, 0) + 8, yi, f"{r.elo:.0f}  ({r.region} {r.region_offset:+.0f})",
                va="center", fontsize=8, color=plots.TEXT_2)
    ax.axvline(0, color=plots.TEXT_2, lw=0.8)
    ax.set_yticks(y, t.name)
    ax.set_xlim(min(0, t.elo.min() * 1.3), t.elo.max() * 1.3)
    ax.grid(axis="x"); ax.set_axisbelow(True)
    ax.set_xlabel("Elo rating (team + region offset)")
    ax.set_title("Worlds 2026 teams: current Elo")
    fig.text(0.01, -0.02, "Region offset in brackets, learned from cross-region games. "
             "As of the last game in the data.", fontsize=8, color=plots.TEXT_2)
    plots.save(fig, "elo_worlds_teams.png")


# ---------- final model ----------

def load_predictor() -> dict:
    """{'model': GameModel, 'state': team snapshot, ...} saved by `python -m worlds model`."""
    return joblib.load(MODEL_PATH)


def run() -> None:
    cfg = load_config()
    cutoff = cfg["worlds_start"][2025]
    inputs = F.load_inputs()

    params, grid = tune_elo(inputs, cfg, cutoff)
    print("Elo tuning (log loss, major+intl games 2025-06-01 .. cutoff):")
    print(grid.to_string(index=False, float_format="{:.4f}".format))
    reg = regression_check(inputs, cfg, params)
    print("\nSeason regression check (Elo log loss, major+intl games Jan-Mar 2026):")
    print(reg.to_string(index=False, float_format="{:.4f}".format))

    res, preds, yt, n_train, n_series = backtest(inputs, cfg, params, cutoff)
    print(f"\nWorlds 2025 backtest: {len(yt)} games, {n_series} series, "
          f"trained on {n_train} games before {cutoff:%Y-%m-%d}")
    print(res.to_string(index=False, float_format="{:.4f}".format))
    plot_calibration(preds, yt)

    best = res[res.model.isin(["lr", "xgb"])].sort_values("logloss").iloc[0].model
    rows, state, offsets = F.build(inputs, params)
    print("\nFearless check, 2026 major-league Bo3/Bo5: pre-series favourite win rate by game")
    print(fearless_check(rows, cfg).to_string(float_format="{:.3f}".format))

    train = rows[(rows.date >= TRAIN_START) & _scope(rows, cfg)]
    X, y = F.game_matrix(train)
    model = GameModel(best).fit(X, y, pd.concat([train.date, train.date], ignore_index=True))
    joblib.dump({"model": model, "kind": best, "state": state, "params": params,
                 "region_offsets": offsets, "as_of": rows.date.max()}, MODEL_PATH)
    with db.connect() as con:
        db.write_table(con, "team_state", state)
        db.write_table(con, "game_features", rows)
        db.write_table(con, "backtest_results", res)
    print(f"\nsaved {MODEL_PATH.relative_to(PROCESSED_DIR.parent.parent)} "
          f"(model={best}, Elo k={params.k:g}, k_region={params.k_region:g})")
    plot_ratings(state, cfg)
