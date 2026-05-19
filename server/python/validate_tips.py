#!/usr/bin/env python3
"""Validate tips file contract integrity."""

import argparse
import json
import sys
from pathlib import Path

RACECARDS_DIR = Path(__file__).resolve().parents[2] / "racecards"


def validate_file(path):
    errors = []
    warnings = []

    try:
        with path.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"path": str(path), "total": 0, "bet": 0, "no_bet": 0, "errors": [f"Cannot read file: {e}"], "warnings": []}

    races = data.get("races", [])
    total = len(races)

    bet = sum(1 for r in races if r.get("bet_pick"))
    no_bet = sum(1 for r in races if not r.get("bet_pick"))

    if bet + no_bet != total:
        errors.append(f"bet({bet}) + no_bet({no_bet}) = {bet + no_bet} != total({total})")

    contract = data.get("selection_contract")
    if not contract:
        warnings.append("Missing selection_contract field")
    else:
        if contract.get("bet_races") != bet:
            errors.append(f"selection_contract.bet_races={contract.get('bet_races')} but actual={bet}")
        if contract.get("no_bet_races") != no_bet:
            errors.append(f"selection_contract.no_bet_races={contract.get('no_bet_races')} but actual={no_bet}")

    for i, r in enumerate(races):
        track = r.get("track", "?")
        rn = r.get("race_number", "?")
        label = f"{track} R{rn}"

        if not r.get("coverage_pick"):
            warnings.append(f"{label}: missing coverage_pick")
        if not r.get("raw_model_leader"):
            warnings.append(f"{label}: missing raw_model_leader")

        bet_status = r.get("bet_status")
        if bet_status not in ("BET", "NO_BET", None):
            errors.append(f"{label}: invalid bet_status={bet_status!r}")

        top_picks = r.get("top_picks", [])
        for j, pick in enumerate(top_picks):
            if "should_bet" not in pick:
                warnings.append(f"{label} pick {j+1}: missing should_bet field")
                break  # only warn once per race

    return {
        "path": str(path),
        "total": total,
        "bet": bet,
        "no_bet": no_bet,
        "errors": errors,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="Validate tips file contract integrity.")
    parser.add_argument("dates", nargs="*", help="Race dates in YYYY-MM-DD format")
    parser.add_argument("--all", action="store_true", help="Validate all canonical tips files")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    if args.all:
        files = sorted(RACECARDS_DIR.glob("tips_*.json"))
        files = [f for f in files if f.stem.count("_") == 1]  # canonical only
    elif args.dates:
        files = [RACECARDS_DIR / f"tips_{d}.json" for d in args.dates]
    else:
        parser.error("Provide date(s) or --all")

    any_errors = False
    for path in files:
        if not path.exists():
            print(f"  SKIP {path.name} (not found)")
            continue

        result = validate_file(path)
        has_errors = bool(result["errors"])
        has_warnings = bool(result.get("warnings"))

        if has_errors:
            status = "FAIL"
            any_errors = True
        elif has_warnings and args.strict:
            status = "FAIL"
            any_errors = True
        elif has_warnings:
            status = "WARN"
        else:
            status = "PASS"

        print(f"  {status} {path.name}: {result['total']} races ({result['bet']} BET, {result['no_bet']} NO BET)")
        for err in result["errors"]:
            print(f"    ERROR: {err}")
        for warn in result.get("warnings", []):
            print(f"    WARN:  {warn}")

    sys.exit(1 if any_errors else 0)


if __name__ == "__main__":
    main()
