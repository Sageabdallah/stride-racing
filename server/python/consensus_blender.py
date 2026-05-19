#!/usr/bin/env python3
"""STRIDE Convergence Blender V2 — three-pillar consensus intelligence library imported by run_tips_pipeline.py for score injection and convergence gating."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "intelligence"))

from intelligence.common import (
    INTELLIGENCE_DIR,
    RACECARDS_DIR,
    get_connection,
    normalize_name,
    normalize_track,
)


def _load_weights() -> dict[str, float]:
    return {
        "stride": float(os.environ.get("STRIDE_MODEL_WEIGHT", "0.50")),
        "consensus": float(os.environ.get("CONSENSUS_WEIGHT", "0.30")),
        "market": float(os.environ.get("MARKET_SIGNAL_WEIGHT", "0.20")),
    }


STRIDE_THRESHOLD = 65.0
CONSENSUS_THRESHOLD = float(os.environ.get("CONSENSUS_LOCK_THRESHOLD", "65"))
MARKET_THRESHOLD = float(os.environ.get("MARKET_SIGNAL_THRESHOLD", "60"))

# THRESHOLD NOTE: selectionScore is on a 0-25 scale (not 0-100).
# Based on 6-day distribution (Mar 14 – Apr 06): P90=12-15, P75=10, P50=7.
# CONFIRMED >= 15 = top ~10% of scores
# CROWD_ONLY 8-14 = above median, model positive
# CROWD_ONLY_WEAK < 8 = below median, structural concerns
# These thresholds should be reviewed monthly as more data accumulates.
MIN_MODEL_SCORE_FOR_BET = 8.0
MIN_CONFIRM_CONVERGENCE_SCORE = 55.0


def blend_scores(
    stride_score: float,
    consensus_score: float,
    market_signal_score: float,
) -> float:
    """Weighted blend of all three pillars."""
    w = _load_weights()
    return round(
        (stride_score * w["stride"])
        + (consensus_score * w["consensus"])
        + (market_signal_score * w["market"]),
        2,
    )


def determine_convergence_tier(
    stride_score: float,
    consensus_score: float,
    market_signal_score: float,
) -> tuple[str, int]:
    """
    Determine convergence tier based on which pillars exceed threshold.
    Returns (tier, pillars_strong).

    Order matters — do not rearrange:
    LOCK           -> all 3 above threshold
    CONFIRM        -> STRIDE + (consensus OR market)
    FLAG           -> STRIDE only, no external confirmation
    CROWD_OVERRIDE -> consensus + market strong but STRIDE weak
    SKIP           -> insufficient convergence
    """
    s = stride_score >= STRIDE_THRESHOLD
    c = consensus_score >= CONSENSUS_THRESHOLD
    m = market_signal_score >= MARKET_THRESHOLD

    if s and c and m:
        return "LOCK", 3
    elif s and (c or m):
        return "CONFIRM", 2
    elif s and not c and not m:
        return "FLAG", 1
    elif not s and c and m:
        return "CROWD_OVERRIDE", 2
    else:
        return "SKIP", sum([s, c, m])


def calculate_consensus_injection(
    consensus_score: float,
    bucket_spread: int,
    total_mentions: int,
    independent_source_rate: float,
    vote_pct: float,
    reasoning_alignment: str | None,
) -> tuple[float, str]:
    """
    Calculate consensus injection into selection_score.
    Range: -8.0 to +12.0 points.
    Returns (injection_points, reason_string).
    """
    if consensus_score < 35.0:
        return 0.0, "no consensus data"

    if consensus_score >= 80:
        base, label = 12.0, f"strong consensus ({consensus_score:.0f})"
    elif consensus_score >= 65:
        base, label = 7.0, f"above-threshold ({consensus_score:.0f})"
    elif consensus_score >= 50:
        base, label = 3.0, f"moderate ({consensus_score:.0f})"
    elif consensus_score >= 35:
        return 0.0, "default range"
    elif consensus_score >= 20:
        base, label = -3.0, f"tipsters avoiding ({consensus_score:.0f})"
    else:
        base, label = -8.0, f"strong against ({consensus_score:.0f})"

    div_m = {0: 0.5, 1: 0.8, 2: 1.0, 3: 1.2, 4: 1.35, 5: 1.5}.get(
        min(bucket_spread, 5), 1.0
    )

    if independent_source_rate >= 0.9:
        ind_m = 1.15
    elif independent_source_rate >= 0.7:
        ind_m = 1.0
    elif independent_source_rate >= 0.5:
        ind_m = 0.85
    else:
        ind_m = 0.65

    if reasoning_alignment == "CONFIRMED":
        rea_m = 1.2
    elif reasoning_alignment == "CONTRADICTED":
        rea_m = 0.5
    else:
        rea_m = 1.0

    final = max(-8.0, min(12.0, round(base * div_m * ind_m * rea_m, 2)))
    reason = (
        f"{label} | {bucket_spread}bkt | {vote_pct:.0f}%vote | "
        f"reasoning:{reasoning_alignment or 'n/a'}"
    )
    return final, reason


def calculate_market_injection(
    market_signal_score: float,
    signal_type: str | None,
    movement_pct: float = 0,
) -> tuple[float, str]:
    """
    Calculate market injection into selection_score.
    Range: -5.0 to +8.0 points.
    Returns (injection_points, reason_string).
    """
    if signal_type == "STEAM":
        return 8.0, f"strong steam ({movement_pct:+.1f}%)"
    elif signal_type == "FIRMING":
        return 4.0, f"firming ({movement_pct:+.1f}%)"
    elif signal_type in ("STABLE", "UNKNOWN", None):
        return 0.0, f"market {(signal_type or 'unknown').lower()}"
    elif signal_type == "DRIFT":
        return -3.0, f"drifting ({movement_pct:+.1f}%)"
    elif signal_type == "STRONG_DRIFT":
        return -5.0, f"strong drift ({movement_pct:+.1f}%)"
    else:
        return 0.0, "unknown signal"


def apply_convergence_gate(
    tier: str,
    convergence_score: float,
    stride_score_raw: float,
) -> tuple[bool, str]:
    """
    Determine if a selection should actually be bet on.
    Returns (should_bet, gate_reason).

    This is the core arbitration step: FLAG picks are gated to NO_BET even when the model is confident, because absence of external confirmation is itself a signal.
    """
    if stride_score_raw < MIN_MODEL_SCORE_FOR_BET:
        return False, f"MODEL_WEAK — raw {stride_score_raw:.0f} < {MIN_MODEL_SCORE_FOR_BET:.0f}"

    if tier == "LOCK":
        return True, "LOCK — all pillars confirm"
    elif tier == "CONFIRM" and convergence_score >= MIN_CONFIRM_CONVERGENCE_SCORE:
        return True, "CONFIRM — STRIDE + external"
    elif tier == "CONFIRM":
        return False, f"CONFIRM_WEAK — conv {convergence_score:.0f} < {MIN_CONFIRM_CONVERGENCE_SCORE:.0f}"
    elif tier == "FLAG":
        return False, "FLAG — model only, no external confirmation"
    elif tier == "SKIP":
        return False, "SKIP — insufficient convergence"
    elif tier == "CROWD_OVERRIDE":
        return False, "CROWD_OVERRIDE — tracked for backtest"
    else:
        return False, f"UNKNOWN: {tier}"


def confirm_with_model(
    consensus_candidates: list[str],
    model_scores: dict[str, float],
) -> dict[str, str]:
    """
    Crowd-first confirmation: consensus picks candidates, model confirms.

    Returns per-horse classification:
    - CONFIRMED: crowd and model agree
    - CROWD_ONLY / CROWD_ONLY_WEAK: crowd picks, model weak
    - MODEL_ONLY: model picks, no crowd support
    """
    results = {}

    for horse in consensus_candidates:
        model_score = model_scores.get(horse, 0)

        if model_score >= 15:
            results[horse] = "CONFIRMED"
        elif model_score >= 8:
            results[horse] = "CROWD_ONLY"
        else:
            results[horse] = "CROWD_ONLY_WEAK"

    model_top = max(model_scores, key=model_scores.get) if model_scores else None
    if model_top and model_top not in consensus_candidates:
        results[model_top] = "MODEL_ONLY"

    return results


def crowd_bet_decision(
    classification: str,
    crowd_score: float,
) -> tuple[bool, str]:
    """
    Determine BET/NO_BET from crowd-first classification.
    Returns (should_bet, reason).
    """
    if classification == "CONFIRMED":
        return True, "CONFIRMED — crowd + model agree"
    elif classification == "CROWD_ONLY" and crowd_score > 70:
        return True, f"CROWD_ONLY — crowd overwhelming ({crowd_score:.0f})"
    elif classification == "CROWD_ONLY" and crowd_score >= 50:
        return True, f"CROWD_ONLY — crowd moderate ({crowd_score:.0f}), reduced stake"
    elif classification == "CROWD_ONLY_WEAK" and crowd_score >= 100:
        # UNANIMOUS CROWD threshold added 2026-04-06
        # Evidence: Satirically (Sandown R4) had 100% crowd consensus and won
        # while being blocked as CROWD_ONLY_WEAK under original rules.
        # One data point — monitor for 4 weeks before raising stake to STANDARD.
        return True, f"UNANIMOUS CROWD — all sources agree ({crowd_score:.0f}%), model weak, reduced stake"
    elif classification == "CROWD_ONLY_WEAK" and crowd_score > 70:
        # Strong crowd but not unanimous — still defer to backtest
        return False, f"CROWD_OVERRIDE — tracked for backtest ({crowd_score:.0f}), not live yet"
    elif classification == "CROWD_ONLY_WEAK":
        return False, f"CROWD_ONLY_WEAK — model too weak, crowd not overwhelming ({crowd_score:.0f})"
    elif classification == "MODEL_ONLY":
        return False, "MODEL_ONLY — archetype trap, no crowd support"
    else:
        return False, f"REJECTED — {classification}"


def calculate_market_confidence(
    consensus_score: float,
    market_signal_score: float,
    vote_pct: float,
    bucket_spread: int,
    independent_source_rate: float,
    signal_type: str | None,
) -> tuple[float, str, str]:
    """
    Combined confidence score for frontend display.
    60% tipster quality / 40% market signal strength.
    Returns (score, label, colour).
    """
    tipster_quality = (
        consensus_score * 0.7
        + min(vote_pct, 100) * 0.2
        + min(bucket_spread * 10, 50) * 0.1
    )

    confidence = tipster_quality * 0.6 + market_signal_score * 0.4

    if independent_source_rate < 0.5:
        confidence *= 0.7

    confidence = round(min(max(confidence, 0), 100), 2)

    if confidence >= 70:
        return confidence, "GREEN", "#10b981"
    elif confidence >= 50:
        return confidence, "AMBER", "#f59e0b"
    else:
        return confidence, "RED", "#ef4444"


def load_consensus_intelligence(
    date_str: str,
) -> tuple[dict, dict, dict[str, bool]]:
    """
    Load consensus and market signal JSON files.

    Returns:
        (consensus_data, market_data, pillars_available)
    """
    pillars = {"stride": True, "consensus": False, "market": False}
    consensus_data: dict = {}
    market_data: dict = {}

    consensus_path = INTELLIGENCE_DIR / f"consensus_{date_str}.json"
    market_path = INTELLIGENCE_DIR / f"market_signals_{date_str}.json"

    if consensus_path.exists():
        try:
            consensus_data = json.loads(consensus_path.read_text(encoding="utf-8"))
            pillars["consensus"] = bool(consensus_data)
            if pillars["consensus"]:
                total = sum(
                    sum(1 for h, d in race.items() if isinstance(d, dict) and d.get("total_mentions", 0) > 0)
                    for race in consensus_data.values()
                    if isinstance(race, dict)
                )
                print(f"  [BLENDER] Consensus loaded: {len(consensus_data)} races, {total} horses with mentions", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [BLENDER] WARNING: Failed to parse consensus file: {e}", file=sys.stderr)
    else:
        print(f"  [BLENDER] WARNING: consensus file missing for {date_str}. Consensus pillar defaults to 35.", file=sys.stderr)

    if market_path.exists():
        try:
            market_data = json.loads(market_path.read_text(encoding="utf-8"))
            pillars["market"] = bool(market_data)
            if pillars["market"]:
                notable = sum(
                    sum(1 for h, d in race.items() if isinstance(d, dict) and d.get("signal_type") not in ("STABLE", "UNKNOWN", None))
                    for race in market_data.values()
                    if isinstance(race, dict)
                )
                print(f"  [BLENDER] Market signals loaded: {len(market_data)} races, {notable} notable movements", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [BLENDER] WARNING: Failed to parse market signals file: {e}", file=sys.stderr)
    else:
        print(f"  [BLENDER] WARNING: market signals file missing for {date_str}. Market pillar defaults to 50.", file=sys.stderr)

    return consensus_data, market_data, pillars


def store_convergence_output(
    date_str: str,
    rows: list[dict],
    pillars_available: dict[str, bool],
) -> None:
    """Write convergence output rows to DB (V2 columns included)."""
    if not rows:
        return
    conn = get_connection()
    try:
        pillars_json = json.dumps(pillars_available)
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO convergence_output
                       (race_date, track, race_number, horse_name, horse_name_norm,
                        stride_score, consensus_score, market_signal_score,
                        final_convergence_score, convergence_tier, pillars_strong,
                        pillars_available,
                        field_size, stride_score_raw, consensus_injection,
                        market_injection, stride_score_final,
                        market_confidence_score, market_confidence_label,
                        vote_pct, tipsters_polled)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (race_date, track, race_number, horse_name_norm)
                       DO UPDATE SET
                        stride_score = EXCLUDED.stride_score,
                        consensus_score = EXCLUDED.consensus_score,
                        market_signal_score = EXCLUDED.market_signal_score,
                        final_convergence_score = EXCLUDED.final_convergence_score,
                        convergence_tier = EXCLUDED.convergence_tier,
                        pillars_strong = EXCLUDED.pillars_strong,
                        pillars_available = EXCLUDED.pillars_available,
                        field_size = EXCLUDED.field_size,
                        stride_score_raw = EXCLUDED.stride_score_raw,
                        consensus_injection = EXCLUDED.consensus_injection,
                        market_injection = EXCLUDED.market_injection,
                        stride_score_final = EXCLUDED.stride_score_final,
                        market_confidence_score = EXCLUDED.market_confidence_score,
                        market_confidence_label = EXCLUDED.market_confidence_label,
                        vote_pct = EXCLUDED.vote_pct,
                        tipsters_polled = EXCLUDED.tipsters_polled""",
                    (
                        date_str, r["track"], r["race_number"],
                        r["horse_name"], normalize_name(r["horse_name"]),
                        r.get("stride_score", 0),
                        r.get("consensus_score", 35),
                        r.get("market_signal_score", 50),
                        r["convergence_score"],
                        r["convergence_tier"],
                        r.get("pillars_strong", 0),
                        pillars_json,
                        r.get("field_size"),
                        r.get("stride_score_raw"),
                        r.get("consensus_injection"),
                        r.get("market_injection"),
                        r.get("stride_score_final"),
                        r.get("market_confidence_score"),
                        r.get("market_confidence_label"),
                        r.get("vote_pct"),
                        r.get("tipsters_polled"),
                    ),
                )
        print(f"  [BLENDER] Stored {len(rows)} convergence rows", file=sys.stderr)
    except Exception as e:
        print(f"  [BLENDER] DB write error: {e}", file=sys.stderr)
    finally:
        conn.close()


def _run_report(date_str: str) -> None:
    """
    Read existing tips + intelligence, blend, and print report.
    Does NOT modify tips file — review/analysis only.
    """
    from dotenv import load_dotenv
    load_dotenv()

    consensus_data, market_data, pillars = load_consensus_intelligence(date_str)

    tips_path = RACECARDS_DIR / f"tips_{date_str}.json"
    tips_data = {}
    if tips_path.exists():
        try:
            tips_data = json.loads(tips_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if not tips_data:
        print(f"[BLENDER] No tips file found for {date_str}. Cannot generate report.", file=sys.stderr)
        return

    print(f"\n{'='*100}")
    print(f"  STRIDE Convergence Report V2 — {date_str}")
    print(f"  Pillars: STRIDE={'Y' if pillars['stride'] else 'N'} | "
          f"Consensus={'Y' if pillars['consensus'] else 'N'} | "
          f"Market={'Y' if pillars['market'] else 'N'}")
    print(f"{'='*100}")

    tier_counts = {"LOCK": 0, "CONFIRM": 0, "FLAG": 0, "CROWD_OVERRIDE": 0, "SKIP": 0}
    gated_count = 0

    for race in tips_data.get("races", []):
        track = race.get("track", "")
        race_num = race.get("race_number", 0)
        track_key = normalize_track(track)
        race_key = f"{track_key}_R{race_num}"
        race_name = race.get("race_name", "")

        print(f"\n  {track} R{race_num} — {race_name}")
        print(
            f"  {'Horse':<25} {'Raw':>6} {'C.Inj':>6} {'M.Inj':>6} {'Final':>6} "
            f"{'Cons':>6} {'Mkt':>6} {'Conv':>6} {'Tier':<14} {'Gate':<6} {'MCS':>5}"
        )
        print(f"  {'-'*25} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*14} {'-'*6} {'-'*5}")

        for pick in race.get("top_picks", []):
            horse = pick.get("horse", "")
            stride_raw = pick.get("selection_score", 0)

            cons = consensus_data.get(race_key, {}).get(horse, {})
            mkt = market_data.get(race_key, {}).get(horse, {})
            consensus_score = cons.get("consensus_score", 35.0) if isinstance(cons, dict) else 35.0
            market_signal_score = mkt.get("market_signal_score", 50.0) if isinstance(mkt, dict) else 50.0

            c_inj, c_reason = calculate_consensus_injection(
                consensus_score,
                cons.get("bucket_spread", 0) if isinstance(cons, dict) else 0,
                cons.get("total_mentions", 0) if isinstance(cons, dict) else 0,
                cons.get("independent_source_rate", 1.0) if isinstance(cons, dict) else 1.0,
                cons.get("vote_pct", 0) if isinstance(cons, dict) else 0,
                cons.get("reasoning_alignment") if isinstance(cons, dict) else None,
            )
            m_inj, m_reason = calculate_market_injection(
                market_signal_score,
                mkt.get("signal_type") if isinstance(mkt, dict) else None,
                mkt.get("movement_pct", 0) if isinstance(mkt, dict) else 0,
            )

            stride_final = stride_raw + c_inj + m_inj
            conv = blend_scores(stride_final, consensus_score, market_signal_score)
            tier, pillars_strong = determine_convergence_tier(stride_final, consensus_score, market_signal_score)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

            should_bet, gate_reason = apply_convergence_gate(tier, conv, stride_raw)
            if not should_bet:
                gated_count += 1

            mcs, mcs_label, _ = calculate_market_confidence(
                consensus_score, market_signal_score,
                cons.get("vote_pct", 0) if isinstance(cons, dict) else 0,
                cons.get("bucket_spread", 0) if isinstance(cons, dict) else 0,
                cons.get("independent_source_rate", 1.0) if isinstance(cons, dict) else 1.0,
                mkt.get("signal_type") if isinstance(mkt, dict) else None,
            )

            marker = ""
            if tier == "LOCK":
                marker = " ***"
            elif tier == "FLAG":
                marker = " !"
            elif not should_bet:
                marker = " X"

            gate_str = "BET" if should_bet else "NO"

            print(
                f"  {horse:<25} {stride_raw:>6.1f} {c_inj:>+6.1f} {m_inj:>+6.1f} {stride_final:>6.1f} "
                f"{consensus_score:>6.1f} {market_signal_score:>6.1f} {conv:>6.1f} "
                f"{tier:<14} {gate_str:<6} {mcs:>5.0f}{marker}"
            )

    print(f"\n{'='*100}")
    print(
        f"  Summary: {tier_counts['LOCK']} LOCK | {tier_counts['CONFIRM']} CONFIRM | "
        f"{tier_counts['FLAG']} FLAG | {tier_counts['CROWD_OVERRIDE']} CROWD_OVERRIDE | "
        f"{tier_counts['SKIP']} SKIP | {gated_count} gated NO_BET"
    )
    print(f"{'='*100}\n")


def main():
    parser = argparse.ArgumentParser(description="STRIDE Convergence Blender V2")
    parser.add_argument("date", help="Race date YYYY-MM-DD")
    parser.add_argument("--report", action="store_true", help="Print convergence report from existing tips")
    parser.add_argument("--dry-run", action="store_true", help="Same as --report (review only)")
    args = parser.parse_args()

    if args.report or args.dry_run:
        _run_report(args.date)
    else:
        print("[BLENDER] No action specified. Use --report for analysis or import as library module.", file=sys.stderr)


if __name__ == "__main__":
    main()
