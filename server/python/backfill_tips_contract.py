#!/usr/bin/env python3
"""
Backfill explicit race-pick contract fields into saved tips files.

Used when the selection contract changes (raw_model_leader / bet_pick / coverage_pick / NO BET status) but the underlying race scoring does not need to be recomputed from scratch.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from run_tips_pipeline import (
    annotate_pick_contract,
    choose_bet_race_pick,
    choose_coverage_race_pick,
    derive_no_bet_reason,
    evaluate_bet_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RACECARDS_DIR = PROJECT_ROOT / "racecards"


def _safe_num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        if parsed != parsed:
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _build_raw_model_leader(race: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    explicit = race.get("raw_model_leader")
    if isinstance(explicit, dict) and explicit.get("horse"):
        return explicit

    full_field = race.get("full_field") or []
    if not full_field:
        return None

    leader = deepcopy(full_field[0])
    leader["rank"] = 1
    horse_name = leader.get("horse")
    should_bet, _ = evaluate_bet_candidate(leader)
    return annotate_pick_contract(
        leader,
        model_leader_horse=horse_name,
        selection_origin="raw_model_leader",
        selection_origin_reason="Strongest pre-filter model signal in the race.",
        should_bet=should_bet,
    )


def backfill_race_contract(race: Dict[str, Any]) -> Dict[str, Any]:
    updated = deepcopy(race)

    # A race that failed inside the live pipeline (run_tips_pipeline.py's
    # per-race exception handler) carries bet_status="ERROR" and an empty
    # full_field/top_picks — there is nothing to rebuild a pick from. Falling
    # through to the rebuild path below would silently relabel it NO_BET via
    # derive_no_bet_reason(None, None)'s generic "no raw model leader" text,
    # discarding the real error. Leave it exactly as the pipeline wrote it.
    if updated.get("bet_status") == "ERROR":
        return updated

    # Reconciled Phase -1 files are authoritative and this key is always
    # present on them (dict when the crowd/risk gate refused the bet, None
    # otherwise) — `in updated` is a file-format check, not a "was it
    # refused" check, and deliberately short-circuits the rebuild path below
    # for every race a current run produces, refused or not. Do not narrow
    # this to `isinstance(refused, dict)`: run_tips_pipeline.py's live
    # bet_pick already carries decision_contract.reconcile_crowd_bet's
    # crowd-gate fields (crowd_score, convergence_tier, stake_recommendation,
    # etc. — see decision_contract._GATE_FIELDS) and the drawdown breaker's
    # actual stake; choose_bet_race_pick() below reconstructs a bet_pick
    # from raw_model_leader alone and would silently drop all of that.
    # Step-3 backfill (the rebuild path) exists for legacy pre-Phase -1
    # files only, which never had this key at all.
    if "refused_bet_pick" in updated:
        refused = updated.get("refused_bet_pick")
        if isinstance(refused, dict):
            updated["bet_pick"] = None
            updated["bet_status"] = "NO_BET"
            updated["bet_status_reason"] = str(
                updated.get("bet_status_reason")
                or refused.get("selection_origin_reason")
                or refused.get("refusal_reason")
                or "Bet refused by a reconciled decision gate."
            )
        return updated
    if updated.get("bet_status") == "NO_BET":
        updated["bet_pick"] = None
        return updated

    full_field = updated.get("full_field") or []
    top_picks = updated.get("top_picks") or []
    raw_model_leader = _build_raw_model_leader(updated)
    model_leader_horse = raw_model_leader.get("horse") if raw_model_leader else (full_field[0].get("horse") if full_field else None)

    if not top_picks and raw_model_leader:
        top_picks = [deepcopy(raw_model_leader)]
        updated["top_picks"] = top_picks

    coverage_pick = choose_coverage_race_pick(top_picks, model_leader_horse, full_field) if (top_picks or full_field) else None
    bet_pick = choose_bet_race_pick(raw_model_leader) if raw_model_leader else None

    updated["raw_model_leader"] = raw_model_leader
    updated["bet_pick"] = bet_pick
    updated["coverage_pick"] = coverage_pick
    updated["primary_pick"] = coverage_pick

    if bet_pick:
        updated["bet_status"] = "BET"
        updated["bet_status_reason"] = bet_pick.get("selection_origin_reason") or "BET — raw model leader remains the only bettable horse."
    else:
        updated["bet_status"] = "NO_BET"
        updated["bet_status_reason"] = derive_no_bet_reason(raw_model_leader, coverage_pick)

    for pick in top_picks:
        if pick.get("selection_origin"):
            continue
        pick_horse = str(pick.get("horse", "")).strip().lower()
        leader_name = str(model_leader_horse or "").strip().lower()
        has_real = bool(pick.get("has_real_market_odds")) and _safe_num(pick.get("odds"), 0.0) > 1
        has_edge = _safe_num(pick.get("edge_pct"), 0.0) > 0
        matches_leader = bool(leader_name) and pick_horse == leader_name
        clears_bet_guardrail, bet_guardrail_reason = evaluate_bet_candidate(pick)

        if clears_bet_guardrail and matches_leader:
            pick["selection_origin"] = "model_backed"
            pick["selection_origin_reason"] = bet_guardrail_reason
            pick["should_bet"] = True
        elif has_real and has_edge and matches_leader:
            pick["selection_origin"] = "tip_only"
            pick["selection_origin_reason"] = bet_guardrail_reason
            pick["should_bet"] = False
        elif has_real and has_edge:
            pick["selection_origin"] = "filtered_substitute"
            pick["selection_origin_reason"] = "Filtered contender with edge, but not the raw model leader."
            pick["should_bet"] = False
        elif not has_real:
            pick["selection_origin"] = "market_unavailable"
            pick["selection_origin_reason"] = "No real market quote available."
            pick["should_bet"] = False
        else:
            pick["selection_origin"] = "tip_only"
            pick["selection_origin_reason"] = "Guide pick without a clear bettable edge."
            pick["should_bet"] = False
        pick["matches_model_leader"] = matches_leader
        pick["model_leader_horse"] = model_leader_horse

    return updated


def backfill_file(date_str: str) -> Dict[str, Any]:
    path = RACECARDS_DIR / f"tips_{date_str}.json"
    if not path.exists():
        raise FileNotFoundError(f"No tips file found for {date_str}: {path}")

    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    races = payload.get("races") or []
    updated_races = [backfill_race_contract(race) for race in races]
    payload["races"] = updated_races

    bet_races = sum(1 for race in updated_races if race.get("bet_pick"))
    no_bet_races = sum(1 for race in updated_races if not race.get("bet_pick"))
    payload["selection_contract"] = {
        "version": "v2-explicit-bet-coverage",
        "bet_races": bet_races,
        "no_bet_races": no_bet_races,
    }

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=path.stem
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        Path(tmp_path).replace(path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    return {
        "date": date_str,
        "path": str(path),
        "race_count": len(updated_races),
        "bet_races": bet_races,
        "no_bet_races": no_bet_races,
    }


def list_tip_dates() -> List[str]:
    return sorted(
        file.stem.replace("tips_", "")
        for file in RACECARDS_DIR.glob("tips_*.json")
        if file.stem.count("_") == 1
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill explicit bet/coverage pick fields into saved tips files.")
    parser.add_argument("dates", nargs="*", help="Race dates in YYYY-MM-DD format. Defaults to all canonical tips files.")
    args = parser.parse_args()

    dates = args.dates or list_tip_dates()
    if not dates:
        raise SystemExit("No canonical tips files found to backfill.")

    summaries = [backfill_file(date_str) for date_str in dates]
    for summary in summaries:
        print(
            f"[backfill_tips_contract] {summary['date']}: "
            f"{summary['race_count']} races | {summary['bet_races']} BET | {summary['no_bet_races']} NO BET"
        )


if __name__ == "__main__":
    main()
