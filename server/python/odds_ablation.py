#!/usr/bin/env python3
"""SP-contamination ablation harness (task 12).

RUN-TASK — for after the odds-snapshot accrual window. Trains nothing live,
writes nothing to the DB. It runs walk_forward_backtest's existing machinery
three ways over a fixed date window and reports per-arm AUC, Brier, and
net-ROI at the registered band with a bootstrap CI (bets resampled via
walk_forward_backtest.bootstrap_roi_ci — the CI math is not reimplemented
here):

  A "legacy-full":       legacy (SP-filled) odds, all rows in the window
  B "snapshot":          tip-time snapshot odds, snapshot rows only
  C "legacy-restricted": legacy odds, EXACTLY B's row set  ← the control.
                         Without C, A-vs-B confounds the data-source effect
                         with the row-selection effect. B-vs-C is the honest
                         SP-contamination estimate.

Usage (after the accrual window):
    DATABASE_URL=... python odds_ablation.py \
        --start 2026-06-01 --end 2026-08-01 \
        [--min-rows 2000] [--band 0.10] [--out odds_ablation_report.json]

Refuses to run (exit 2) when snapshot coverage in the window is below
--min-rows: below the floor the B arm is too small for the comparison to
mean anything, and a quiet underpowered number is worse than no number.

All three arms run with identical harness configuration (default SP
settlement) so the only thing that differs is the odds the model trains on.
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import walk_forward_backtest as wfb

ARMS = ("legacy-full", "snapshot", "legacy-restricted")
DEFAULT_MIN_ROWS = 2000
DEFAULT_BAND = 0.10


def snapshot_row_count(df: pd.DataFrame) -> int:
    if "odds_source" not in df.columns:
        return 0
    return int((df["odds_source"].astype(str) == "snapshot").sum())


def build_ablation_arms(df: pd.DataFrame) -> dict:
    """Construct the three arms from a backtest-shaped frame that also carries
    tip_time_odds + odds_source (roi/04 view lineage).

    Legacy odds = market_odds.fillna(starting_price) — mirrors the retrain's
    SP-filled _effective_odds so arms A/C train on exactly what the retrain
    trains on today. Arm B's market_odds is replaced by tip_time_odds outright:
    no SP can reach it. C keeps B's exact row set with legacy odds restored.
    """
    legacy_odds = pd.to_numeric(df["market_odds"], errors="coerce").fillna(
        pd.to_numeric(df["starting_price"], errors="coerce"))
    is_snap = df["odds_source"].astype(str) == "snapshot"

    arm_a = df.copy()
    arm_a["market_odds"] = legacy_odds

    arm_b = df[is_snap].copy()
    arm_b["market_odds"] = pd.to_numeric(arm_b["tip_time_odds"], errors="coerce")

    arm_c = df[is_snap].copy()
    arm_c["market_odds"] = legacy_odds[is_snap]

    return {
        "legacy-full": arm_a,
        "snapshot": arm_b,
        "legacy-restricted": arm_c,
    }


def refuse_below_floor(df: pd.DataFrame, min_rows: int) -> int:
    """Exit 2 when snapshot coverage is below the floor. Returns the count."""
    n = snapshot_row_count(df)
    if n < min_rows:
        print(
            f"REFUSAL: snapshot coverage is {n} rows in the window, below the "
            f"--min-rows floor of {min_rows}. The snapshot arm would be too "
            f"small for the ablation to mean anything — rerun after more "
            f"accrual or lower --min-rows explicitly.",
            file=sys.stderr,
        )
        sys.exit(2)
    return n


def _capture_fold_bets():
    """Wrap wfb.compute_fold_metrics to capture each fold's (y, proba, odds).

    The fold metrics keep only aggregates; the arm-level bootstrap CI needs
    the pooled per-bet population, which is reconstructed here and fed to the
    harness's own compute_bet_pnl + bootstrap_roi_ci (no CI math reimplemented).
    """
    captured = []
    original = wfb.compute_fold_metrics

    def wrapper(y_true, y_pred_proba, odds, **kwargs):
        captured.append((np.asarray(y_true, dtype=int),
                         np.asarray(y_pred_proba, dtype=float),
                         np.asarray(odds, dtype=float),
                         kwargs.get("commission_rate", 0.0)))
        return original(y_true, y_pred_proba, odds, **kwargs)

    return captured, wrapper


def band_roi_from_bets(captured, band: float, stake: float = 100.0,
                       bootstrap_n: int = 2000, bootstrap_seed: int = 42) -> dict:
    """Pooled net-ROI at the band across all folds, with bootstrap CI from
    walk_forward_backtest.bootstrap_roi_ci (resamples bets)."""
    empty = {"band": band, "n_bets": 0, "net_roi_pct": 0.0, "profit": 0.0,
             "roi_ci95_bootstrap": wfb.bootstrap_roi_ci(
                 np.array([]), stake=stake, n_boot=bootstrap_n, seed=bootstrap_seed)}
    if not captured:
        return empty

    y = np.concatenate([c[0] for c in captured])
    p = np.concatenate([c[1] for c in captured])
    o = np.concatenate([c[2] for c in captured])
    commission = captured[0][3]

    mask = p >= band
    n_bets = int(mask.sum())
    if n_bets == 0:
        return empty

    pnl = wfb.compute_bet_pnl(y[mask], o[mask], stake=stake,
                              commission_rate=commission)
    profit = float(pnl.sum())
    return {
        "band": band,
        "n_bets": n_bets,
        "net_roi_pct": round(profit / (n_bets * stake) * 100.0, 2),
        "profit": round(profit, 2),
        "roi_ci95_bootstrap": wfb.bootstrap_roi_ci(
            pnl, stake=stake, n_boot=bootstrap_n, seed=bootstrap_seed),
    }


def run_arm(name: str, frame: pd.DataFrame, band: float,
            harness_kwargs: dict = None) -> dict:
    """Run the walk-forward machinery on one arm and extract the report."""
    kwargs = dict(harness_kwargs or {})
    bootstrap_n = kwargs.get("bootstrap_n", 2000)
    bootstrap_seed = kwargs.get("bootstrap_seed", 42)

    captured, wrapper = _capture_fold_bets()
    original = wfb.compute_fold_metrics
    wfb.compute_fold_metrics = wrapper
    try:
        report = wfb.WalkForwardBacktester(**kwargs).run(data=frame)
    finally:
        wfb.compute_fold_metrics = original

    agg = report.get("aggregate", {})
    return {
        "n_rows": int(len(frame)),
        "auc_roc": agg.get("auc_roc"),
        "brier": agg.get("brier"),
        "roi_at_band": band_roi_from_bets(
            captured, band, bootstrap_n=bootstrap_n, bootstrap_seed=bootstrap_seed),
        "n_folds": report.get("data_summary", {}).get("n_folds", 0),
    }


def run_ablation(df: pd.DataFrame, band: float = DEFAULT_BAND,
                 harness_kwargs: dict = None) -> dict:
    """All three arms over an already-loaded, already-floor-checked window."""
    arms = build_ablation_arms(df)
    results = {name: run_arm(name, frame, band, harness_kwargs)
               for name, frame in arms.items()}

    b = results["snapshot"]["roi_at_band"]
    c = results["legacy-restricted"]["roi_at_band"]
    results["comparison"] = {
        "sp_contamination_estimate": (
            "B (snapshot odds) vs C (legacy odds, same rows): isolates the "
            "odds-source effect from the row-selection effect"),
        "b_minus_c_net_roi_pct": round(b["net_roi_pct"] - c["net_roi_pct"], 2),
        "b_net_roi_pct": b["net_roi_pct"],
        "c_net_roi_pct": c["net_roi_pct"],
    }
    return results


def load_window(start: str, end: str, database_url: str) -> pd.DataFrame:
    """Backtest-shaped frame for [start, end) with tip_time_odds/odds_source
    joined from runner_odds_snapshots (same aggregation as the roi/04 view).
    Read-only."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(database_url)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            WITH odds_snap AS (
                SELECT
                    s.race_date,
                    stride_norm_track(s.track) AS track_norm,
                    s.race_number,
                    s.horse_name_norm,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY s.decimal_odds)::double precision AS tip_time_odds
                FROM runner_odds_snapshots s
                WHERE s.snapshot_kind = 'tip_time'
                  AND s.race_date IS NOT NULL
                  AND s.horse_name_norm IS NOT NULL
                GROUP BY 1, 2, 3, 4
            )
            SELECT
                td.race_id, td.track, td.race_number, td.race_date, td.distance,
                td.going, td.race_class, td.horse_name, td.horse_number,
                td.barrier, td.jockey, td.trainer, td.weight,
                td.market_odds, td.actual_position, td.won, td.placed,
                td.starting_price,
                osn.tip_time_odds,
                CASE
                    WHEN osn.tip_time_odds IS NOT NULL THEN 'snapshot'
                    WHEN td.market_odds IS NOT NULL THEN 'racecard'
                    WHEN td.starting_price IS NOT NULL THEN 'sp_fallback'
                    ELSE NULL
                END AS odds_source
            FROM training_data td
            LEFT JOIN odds_snap osn
              ON osn.race_date = td.race_date::date
             AND osn.track_norm = stride_norm_track(td.track)
             AND osn.race_number = td.race_number
             AND osn.horse_name_norm = stride_norm_name(td.horse_name)
            WHERE td.won IS NOT NULL
              AND td.race_date IS NOT NULL
              AND td.race_date::date >= %(start)s::date
              AND td.race_date::date < %(end)s::date
            ORDER BY td.race_date, td.track, td.race_number
        """, {"start": start, "end": end})
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    df = pd.DataFrame(rows)
    if df.empty:
        print(f"REFUSAL: 0 training rows in [{start}, {end})", file=sys.stderr)
        sys.exit(2)
    df["won"] = df["won"].astype(int)
    if "placed" in df.columns:
        df["placed"] = df["placed"].fillna(0).astype(int)
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="SP-contamination ablation (task 12).")
    parser.add_argument("--start", required=True, help="window start YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="window end YYYY-MM-DD (exclusive)")
    parser.add_argument("--min-rows", type=int, default=DEFAULT_MIN_ROWS,
                        help=f"snapshot coverage floor (default {DEFAULT_MIN_ROWS})")
    parser.add_argument("--band", type=float, default=DEFAULT_BAND,
                        help=f"registered probability band for net-ROI (default {DEFAULT_BAND})")
    parser.add_argument("--out", default=None, help="write the report JSON here")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    df = load_window(args.start, args.end, database_url)
    n_snap = refuse_below_floor(df, args.min_rows)
    print(f"Window [{args.start}, {args.end}): {len(df):,} rows, "
          f"{n_snap:,} with snapshot odds")

    results = run_ablation(df, band=args.band)
    report = {
        "window": {"start": args.start, "end": args.end},
        "min_rows": args.min_rows,
        "snapshot_rows": n_snap,
        "band": args.band,
        "arms": {name: results[name] for name in ARMS},
        "comparison": results["comparison"],
    }

    text = json.dumps(report, indent=2, default=str)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"Report written to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
