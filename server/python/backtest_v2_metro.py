#!/usr/bin/env python3
"""
Targeted backtest of the v2 ensemble model on recent metro races.
Loads the saved model, scores each race, and compares strategies.
"""

import argparse
import json
import os
import sys
import pickle
import numpy as np
import pandas as pd
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import roi_stats  # shared backtest statistics (roi-roadmap task 02)

# Commission for net ROI: STRIDE_COMMISSION_RATE, default 0.08 until task 01's
# settlement plumbing merges.
COMMISSION = roi_stats.commission_rate_from_env()

ENV_PATH = os.path.normpath(os.path.join(SCRIPT_DIR, os.pardir, os.pardir, ".env"))
# A .env outside the repo, for a checkout whose root has none. This used to be
# a hardcoded path to one dev machine's Windows checkout, which resolved on no
# other machine — including the Mac it was meant for.
EXPLICIT_ENV = os.environ.get("STRIDE_ENV_FILE", "")

def _load_env(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env

_env = _load_env(ENV_PATH)
if EXPLICIT_ENV and not _env.get("DATABASE_URL"):
    _env = _load_env(EXPLICIT_ENV)
DATABASE_URL = _env.get("DATABASE_URL") or os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found"); sys.exit(1)

import psycopg2, psycopg2.extras
from sklearn.isotonic import IsotonicRegression

METRO_TRACKS = (
    'rosehill gardens', 'royal randwick', 'flemington', 'caulfield',
    'eagle farm', 'doomben', 'canterbury park', 'newcastle',
    'warwick farm', 'morphettville', 'sandown', 'moonee valley',
    'caulfield heath',
)

MODEL_PATH = os.path.join(SCRIPT_DIR, "models", "racing_ensemble_v2.pkl")

PHASE2_FEATURES = [
    "z_200m", "z_400m", "z_600m", "z_800m",
    "lambda_decay", "svi", "rsi", "trip_cost_seconds",
]

VIEW_TO_FEATURE_MAP = {
    "prior_z_200m": "z_200m", "prior_z_400m": "z_400m",
    "prior_z_600m": "z_600m", "prior_z_800m": "z_800m",
    "prior_lambda_decay": "lambda_decay", "prior_svi": "svi",
    "prior_rsi": "rsi", "prior_trip_cost_seconds": "trip_cost_seconds",
    "barrier": "barrier_draw", "distance_m": "distance",
    "weight_kg": "weight_kg", "field_size": "field_size",
    "class_level": "class_level", "sp_odds": "market_odds",
    "weighted_form_score": "weighted_form_score",
    "ground_suitability": "ground_suitability",
    "distance_strike_rate": "distance_strike_rate",
}


def load_model(model_path=MODEL_PATH):
    with open(model_path, "rb") as f:
        payload = pickle.load(f)
    return payload


def predict_ensemble(payload, X):
    feature_cols = payload["feature_columns"]
    X_sub = X[feature_cols]
    preds = []

    for key in ["xgb_model", "lgb_model", "cb_model"]:
        model = payload.get(key)
        if model is not None:
            raw = model.predict_proba(X_sub)[:, 1]
            if hasattr(model, "_isotonic"):
                raw = model._isotonic.transform(raw)
            preds.append(raw)

    if not preds:
        return np.zeros(len(X_sub))
    return np.mean(preds, axis=0)


def load_metro_data(date_from, date_to):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            race_date, track, race_number, horse_name, position,
            is_winner, margin_lengths,
            barrier, distance_m, weight_kg, field_size, class_level,
            sp_odds, market_odds,
            weighted_form_score, ground_suitability, distance_strike_rate,
            prior_z_200m, prior_z_400m, prior_z_600m, prior_z_800m,
            prior_lambda_decay, prior_svi, prior_rsi, prior_trip_cost_seconds
        FROM training_view_v2
        WHERE race_date >= %s AND race_date <= %s
          AND LOWER(track) IN %s
          AND sp_odds IS NOT NULL AND sp_odds > 0
        ORDER BY race_date, track, race_number, position
    """, (date_from, date_to, METRO_TRACKS))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return pd.DataFrame(rows)


def build_feature_matrix(df, feature_cols):
    out = pd.DataFrame(index=df.index)

    if "market_odds" in df.columns and "sp_odds" in df.columns:
        df["_effective_odds"] = pd.to_numeric(
            df["market_odds"], errors="coerce"
        ).fillna(pd.to_numeric(df["sp_odds"], errors="coerce"))
    elif "sp_odds" in df.columns:
        df["_effective_odds"] = pd.to_numeric(df["sp_odds"], errors="coerce")
    else:
        df["_effective_odds"] = np.nan

    for view_col, feat_col in VIEW_TO_FEATURE_MAP.items():
        if view_col == "sp_odds":
            continue
        if view_col in df.columns:
            out[feat_col] = pd.to_numeric(df[view_col], errors="coerce")

    out["market_odds"] = df["_effective_odds"]

    for col in feature_cols:
        if col not in out.columns:
            out[col] = np.nan

    non_sect = [f for f in feature_cols if f not in PHASE2_FEATURES]
    for col in non_sect:
        if col in out.columns:
            out[col] = out[col].fillna(0)

    out = out[[c for c in feature_cols if c in out.columns]]
    return out


STRATEGIES = [
    ("Value Edge 3%+ ($2-$15)", {"min_odds": 2.0, "max_odds": 15.0, "min_edge": 0.03}),
    ("Mid-Range $3-$8 5%+ edge", {"min_odds": 3.0, "max_odds": 8.0, "min_edge": 0.05}),
    ("Short Price $2-$5 3%+ edge", {"min_odds": 2.0, "max_odds": 5.0, "min_edge": 0.03}),
    ("Big Value $5-$15 5%+ edge", {"min_odds": 5.0, "max_odds": 15.0, "min_edge": 0.05}),
    ("Top Pick (highest prob)", {"top_pick": True}),
    ("All $2-$20 positive edge", {"min_odds": 2.0, "max_odds": 20.0, "min_edge": 0.001}),
]

STAKE = 100


def run_backtest(df, payload):
    feature_cols = payload["feature_columns"]
    X = build_feature_matrix(df.copy(), feature_cols)
    probs = predict_ensemble(payload, X)
    df = df.copy()
    df["model_prob"] = probs
    df["norm_prob"] = df.groupby(["race_date", "track", "race_number"])["model_prob"].transform(
        lambda x: x / x.sum() if x.sum() > 0 else 1.0 / max(len(x), 1)
    )

    races = df.groupby(["race_date", "track", "race_number"])

    results = {label: {"bets": [], "label": label} for label, _ in STRATEGIES}

    for (date, track, rnum), race_df in races:
        race_df = race_df.copy()
        total_prob = race_df["model_prob"].sum()
        if total_prob > 0:
            race_df["norm_prob"] = race_df["model_prob"] / total_prob
        else:
            race_df["norm_prob"] = 1.0 / len(race_df)

        for label, cfg in STRATEGIES:
            if cfg.get("top_pick"):
                best_idx = race_df["norm_prob"].idxmax()
                best = race_df.loc[best_idx]
                sp = float(best["sp_odds"])
                if sp >= 1.5:
                    results[label]["bets"].append({
                        "date": str(date), "track": track, "race": rnum,
                        "horse": best["horse_name"], "sp": sp,
                        "prob": float(best["norm_prob"]),
                        "won": bool(best["is_winner"]),
                        "position": int(best["position"]) if pd.notna(best["position"]) else 99,
                    })
                continue

            min_odds = cfg["min_odds"]
            max_odds = cfg["max_odds"]
            min_edge = cfg["min_edge"]

            candidates = []
            for _, row in race_df.iterrows():
                sp = float(row["sp_odds"])
                if sp < min_odds or sp > max_odds:
                    continue
                implied = 1.0 / sp
                prob = float(row["norm_prob"])
                edge = prob - implied
                if edge >= min_edge:
                    ev = prob * sp - 1
                    candidates.append({
                        "date": str(date), "track": track, "race": rnum,
                        "horse": row["horse_name"], "sp": sp,
                        "prob": prob, "edge": edge, "ev": ev,
                        "won": bool(row["is_winner"]),
                        "position": int(row["position"]) if pd.notna(row["position"]) else 99,
                    })

            if candidates:
                best = max(candidates, key=lambda x: x["ev"])
                results[label]["bets"].append(best)

    return results, df


def _compute_calibration_bins(df, n_bins=10):
    """Bin predicted win prob and report per-bin predicted-vs-observed (for a calibration plot)."""
    work = df[df["norm_prob"].notna() & df["is_winner"].notna()].copy()
    if len(work) == 0:
        return {"bins": [], "brier_score": None, "n": 0}
    edges = np.linspace(0, 1, n_bins + 1)
    work["_bin"] = pd.cut(work["norm_prob"], bins=edges, include_lowest=True)
    bins_out = []
    for interval, g in work.groupby("_bin", observed=True):
        n = len(g)
        if n == 0:
            continue
        bins_out.append({
            "bin_low": float(interval.left),
            "bin_high": float(interval.right),
            "n_runners": int(n),
            "predicted_mean": float(g["norm_prob"].mean()),
            "observed_mean": float(g["is_winner"].astype(int).mean()),
        })
    p = work["norm_prob"].astype(float).values
    y = work["is_winner"].astype(int).values
    brier = float(np.mean((p - y) ** 2))
    return {"bins": bins_out, "brier_score": brier, "n": int(len(work))}


def _compute_confidence_bands(df, longshot_cap=30.0):
    """Take the top model pick per race, classify its live confidence tier, and aggregate ROI per tier."""
    work = df[df["norm_prob"].notna() & df["sp_odds"].notna() & df["is_winner"].notna()].copy()
    work["_sp"] = pd.to_numeric(work["sp_odds"], errors="coerce")
    work = work[work["_sp"] > 1.0]

    top_idx = work.groupby(["race_date", "track", "race_number"])["norm_prob"].idxmax()
    top = work.loc[top_idx].copy()
    top["_implied"] = 1.0 / top["_sp"]
    top["_edge_pp"] = (top["norm_prob"] - top["_implied"]) * 100
    top["_ev"] = top["norm_prob"] * top["_sp"] - 1.0

    def tier(row):
        if row["_sp"] > longshot_cap:
            return "LOW"
        if row["_ev"] > 0 and row["_edge_pp"] > 1.0:
            return "HIGH"
        if row["_edge_pp"] > 0:
            return "MEDIUM"
        return "LOW"

    top["_tier"] = top.apply(tier, axis=1)

    bands = []
    for t in ("HIGH", "MEDIUM", "LOW"):
        g = top[top["_tier"] == t]
        n = int(len(g))
        wins = int(g["is_winner"].astype(int).sum()) if n else 0
        staked = n * STAKE
        returned = float((g[g["is_winner"].astype(bool)]["_sp"] * STAKE).sum()) if wins else 0.0
        pnl = returned - staked
        roi = (pnl / staked * 100) if staked else 0.0
        bands.append({
            "tier": t,
            "races_picked": n,
            "wins": wins,
            "strike_rate": round((wins / n * 100) if n else 0.0, 2),
            "staked": staked,
            "returned": round(returned, 2),
            "pnl": round(pnl, 2),
            "roi": round(roi, 2),
            "note": "NO_BET in live system" if t == "LOW" else "stakeable",
        })
    return bands


def summarize_results(results, df):
    n_races = df.groupby(["race_date", "track", "race_number"]).ngroups
    n_runners = len(df)
    dates = sorted(df["race_date"].astype(str).unique())

    strategy_rows = []
    stat_blocks = []
    for label, _ in STRATEGIES:
        bets = results[label]["bets"]
        n_bets = len(bets)
        wins = sum(1 for b in bets if b["won"])
        staked = n_bets * STAKE
        returned = sum(STAKE * b["sp"] for b in bets if b["won"])
        pnl = returned - staked
        roi = (pnl / staked * 100) if staked else 0.0

        # Full statistics block (CIs, drawdown, streaks, concentration,
        # reportability) from the shared roi_stats module. CIs and
        # reportability are on NET returns per the settlement contract.
        won = [1 if b["won"] else 0 for b in bets]
        odds = [b["sp"] for b in bets]
        stats = roi_stats.summarise_bets(won, odds, commission=COMMISSION)
        # roi/02 + roi/01 acceptance #3: net ROI at BOTH MBR rates, so the
        # 8% vs 10% comparison falls out of one summary. CI/reportability
        # above are computed at COMMISSION (0.08) per the settlement contract.
        if n_bets:
            stats["roi_net_8pct"] = round(float(np.mean(
                roi_stats.per_bet_returns(won, odds, commission=0.08))) * 100.0, 2)
            stats["roi_net_10pct"] = round(float(np.mean(
                roi_stats.per_bet_returns(won, odds, commission=0.10))) * 100.0, 2)
        else:
            stats["roi_net_8pct"] = stats["roi_net_10pct"] = None
        stat_blocks.append(stats)

        row = {
            "label": label,
            "bets": n_bets,
            "wins": wins,
            "strike_rate": (wins / n_bets * 100) if n_bets else 0.0,
            "staked": staked,
            "returned": returned,
            "pnl": pnl,
            "roi": roi,
        }
        row.update({k: stats[k] for k in (
            "roi_gross", "roi_net", "roi_net_8pct", "roi_net_10pct",
            "se", "ci95", "ci95_normal", "z",
            "p_roi_le_zero", "max_drawdown", "max_losing_streak",
            "expected_max_losing_streak", "roi_minus_top1", "roi_minus_top2",
            "roi_minus_top3", "reportable", "reportable_status",
            "reportable_reason", "bootstrap")})
        strategy_rows.append(row)

    summary = {
        "date_from": dates[0] if dates else None,
        "date_to": dates[-1] if dates else None,
        "races": n_races,
        "runners": n_runners,
        "tracks": sorted(df["track"].unique().tolist()),
        "strategies": strategy_rows,
        "commission_rate": COMMISSION,
    }
    # Sweep labelling: >1 strategy on one window needs a selection-bias caveat.
    mc = roi_stats.multi_comparison_block(stat_blocks)
    if mc is not None:
        summary["multi_comparison"] = mc
    if "norm_prob" in df.columns:
        summary["calibration"] = _compute_calibration_bins(df)
        summary["confidence_bands"] = _compute_confidence_bands(df)
    return summary


def print_results(results, df):
    summary = summarize_results(results, df)
    n_races = df.groupby(["race_date", "track", "race_number"]).ngroups
    n_runners = len(df)
    dates = sorted(df["race_date"].astype(str).unique())

    print("\n" + "=" * 80)
    print("  V2 ENSEMBLE MODEL BACKTEST — RECENT METRO RACES")
    print("=" * 80)
    print(f"\n  Dates: {dates[0]} to {dates[-1]}")
    print(f"  Races: {n_races} | Runners: {n_runners}")
    print(f"  Tracks: {', '.join(sorted(df['track'].unique()))}")
    print(f"  Staking: Flat ${STAKE} per bet | Net ROI/CI at {summary.get('commission_rate', 0)*100:.0f}% commission on winnings")

    date_track = df.groupby(["race_date", "track"]).agg(
        races=("race_number", "nunique"),
        runners=("horse_name", "count"),
    ).reset_index()
    print(f"\n  {'Date':<12} {'Track':<25} {'Races':>5} {'Runners':>7}")
    print(f"  {'-'*12} {'-'*25} {'-'*5} {'-'*7}")
    for _, row in date_track.iterrows():
        print(f"  {str(row['race_date']):<12} {row['track']:<25} {row['races']:>5} {row['runners']:>7}")

    print(f"\n\n  {'Strategy':<35} {'Bets':>5} {'Wins':>5} {'SR%':>7} {'Staked':>9} {'Return':>9} {'P&L':>10} {'ROI':>8}  {'ROI net':>8}  {'95% CI (net)':>18}  Status")
    print(f"  {'-'*35} {'-'*5} {'-'*5} {'-'*7} {'-'*9} {'-'*9} {'-'*10} {'-'*8}  {'-'*8}  {'-'*18}  {'-'*13}")

    for row in summary["strategies"]:
        label = row["label"]
        if row["bets"] == 0:
            print(f"  {label:<35} {'0':>5} {'0':>5} {'—':>7} {'—':>9} {'—':>9} {'—':>10} {'—':>8}  {'—':>8}  {'—':>18}  NOT_REPORTABLE")
            continue
        ci = row.get("ci95") or [None, None]
        ci_str = (f"[{ci[0]:+.1f}, {ci[1]:+.1f}]%"
                  if None not in ci else "—")
        net = row.get("roi_net")
        net_str = f"{net:+.1f}%" if net is not None else "—"
        print(f"  {label:<35} {row['bets']:>5} {row['wins']:>5} {row['strike_rate']:>6.1f}% "
              f"${row['staked']:>7,} ${row['returned']:>7,.0f} ${row['pnl']:>+9,.0f} "
              f"{row['roi']:>+7.1f}%  {net_str:>8}  {ci_str:>18}  {row['reportable_status']}")

    mc = summary.get("multi_comparison")
    if mc:
        print(f"\n  MULTI-COMPARISON: {mc['n_strategies']} strategies on one window — "
              f"Bonferroni requires z >= {mc['bonferroni_z_required']}, "
              f"best observed z = {mc['best_observed_z']} ({mc['verdict']})")

    print("\n\n" + "=" * 80)
    print("  DETAILED BET LOG — All strategies")
    print("=" * 80)

    for label, _ in STRATEGIES:
        bets = results[label]["bets"]
        if not bets:
            continue

        wins = sum(1 for b in bets if b["won"])
        n_bets = len(bets)
        staked = n_bets * STAKE
        returned = sum(STAKE * b["sp"] for b in bets if b["won"])
        pnl = returned - staked
        roi = pnl / staked * 100 if staked > 0 else 0

        print(f"\n  {label} ({n_bets} bets, {wins}W, ROI: {roi:+.1f}%)")
        print(f"  {'Date':<12} {'Track':<22} {'R':>2} {'Horse':<25} {'SP':>6} {'Prob':>6} {'Pos':>4} {'Result':>8}")
        print(f"  {'-'*12} {'-'*22} {'-'*2} {'-'*25} {'-'*6} {'-'*6} {'-'*4} {'-'*8}")

        running_pnl = 0
        for b in sorted(bets, key=lambda x: (x["date"], x["track"], x["race"])):
            result_str = f"WON +${STAKE * b['sp'] - STAKE:.0f}" if b["won"] else f"LOST -${STAKE}"
            running_pnl += (STAKE * b["sp"] - STAKE) if b["won"] else -STAKE
            prob_str = f"{b['prob']*100:.1f}%" if "prob" in b else "—"
            print(f"  {b['date']:<12} {b['track']:<22} {b['race']:>2} {b['horse']:<25} ${b['sp']:>5.1f} {prob_str:>6} {b['position']:>4} {result_str:>8}")

        print(f"  {'':>12} {'':>22} {'':>2} {'':>25} {'':>6} {'':>6} {'':>4} {'Running':>8}")
        print(f"  {'':>12} {'':>22} {'':>2} {'TOTAL':<25} {'':>6} {'':>6} {'':>4} ${running_pnl:>+7,.0f}")

    print("\n\n" + "=" * 80)
    print("  BY TRACK BREAKDOWN (All $2-$20 positive edge)")
    print("=" * 80)

    all_bets = results["All $2-$20 positive edge"]["bets"]
    if all_bets:
        track_groups = defaultdict(list)
        for b in all_bets:
            track_groups[b["track"]].append(b)

        print(f"\n  {'Track':<25} {'Bets':>5} {'Wins':>5} {'SR%':>7} {'P&L':>10} {'ROI':>8}")
        print(f"  {'-'*25} {'-'*5} {'-'*5} {'-'*7} {'-'*10} {'-'*8}")

        for track in sorted(track_groups.keys()):
            tb = track_groups[track]
            tw = sum(1 for b in tb if b["won"])
            ts = len(tb) * STAKE
            tr = sum(STAKE * b["sp"] for b in tb if b["won"])
            print(f"  {track:<25} {len(tb):>5} {tw:>5} {tw/len(tb)*100:>6.1f}% ${tr-ts:>+9,.0f} {(tr-ts)/ts*100:>+7.1f}%")

    return summary


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest the v2 ensemble model on metro races.")
    parser.add_argument("date_from", nargs="?", default="2026-02-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("date_to", nargs="?", default="2026-02-28", help="End date (YYYY-MM-DD)")
    parser.add_argument("--model-path", default=MODEL_PATH, help="Path to the model artifact to backtest.")
    parser.add_argument("--summary-json", help="Optional path to write machine-readable backtest summary JSON.")
    return parser.parse_args()


def main():
    args = parse_args()
    model_path = os.path.abspath(args.model_path)
    print("Loading v2 ensemble model...")
    payload = load_model(model_path)
    print(f"  Model version: {payload.get('version', '?')}")
    print(f"  Trained at: {payload.get('trained_at', '?')}")
    print(f"  Features: {len(payload['feature_columns'])}")

    date_from = args.date_from
    date_to = args.date_to

    print(f"\nLoading metro race data ({date_from} to {date_to})...")
    df = load_metro_data(date_from, date_to)

    if df.empty:
        print("ERROR: No metro race data found for this period.")
        sys.exit(1)

    print(f"  Loaded {len(df)} runners across {df.groupby(['race_date','track','race_number']).ngroups} races")

    print("\nRunning backtest...")
    results, df = run_backtest(df, payload)
    summary = print_results(results, df)
    if args.summary_json:
        with open(args.summary_json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
    print()


if __name__ == "__main__":
    main()
