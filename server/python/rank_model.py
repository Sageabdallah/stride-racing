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
fold by fold — and, on test races where the production model's stored
MC-stage probabilities cover the full field, a three-way head-to-head
(ranker vs stored model vs favourite on identical races). Nothing in the
live pipeline consumes the artifact until that report earns integration
(the criterion is written in docs/12 §5.4).

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
       "winner": int index, "odds": list (None ok),
       "stored": optional list of the production model's stored win probs
                 (None entries ok)}
    Returns the fold-by-fold and aggregate report comparing the ranker's
    top pick against the market favourite — and, on the subset of test races
    where the stored probabilities cover the FULL field, a three-way
    head-to-head (ranker vs stored model vs favourite on identical races).
    That subset is what docs/12 §5.4's integration criterion is judged on:
    partial coverage would make argmax-over-part-of-the-field meaningless.
    """
    race_dates = [r["date"] for r in races]
    folds = []
    agg = {"races": 0, "ranker_hits": 0, "market_hits": 0,
           "h2h_races": 0, "h2h_ranker": 0, "h2h_stored": 0, "h2h_market": 0}

    for train_idx, test_idx in _temporal_folds(race_dates):
        X_tr = np.vstack([races[i]["X"] for i in train_idx])
        y_tr = np.concatenate([
            np.eye(1, len(races[i]["X"]), races[i]["winner"]).ravel()
            for i in train_idx
        ])
        groups = [len(races[i]["X"]) for i in train_idx]
        model = train_ranker(X_tr, y_tr, groups, params)

        r_hits = m_hits = n = 0
        h2h = {"n": 0, "ranker": 0, "stored": 0, "market": 0}
        for i in test_idx:
            r = races[i]
            scores = model.predict(np.asarray(r["X"]))
            pick = int(np.argmax(scores))
            r_hits += int(pick == r["winner"])
            odds = [(o if (o is not None and o > 1) else np.inf) for o in r["odds"]]
            fav = int(np.argmin(odds)) if np.isfinite(min(odds)) else None
            if fav is not None:
                m_hits += int(fav == r["winner"])
            n += 1

            stored = r.get("stored")
            if stored is not None and fav is not None:
                s = np.asarray([(v if v is not None else np.nan) for v in stored],
                               dtype=float)
                if s.shape[0] == len(odds) and np.all(np.isfinite(s)) and np.all(s > 0):
                    h2h["n"] += 1
                    h2h["ranker"] += int(pick == r["winner"])
                    h2h["stored"] += int(int(np.argmax(s)) == r["winner"])
                    h2h["market"] += int(fav == r["winner"])

        folds.append({
            "train_races": len(train_idx), "test_races": n,
            "ranker_hit": round(r_hits / n, 4),
            "market_hit": round(m_hits / n, 4),
            "h2h_races": h2h["n"],
            "h2h_ranker_hit": round(h2h["ranker"] / h2h["n"], 4) if h2h["n"] else None,
            "h2h_stored_hit": round(h2h["stored"] / h2h["n"], 4) if h2h["n"] else None,
            "h2h_market_hit": round(h2h["market"] / h2h["n"], 4) if h2h["n"] else None,
        })
        agg["races"] += n
        agg["ranker_hits"] += r_hits
        agg["market_hits"] += m_hits
        agg["h2h_races"] += h2h["n"]
        agg["h2h_ranker"] += h2h["ranker"]
        agg["h2h_stored"] += h2h["stored"]
        agg["h2h_market"] += h2h["market"]
        if verbose:
            line = (f"[RANK] fold {len(folds):>2}: train {len(train_idx):>5} races | "
                    f"test {n:>4} | ranker top-1 {r_hits / n:.3f} | "
                    f"market fav {m_hits / n:.3f}")
            if h2h["n"]:
                line += (f" | h2h {h2h['n']:>3}: ranker {h2h['ranker'] / h2h['n']:.3f} "
                         f"stored {h2h['stored'] / h2h['n']:.3f} "
                         f"fav {h2h['market'] / h2h['n']:.3f}")
            print(line)

    report = {
        "folds": folds,
        "n_races": agg["races"],
        "ranker_top1_hit": round(agg["ranker_hits"] / agg["races"], 4) if agg["races"] else None,
        "market_top1_hit": round(agg["market_hits"] / agg["races"], 4) if agg["races"] else None,
        "h2h_races": agg["h2h_races"],
        "h2h_ranker_hit": round(agg["h2h_ranker"] / agg["h2h_races"], 4) if agg["h2h_races"] else None,
        "h2h_stored_hit": round(agg["h2h_stored"] / agg["h2h_races"], 4) if agg["h2h_races"] else None,
        "h2h_market_hit": round(agg["h2h_market"] / agg["h2h_races"], 4) if agg["h2h_races"] else None,
    }
    if verbose and agg["races"]:
        print(f"[RANK] TOTAL {agg['races']} holdout races | "
              f"ranker top-1 {report['ranker_top1_hit']:.3f} | "
              f"market favourite {report['market_top1_hit']:.3f}")
        if agg["h2h_races"]:
            print(f"[RANK] H2H (test races where stored probs cover the full field) "
                  f"{agg['h2h_races']} races | ranker {report['h2h_ranker_hit']:.3f} | "
                  f"stored model {report['h2h_stored_hit']:.3f} | "
                  f"market fav {report['h2h_market_hit']:.3f}")
        else:
            print("[RANK] H2H: no test races with full stored-prob coverage "
                  "(predicted_win_prob missing from the loaded view?)")
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
    # Stored production probability (MC stage, via prediction_audit in the
    # view) — benchmark only. It is not in FEATURE_COLUMNS, so the ranker
    # never sees it; it exists purely so the walk-forward report can judge
    # docs/12 §5.4's criterion on identical races.
    if "predicted_win_prob" in df.columns:
        stored_all = pd.to_numeric(df["predicted_win_prob"], errors="coerce").values
    else:
        stored_all = np.full(len(df), np.nan)
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
            "stored": [stored_all[ri] if np.isfinite(stored_all[ri]) else None for ri in rows],
        })
    races.sort(key=lambda r: r["date"])
    print(f"[RANK] Usable races: {len(races)} "
          f"({races[0]['date']} -> {races[-1]['date']})" if races else "[RANK] No usable races")
    covered = sum(
        1 for r in races
        if all(v is not None and v > 0 for v in r["stored"])
    )
    print(f"[RANK] Stored-prob full-field coverage: {covered}/{len(races)} races "
          f"(the head-to-head subset)")
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
        race = {
            "date": base + timedelta(days=int(k / 3)),
            "X": X, "winner": winner, "odds": odds.tolist(),
        }
        # Stored-model probs appear only from k=260 on (mirrors live data,
        # where prediction_audit coverage starts partway through the view),
        # and every 10th covered race is left partially covered — the
        # head-to-head must count neither the uncovered nor the partial ones.
        if k >= 260:
            stored = np.clip(true_p + rng.normal(0, 0.05, n), 1e-3, None)
            if k % 10 == 0:
                race["stored"] = [None] + stored[1:].tolist()
            else:
                race["stored"] = stored.tolist()
        races.append(race)

    report = walk_forward_report(races, params={"n_estimators": 120}, verbose=False)
    assert report["n_races"] >= 50, report
    avg_field = float(np.mean([len(r["X"]) for r in races]))
    random_hit = 1.0 / avg_field
    assert report["ranker_top1_hit"] > 2 * random_hit, (
        f"ranker {report['ranker_top1_hit']} vs random {random_hit:.3f}")
    print(f"  walk-forward: {report['n_races']} holdout races, "
          f"ranker top-1 {report['ranker_top1_hit']:.3f} "
          f"(random {random_hit:.3f}, market {report['market_top1_hit']:.3f})")

    # head-to-head subset mechanics: some races covered, partial ones skipped
    assert 0 < report["h2h_races"] < report["n_races"], report
    for key in ("h2h_ranker_hit", "h2h_stored_hit", "h2h_market_hit"):
        assert 0.0 <= report[key] <= 1.0, (key, report[key])
    assert report["h2h_stored_hit"] > 2 * random_hit, (
        f"stored argmax should track the synthetic signal: "
        f"{report['h2h_stored_hit']} vs random {random_hit:.3f}")
    per_fold_h2h = sum(f["h2h_races"] for f in report["folds"])
    assert per_fold_h2h == report["h2h_races"]
    print(f"  head-to-head subset: {report['h2h_races']}/{report['n_races']} races, "
          f"ranker {report['h2h_ranker_hit']:.3f} / stored {report['h2h_stored_hit']:.3f} / "
          f"fav {report['h2h_market_hit']:.3f}")

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
