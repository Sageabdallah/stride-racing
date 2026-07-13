"""
LambdaRank winner-ranking model (evidence-first; no pipeline hook yet).

Why: the ensemble's base learners are pointwise binary classifiers, but
picking a winner is a within-race ranking problem. Pairwise learning-to-rank
(LambdaMART-family) trained with races as query groups directly optimises
"put the winner first" and has been reported to beat pointwise learning on
this exact task (see docs/12-hit-rate-research.md §2.4).

Scope discipline: this module produces *evidence*, not behaviour change.
It reuses retrain_v2's exact data loading and 113-column feature matrix
(so there is no second feature path to drift) and a walk-forward split with
the same 60/14/14/14-day parameters (purge gap included — no leakage), then
reports the ranker's top-pick hit rate against the market-favourite baseline
fold by fold. Nothing in the live pipeline consumes the artifact until that
report earns integration (the criterion is written in docs/12 §5.4).

Usage:
    python rank_model.py              # synthetic self-test (no DB, no repo deps)
    python rank_model.py --train      # train + walk-forward report from
                                      # training_view_v2 (needs DATABASE_URL);
                                      # saves models/rank_model_v1.pkl

The train-rank-model GitHub Action runs --train next to the database and
uploads the artifact.
"""

import argparse
import os
import pickle
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = SCRIPT_DIR / "models" / "rank_model_v1.pkl"

# Mirrors retrain_v2.DateWindowSplitter parameters (60/14/14/14) so the
# ranker's evidence is produced under the same temporal regime as the
# production trainer. Splitting is done at RACE granularity: a race is
# either wholly train or wholly test, with a purge gap between them.
MIN_TRAIN_DAYS = 60
PURGE_GAP_DAYS = 14
TEST_WINDOW_DAYS = 14
STEP_DAYS = 14

RANKER_PARAMS = {
    "objective": "lambdarank",
    "n_estimators": 300,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "max_depth": 6,
    "min_child_samples": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    # binary relevance: winner=1, loser=0 — explicit gain avoids LightGBM's
    # default exponential label_gain table
    "label_gain": [0, 1],
    "random_state": 42,
    "verbosity": -1,
}


def _lgb():
    import lightgbm as lgb
    return lgb


# ----------------------------------------------------------------- training
def train_ranker(X, y, group_sizes, params=None):
    """
    Fit an LGBMRanker. X: feature matrix with rows ordered race-by-race;
    y: binary winner labels; group_sizes: runners per race in row order.
    """
    lgb = _lgb()
    p = dict(RANKER_PARAMS)
    if params:
        p.update(params)
    model = lgb.LGBMRanker(**p)
    model.fit(X, y, group=group_sizes)
    return model


def _temporal_folds(race_dates):
    """
    Yield (train_race_idx, test_race_idx) over races ordered by date, with a
    purge gap between train end and test start. race_dates: one date per
    race (datetime.date), ascending.
    """
    if not race_dates:
        return
    start, end = race_dates[0], race_dates[-1]
    train_end = start + timedelta(days=MIN_TRAIN_DAYS)
    while True:
        test_start = train_end + timedelta(days=PURGE_GAP_DAYS)
        test_end = test_start + timedelta(days=TEST_WINDOW_DAYS)
        if test_start > end:
            break
        train_idx = [i for i, d in enumerate(race_dates) if d < train_end]
        test_idx = [i for i, d in enumerate(race_dates)
                    if test_start <= d < test_end]
        if len(train_idx) >= 30 and len(test_idx) >= 5:
            yield train_idx, test_idx
        train_end = train_end + timedelta(days=STEP_DAYS)


def walk_forward_report(races, params=None, verbose=True):
    """
    races: list of dicts ordered by date, each:
      {"date": date, "X": 2D array (runners x features),
       "winner": int index, "odds": list (None ok)}
    Returns the fold-by-fold and aggregate report comparing the ranker's
    top pick against the market favourite.
    """
    race_dates = [r["date"] for r in races]
    folds, agg = [], {"races": 0, "ranker_hits": 0, "market_hits": 0}

    for train_idx, test_idx in _temporal_folds(race_dates):
        X_tr = np.vstack([races[i]["X"] for i in train_idx])
        y_tr = np.concatenate([
            np.eye(1, len(races[i]["X"]), races[i]["winner"]).ravel()
            for i in train_idx
        ])
        groups = [len(races[i]["X"]) for i in train_idx]
        model = train_ranker(X_tr, y_tr, groups, params)

        r_hits = m_hits = n = 0
        for i in test_idx:
            r = races[i]
            scores = model.predict(np.asarray(r["X"]))
            r_hits += int(int(np.argmax(scores)) == r["winner"])
            odds = [(o if (o is not None and o > 1) else np.inf) for o in r["odds"]]
            if np.isfinite(min(odds)):
                m_hits += int(int(np.argmin(odds)) == r["winner"])
            n += 1

        folds.append({
            "train_races": len(train_idx), "test_races": n,
            "ranker_hit": round(r_hits / n, 4),
            "market_hit": round(m_hits / n, 4),
        })
        agg["races"] += n
        agg["ranker_hits"] += r_hits
        agg["market_hits"] += m_hits
        if verbose:
            print(f"[RANK] fold {len(folds):>2}: train {len(train_idx):>5} races | "
                  f"test {n:>4} | ranker top-1 {r_hits / n:.3f} | "
                  f"market fav {m_hits / n:.3f}")

    report = {
        "folds": folds,
        "n_races": agg["races"],
        "ranker_top1_hit": round(agg["ranker_hits"] / agg["races"], 4) if agg["races"] else None,
        "market_top1_hit": round(agg["market_hits"] / agg["races"], 4) if agg["races"] else None,
    }
    if verbose and agg["races"]:
        print(f"[RANK] TOTAL {agg['races']} holdout races | "
              f"ranker top-1 {report['ranker_top1_hit']:.3f} | "
              f"market favourite {report['market_top1_hit']:.3f}")
    return report


# ------------------------------------------------------------------ DB path
def train_from_database(model_path=None, params=None):
    """
    Load training_view_v2 through retrain_v2's exact pipeline (same feature
    matrix, same leakage discipline), produce the walk-forward report, train
    the final ranker on the first 90% of races (temporal), and save the
    artifact. Evidence only — no pipeline hook consumes it.
    """
    import retrain_v2  # lazy: hard-requires DATABASE_URL at import

    df = retrain_v2.load_training_data()
    X_all = retrain_v2.build_feature_matrix(df)
    feature_columns = list(X_all.columns)

    import pandas as pd
    race_key = pd.MultiIndex.from_arrays([
        df["race_date"].astype(str),
        df["track"].astype(str).str.strip().str.lower(),
        pd.to_numeric(df["race_number"], errors="coerce").fillna(0).astype(int),
    ])
    y_all = pd.to_numeric(df["is_winner"], errors="coerce").fillna(0).astype(int).values
    odds_all = pd.to_numeric(df["_effective_odds"], errors="coerce").values
    Xv = X_all.values

    races = []
    order = pd.Series(range(len(df)), index=race_key)
    for key, idx in order.groupby(level=[0, 1, 2]):
        rows = idx.values
        winners = [j for j, ri in enumerate(rows) if y_all[ri] == 1]
        if len(rows) < 4 or len(winners) != 1:
            continue
        try:
            d = datetime.strptime(str(key[0])[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        races.append({
            "date": d,
            "X": Xv[rows],
            "winner": winners[0],
            "odds": [odds_all[ri] if np.isfinite(odds_all[ri]) else None for ri in rows],
        })
    races.sort(key=lambda r: r["date"])
    print(f"[RANK] Usable races: {len(races)} "
          f"({races[0]['date']} -> {races[-1]['date']})" if races else "[RANK] No usable races")
    if len(races) < 100:
        print("[RANK] Not enough races to produce meaningful evidence", file=sys.stderr)
        return None

    report = walk_forward_report(races, params=params)

    n_final = max(1, int(len(races) * 0.9))
    final = races[:n_final]
    X_tr = np.vstack([r["X"] for r in final])
    y_tr = np.concatenate([
        np.eye(1, len(r["X"]), r["winner"]).ravel() for r in final
    ])
    groups = [len(r["X"]) for r in final]
    model = train_ranker(X_tr, y_tr, groups, params)

    path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({
            "rank_model": model,
            "feature_columns": feature_columns,
            "cv_results": report,
            "params": {**RANKER_PARAMS, **(params or {})},
            "trained_at": datetime.now().isoformat(),
            "version": "rank_v1",
        }, f)
    print(f"[RANK] Saved {path}")
    print("[RANK] NOTE: evidence-only artifact — no pipeline hook consumes it "
          "(integration criterion: docs/12 §5 item 4).")
    return report


# ---------------------------------------------------------------- self-test
def _self_test():
    print("=" * 60)
    print("rank_model self-test (synthetic)")
    print("=" * 60)
    rng = np.random.default_rng(19)
    base = datetime(2026, 1, 1).date()
    races = []
    for k in range(360):
        n = int(rng.integers(6, 13))
        strength = rng.normal(0, 1, n)
        true_p = np.exp(1.5 * strength)
        true_p /= true_p.sum()
        winner = int(rng.choice(n, p=true_p))
        X = np.column_stack([
            strength + rng.normal(0, 0.6, n),   # noisy form signal
            strength + rng.normal(0, 1.0, n),   # noisier second signal
            rng.normal(0, 1, n),                # pure noise
            rng.normal(0, 1, n),
        ])
        X[rng.random(X.shape) < 0.05] = np.nan  # ranker must tolerate NaNs
        odds = (1.0 / np.clip(true_p + rng.normal(0, 0.02, n), 0.01, None)) * 1.15
        races.append({
            "date": base + timedelta(days=int(k / 3)),
            "X": X, "winner": winner, "odds": odds.tolist(),
        })

    report = walk_forward_report(races, params={"n_estimators": 120}, verbose=False)
    assert report["n_races"] >= 50, report
    avg_field = float(np.mean([len(r["X"]) for r in races]))
    random_hit = 1.0 / avg_field
    assert report["ranker_top1_hit"] > 2 * random_hit, (
        f"ranker {report['ranker_top1_hit']} vs random {random_hit:.3f}")
    print(f"  walk-forward: {report['n_races']} holdout races, "
          f"ranker top-1 {report['ranker_top1_hit']:.3f} "
          f"(random {random_hit:.3f}, market {report['market_top1_hit']:.3f})")

    # save/load roundtrip parity
    X_tr = np.vstack([r["X"] for r in races[:300]])
    y_tr = np.concatenate([np.eye(1, len(r["X"]), r["winner"]).ravel() for r in races[:300]])
    groups = [len(r["X"]) for r in races[:300]]
    model = train_ranker(X_tr, y_tr, groups, params={"n_estimators": 60})
    p1 = model.predict(np.asarray(races[-1]["X"]))
    tmp = "/tmp/rank_selftest.pkl"
    with open(tmp, "wb") as f:
        pickle.dump({"rank_model": model, "version": "rank_v1"}, f)
    with open(tmp, "rb") as f:
        p2 = pickle.load(f)["rank_model"].predict(np.asarray(races[-1]["X"]))
    assert np.allclose(p1, p2)
    print("  save/load roundtrip: predictions identical")
    print("All rank_model self-tests passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LambdaRank winner-ranking trainer")
    parser.add_argument("--train", action="store_true",
                        help="Train + walk-forward report from training_view_v2 "
                             "(requires DATABASE_URL)")
    parser.add_argument("--model-path", default=None,
                        help="Override artifact path (default models/rank_model_v1.pkl)")
    args = parser.parse_args()

    if args.train:
        train_from_database(model_path=args.model_path)
    else:
        _self_test()
