"""
Within-race relative market-position features (Phase 5).

The market is the strongest single winner predictor in Australian racing:
favourites win ~35% of races, second favourites ~20%, third favourites ~13.5%
(long-run national samples). The trained ensemble previously saw only each
runner's own price (market_odds) — never its position *within the field* —
so the model had to rediscover the favourite/second-favourite ladder from a
raw price that is not comparable across field sizes and overrounds.

These helpers compute three field-relative features. Names and semantics are
deliberately identical to what mc_api.extract_ml_features already computes
for its adjustment layer, so adding them to the FEATURE_COLUMNS contract
keeps BOTH inference paths (run_tips_pipeline and mc_api) in train/serve
parity without touching mc_api:

    fair_implied_prob   overround-corrected implied win chance, 0-100 scale:
                        (1/odds) / sum(1/odds over quoted runners) * 100
    odds_rank           1 = market favourite; ties share the lower rank
                        (strictly-shorter-price count + 1, matching mc_api)
    odds_rank_pct       odds_rank / number of runners in the field

A runner with no usable quote (missing or <= 1.0), or a field with fewer
than two quoted runners, gets 0 for all three — 0 is out of range for every
feature (real shares/ranks start above 0), so tree models can isolate the
"no market" case on its own branch.

Consumed by retrain_v2.build_feature_matrix (training) and
run_tips_pipeline (inference). Self-test: python relative_market.py
"""

from typing import List, Optional

import numpy as np
import pandas as pd


def compute_field_relative_market(odds_list: List[Optional[float]]) -> List[dict]:
    """
    Compute the three relative-market features for one race field.

    odds_list: decimal odds per runner in field order (None/<=1 = no quote).
    Returns one dict per runner, aligned with the input order.
    """
    n_field = len(odds_list)
    quoted = [(i, float(o)) for i, o in enumerate(odds_list)
              if o is not None and _is_valid_odds(o)]

    defaults = [{"fair_implied_prob": 0.0, "odds_rank": 0.0, "odds_rank_pct": 0.0}
                for _ in range(n_field)]
    if len(quoted) < 2 or n_field < 2:
        return defaults

    implied_sum = sum(1.0 / o for _, o in quoted)
    if implied_sum <= 0:
        return defaults

    quoted_odds = [o for _, o in quoted]
    out = defaults
    for i, o in quoted:
        rank = 1 + sum(1 for other in quoted_odds if other < o)
        out[i] = {
            "fair_implied_prob": round((1.0 / o) / implied_sum * 100.0, 4),
            "odds_rank": float(rank),
            "odds_rank_pct": round(rank / n_field, 4),
        }
    return out


def _is_valid_odds(o) -> bool:
    try:
        v = float(o)
    except (TypeError, ValueError):
        return False
    return np.isfinite(v) and v > 1.0


def add_relative_market_features(
    out: pd.DataFrame,
    df: pd.DataFrame,
    odds_col: str = "_effective_odds",
    race_date_col: str = "race_date",
    track_col: str = "track",
    race_number_col: str = "race_number",
) -> pd.DataFrame:
    """
    Group `df` into races by (race_date, track, race_number) — the same key
    retrain_v2 uses for pace features — and write fair_implied_prob /
    odds_rank / odds_rank_pct into `out` (index-aligned with df).
    """
    for col in ("fair_implied_prob", "odds_rank", "odds_rank_pct"):
        out[col] = 0.0

    required = {odds_col, race_date_col, track_col, race_number_col}
    if not required.issubset(df.columns):
        return out

    key = pd.MultiIndex.from_arrays([
        df[race_date_col].astype(str),
        df[track_col].astype(str).str.strip().str.lower(),
        pd.to_numeric(df[race_number_col], errors="coerce").fillna(0).astype(int),
    ])
    odds = pd.to_numeric(df[odds_col], errors="coerce")

    n_races = 0
    for _, idx in pd.Series(range(len(df)), index=key).groupby(level=[0, 1, 2]):
        positions = idx.values
        field_odds = [
            (o if np.isfinite(o) and o > 1.0 else None)
            for o in odds.iloc[positions].values
        ]
        feats = compute_field_relative_market(field_odds)
        rows = df.index[positions]
        out.loc[rows, "fair_implied_prob"] = [f["fair_implied_prob"] for f in feats]
        out.loc[rows, "odds_rank"] = [f["odds_rank"] for f in feats]
        out.loc[rows, "odds_rank_pct"] = [f["odds_rank_pct"] for f in feats]
        n_races += 1

    print(f"  Relative market features computed for {n_races:,} races")
    return out


if __name__ == "__main__":
    print("=" * 60)
    print("relative_market self-test")
    print("=" * 60)

    # Basic field: $2 fav, $4, $8, unquoted
    feats = compute_field_relative_market([2.0, 4.0, 8.0, None])
    assert feats[0]["odds_rank"] == 1.0 and feats[1]["odds_rank"] == 2.0
    assert feats[3] == {"fair_implied_prob": 0.0, "odds_rank": 0.0, "odds_rank_pct": 0.0}
    total_share = sum(f["fair_implied_prob"] for f in feats)
    assert abs(total_share - 100.0) < 0.01, total_share
    assert feats[0]["fair_implied_prob"] > 50.0  # fav majority share of 2/4/8 book
    assert feats[0]["odds_rank_pct"] == 0.25  # rank 1 of a 4-horse field
    print(f"  basic field: shares sum to {total_share:.2f}, fav share "
          f"{feats[0]['fair_implied_prob']:.1f}%")

    # Tie handling matches mc_api (strictly-shorter count + 1)
    feats = compute_field_relative_market([3.0, 3.0, 6.0])
    assert feats[0]["odds_rank"] == 1.0 and feats[1]["odds_rank"] == 1.0
    assert feats[2]["odds_rank"] == 3.0
    print("  tie handling: co-favourites both rank 1, next horse rank 3")

    # Degenerate fields return zeros
    assert compute_field_relative_market([2.5])[0]["odds_rank"] == 0.0
    assert compute_field_relative_market([None, 1.0])[0]["fair_implied_prob"] == 0.0
    print("  degenerate fields: all-zero defaults")

    # DataFrame path groups by race and aligns by index
    df = pd.DataFrame({
        "race_date": ["2026-04-18"] * 3 + ["2026-04-18"] * 2,
        "track": ["Flemington"] * 3 + ["Randwick"] * 2,
        "race_number": [1, 1, 1, 4, 4],
        "_effective_odds": [2.0, 4.0, 8.0, 1.8, 3.5],
    }, index=[10, 20, 30, 40, 50])
    out = pd.DataFrame(index=df.index)
    out = add_relative_market_features(out, df)
    assert out.loc[10, "odds_rank"] == 1.0 and out.loc[30, "odds_rank"] == 3.0
    assert out.loc[40, "odds_rank"] == 1.0 and out.loc[50, "odds_rank"] == 2.0
    r4_sum = out.loc[[40, 50], "fair_implied_prob"].sum()
    assert abs(r4_sum - 100.0) < 0.01
    print(f"  dataframe path: per-race shares sum to 100 (R4 sum {r4_sum:.2f})")

    print("All relative_market self-tests passed.")
