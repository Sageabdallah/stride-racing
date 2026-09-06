#!/usr/bin/env python3
"""Per-race metrics for walk-forward CV — the number the product sells.

retrain_v2's CV reported pooled AUC and Brier only. Pooled AUC is dominated by
cross-race separation (the market feature supplies most of it) while a tip is
a within-race choice, so ablations were being decided on ±0.001 AUC against a
fold standard deviation of 0.044 and the per-race top-1 hit rate — the thing
STRIDE actually sells — was never measured in the trainer (evidence base C1,
12-retrain-rebaseline.md step 3). This module computes it, with the baselines
the pre-registration amendment of 2026-09-05 fixes:

  model_top1_hit      argmax of the ensemble probability == winner
  fav_tip_time_hit    the TIP-TIME favourite (shortest tip_time_odds) — the
                      PRIMARY baseline: selection uses the price knowable at
                      tip time (09-forward-validation-protocol.md)
  fav_sp_hit          the SP favourite — a HINDSIGHT diagnostic only; SP is
                      the closing line and is for settlement/CLV
  h2h                 on races where the stored production probability
                      (training_view_v2.predicted_win_prob) covers the FULL
                      field: model vs stored vs tip-time favourite on identical
                      races — the same-race criterion rank_model.py uses
  race_logloss        -log of the winner's within-race normalised probability
                      (p_winner / sum over the field)

A race is usable when it has at least MIN_RUNNERS runners in the frame and
exactly one winner — rank_model.py's rule; a two-runner "race" in the
training view is usually a partial field, not a match race. A favourite is
defined only when EVERY runner in the race has a usable price (> 1.0):
argmin over a partial set is not the favourite.

Fold results carry integer counts so pooling is exact (sum the counts, then
divide) rather than a mean of fold rates.

DB-free: numpy and pandas only. `python race_metrics.py` runs the self-test.
"""

from __future__ import annotations

import math
import sys
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd

MIN_RUNNERS = 4

_COUNT_KEYS = (
    "races_total", "races_used", "model_hits",
    "fav_tt_races", "fav_tt_hits", "fav_sp_races", "fav_sp_hits",
    "h2h_races", "h2h_model_hits", "h2h_stored_hits",
    "h2h_fav_tt_races", "h2h_fav_tt_hits",
    "logloss_races",
)


def _empty_counts() -> Dict[str, Any]:
    c: Dict[str, Any] = {k: 0 for k in _COUNT_KEYS}
    c["logloss_sum"] = 0.0
    return c


def favourite_index(odds) -> Optional[int]:
    """Index of the shortest price, or None unless every runner has a usable
    price (> 1.0). A favourite over part of a field is not a favourite."""
    if odds is None:
        return None
    o = np.asarray(odds, dtype=float)
    if o.size == 0 or not np.all(np.isfinite(o)) or not np.all(o > 1.0):
        return None
    return int(np.argmin(o))


def _col(values, n: int) -> np.ndarray:
    if values is None:
        return np.full(n, np.nan)
    arr = pd.to_numeric(pd.Series(np.asarray(values)), errors="coerce").to_numpy(dtype=float)
    if arr.shape[0] != n:
        raise ValueError(f"length {arr.shape[0]} != {n}")
    return arr


def race_counts(proba, y, race_keys, tip_time_odds=None, sp_odds=None,
                stored=None, min_runners: int = MIN_RUNNERS) -> Dict[str, Any]:
    """Integer counts over the races in one fold (see module docstring)."""
    p_all = np.asarray(proba, dtype=float)
    n = p_all.shape[0]
    y_all = np.asarray(y).astype(int)
    keys = np.asarray(race_keys).astype(str)
    if y_all.shape[0] != n or keys.shape[0] != n:
        raise ValueError("proba, y and race_keys must be the same length")
    tt_all, sp_all, st_all = _col(tip_time_odds, n), _col(sp_odds, n), _col(stored, n)

    counts = _empty_counts()
    order = pd.Series(np.arange(n), index=keys)
    for _key, idx in order.groupby(level=0, sort=False):
        rows = idx.to_numpy()
        counts["races_total"] += 1
        winners = np.where(y_all[rows] == 1)[0]
        if rows.shape[0] < min_runners or winners.shape[0] != 1:
            continue
        counts["races_used"] += 1
        w = int(winners[0])
        p = p_all[rows]
        pick = int(np.argmax(p))
        counts["model_hits"] += int(pick == w)

        total = float(np.sum(np.clip(p, 0.0, None)))
        if total > 0:
            pw = max(float(p[w]), 0.0) / total
            counts["logloss_sum"] += -math.log(max(pw, 1e-12))
            counts["logloss_races"] += 1

        f_tt = favourite_index(tt_all[rows])
        if f_tt is not None:
            counts["fav_tt_races"] += 1
            counts["fav_tt_hits"] += int(f_tt == w)
        f_sp = favourite_index(sp_all[rows])
        if f_sp is not None:
            counts["fav_sp_races"] += 1
            counts["fav_sp_hits"] += int(f_sp == w)

        st = st_all[rows]
        if np.all(np.isfinite(st)) and np.all(st > 0):
            counts["h2h_races"] += 1
            counts["h2h_model_hits"] += int(pick == w)
            counts["h2h_stored_hits"] += int(int(np.argmax(st)) == w)
            if f_tt is not None:
                counts["h2h_fav_tt_races"] += 1
                counts["h2h_fav_tt_hits"] += int(f_tt == w)
    return counts


def summarise(counts: Dict[str, Any]) -> Dict[str, Any]:
    """Exact ratios (rounding is for display only — format_line)."""
    def rate(h, d):
        return h / d if d else None

    used = counts["races_used"]
    return {
        "counts": dict(counts),
        "n_races_total": counts["races_total"],
        "n_races_used": used,
        "model_top1_hit": rate(counts["model_hits"], used),
        "fav_tip_time_hit": rate(counts["fav_tt_hits"], counts["fav_tt_races"]),
        "fav_tip_time_coverage": rate(counts["fav_tt_races"], used),
        "fav_sp_hit": rate(counts["fav_sp_hits"], counts["fav_sp_races"]),
        "fav_sp_coverage": rate(counts["fav_sp_races"], used),
        "h2h": {
            "n": counts["h2h_races"],
            "model_hit": rate(counts["h2h_model_hits"], counts["h2h_races"]),
            "stored_hit": rate(counts["h2h_stored_hits"], counts["h2h_races"]),
            "fav_tip_time_hit": rate(counts["h2h_fav_tt_hits"], counts["h2h_fav_tt_races"]),
            "fav_tip_time_n": counts["h2h_fav_tt_races"],
        },
        "race_logloss": (counts["logloss_sum"] / counts["logloss_races"]
                         if counts["logloss_races"] else None),
    }


def per_race_metrics(proba, y, race_keys, tip_time_odds=None, sp_odds=None,
                     stored=None, min_runners: int = MIN_RUNNERS) -> Dict[str, Any]:
    return summarise(race_counts(proba, y, race_keys, tip_time_odds, sp_odds,
                                 stored, min_runners))


def pool(fold_metrics: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Exact pooling: sum the per-fold counts, then divide once."""
    total = _empty_counts()
    for m in fold_metrics:
        c = m.get("counts") or {}
        for k in _COUNT_KEYS:
            total[k] += int(c.get(k, 0))
        total["logloss_sum"] += float(c.get("logloss_sum", 0.0))
    return summarise(total)


def format_line(m: Dict[str, Any]) -> str:
    def f(v):
        return "  n/a" if v is None else f"{v:.3f}"

    h = m["h2h"]
    line = (f"races {m['n_races_used']:>4}/{m['n_races_total']:<4} top-1: model {f(m['model_top1_hit'])}"
            f" | fav(tip-time) {f(m['fav_tip_time_hit'])}"
            f" (cov {f(m['fav_tip_time_coverage'])})"
            f" | fav(SP, hindsight) {f(m['fav_sp_hit'])}"
            f" | race log-loss {f(m['race_logloss'])}")
    if h["n"]:
        line += (f" | H2H n={h['n']}: model {f(h['model_hit'])} stored {f(h['stored_hit'])}"
                 f" fav(tip) {f(h['fav_tip_time_hit'])}")
    return line


def build_race_meta(df: pd.DataFrame) -> pd.DataFrame:
    """The per-row race context retrain_v2 hands to the CV: race key on the
    same (race_date, track, race_number) identity the pace features group
    by, tip-time and SP odds, and the stored production probability.
    Columns absent from the frame become NaN — never a substitute."""
    key = (df["race_date"].astype(str) + "|"
           + df["track"].astype(str).str.strip().str.lower() + "|"
           + pd.to_numeric(df["race_number"], errors="coerce").fillna(0).astype(int).astype(str))
    n = len(df)

    def num(col):
        return (pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
                if col in df.columns else np.full(n, np.nan))

    out = pd.DataFrame({
        "race_key": key.to_numpy(),
        "tip_time_odds": num("tip_time_odds"),
        "sp_odds": num("sp_odds"),
        "predicted_win_prob": num("predicted_win_prob"),
    })
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- self-test

def _self_test() -> None:
    print("race_metrics self-test")
    # Two races. Race A: model right, tip-time fav wrong, SP fav right, stored right.
    # Race B: model wrong, tip-time fav right, SP fav missing (one NaN), stored partial.
    keys = ["A"] * 4 + ["B"] * 5
    y = [1, 0, 0, 0, 0, 1, 0, 0, 0]
    p = [0.5, 0.3, 0.1, 0.1, 0.4, 0.3, 0.2, 0.05, 0.05]
    tt = [3.0, 2.5, 8.0, 12.0, 4.0, 2.0, 6.0, 10.0, 20.0]
    sp = [2.0, 3.0, 8.0, 12.0, 4.0, np.nan, 6.0, 10.0, 20.0]
    st = [0.4, 0.3, 0.2, 0.1, 0.3, np.nan, 0.2, 0.1, 0.1]
    m = per_race_metrics(p, y, keys, tt, sp, st)
    assert m["n_races_used"] == 2 and m["model_top1_hit"] == 0.5, m
    assert m["fav_tip_time_hit"] == 0.5 and m["fav_tip_time_coverage"] == 1.0
    assert m["fav_sp_hit"] == 1.0 and m["fav_sp_coverage"] == 0.5, "race B has a NaN SP -> no favourite"
    assert m["h2h"]["n"] == 1 and m["h2h"]["model_hit"] == 1.0 and m["h2h"]["stored_hit"] == 1.0
    assert m["h2h"]["fav_tip_time_hit"] == 0.0  # race A's tip-time fav was runner 2, not the winner
    assert abs(m["race_logloss"] - (-math.log(0.5) - math.log(0.3)) / 2) < 1e-9
    print(f"  two-race fixture: {format_line(m)}")

    # Unusable races: fewer than MIN_RUNNERS, or not exactly one winner.
    m2 = per_race_metrics([0.6, 0.4, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5], [1, 0, 1, 1, 0, 0, 0, 0],
                          ["S", "S", "D", "D", "D", "D", "D", "D"])
    assert m2["n_races_total"] == 2 and m2["n_races_used"] == 0 and m2["model_top1_hit"] is None

    # Favourite is undefined over a partial price set, and over odds <= 1.
    assert favourite_index([2.0, np.nan, 3.0]) is None
    assert favourite_index([2.0, 1.0, 3.0]) is None
    assert favourite_index([2.0, 1.5, 3.0]) == 1

    # Pooling is exact: counts sum, rates recomputed.
    rng = np.random.default_rng(3)
    folds = []
    all_p, all_y, all_k, all_tt = [], [], [], []
    for f in range(4):
        for r in range(30):
            n = int(rng.integers(4, 12))
            strength = rng.normal(0, 1, n)
            prob = np.exp(strength) / np.exp(strength).sum()
            w = int(rng.choice(n, p=prob))
            yy = np.zeros(n, int); yy[w] = 1
            odds = 1.0 / np.clip(prob + rng.normal(0, 0.03, n), 0.02, None) * 1.15
            all_p += list(prob); all_y += list(yy); all_k += [f"f{f}r{r}"] * n; all_tt += list(odds)
        sl = slice(len(all_p) - sum(1 for k in all_k if k.startswith(f"f{f}")), len(all_p))
        folds.append(per_race_metrics(all_p[sl], all_y[sl], all_k[sl], all_tt[sl]))
    pooled = pool(folds)
    direct = per_race_metrics(all_p, all_y, all_k, all_tt)
    for k in _COUNT_KEYS:   # integers: exact
        assert pooled["counts"][k] == direct["counts"][k], (k, pooled["counts"][k], direct["counts"][k])
    assert abs(pooled["counts"]["logloss_sum"] - direct["counts"]["logloss_sum"]) < 1e-9
    assert pooled["model_top1_hit"] == direct["model_top1_hit"]
    assert pooled["n_races_used"] == 120
    print(f"  pooled 4 folds == direct: {format_line(pooled)}")

    # build_race_meta: key identity + NaN for absent columns
    df = pd.DataFrame({"race_date": ["2026-03-14", "2026-03-14"], "track": [" Flemington ", "flemington"],
                       "race_number": [5, 5.0], "tip_time_odds": [3.5, None], "sp_odds": [3.2, 4.0]})
    meta = build_race_meta(df)
    assert list(meta["race_key"]) == ["2026-03-14|flemington|5"] * 2
    assert meta["tip_time_odds"].tolist()[0] == 3.5 and math.isnan(meta["tip_time_odds"].tolist()[1])
    assert meta["predicted_win_prob"].isna().all(), "absent column -> NaN, never a substitute"
    print("All race_metrics self-tests passed.")


if __name__ == "__main__":
    _self_test()
    sys.exit(0)
