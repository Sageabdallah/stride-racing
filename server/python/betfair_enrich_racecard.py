#!/usr/bin/env python3
"""Inject Betfair prices into the day's racecard (Phase C serve wiring).

Punting Form cards carry no odds by design, so every serve-side consumer
that reads runner["odds"] (extract_odds in run_tips_pipeline / mc_api, the
tip_time snapshot capture, read_racecard_odds in odds_movement) has been
scoring blind since the Racing API died. This script writes the current
Betfair market into the card's empty odds arrays:

    runner["odds"] = [{"bookmaker": "betfair", "win_odds": 4.2,
                       "source_api": "betfair_delayed"}]

Existing non-empty odds arrays are never overwritten: a card that already
has bookmaker quotes keeps them. Each meeting gains "_odds_source" and
"_odds_enriched_at" metadata so downstream writers can tag provenance.

Exit codes: 0 = enriched with at least one price; 1 = ran but matched zero
prices (loud, per the house rule); 2 = card missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import betfair_markets
import betfair_prices
from pf_backfill_results import norm_name

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RACECARD_PATHS = (PROJECT_ROOT / "racecards",
                  Path(__file__).resolve().parent / "racecards")


def enrich(date_str: str, prefer_direct: bool = True) -> dict:
    card_paths = [d / f"racecard_{date_str}.json" for d in RACECARD_PATHS]
    existing = [p for p in card_paths if p.exists()]
    if not existing:
        print(f"[ENRICH] no racecard_{date_str}.json in "
              f"{' or '.join(str(d) for d in RACECARD_PATHS)}", file=sys.stderr)
        return {"status": "no_card", "matched": 0}

    price_map = betfair_prices.fetch_price_map(date_str,
                                               prefer_direct=prefer_direct)
    source = price_map["source"]
    races = price_map["races"]

    meets = json.loads(existing[0].read_text())
    matched = 0
    unmatched_runners = 0
    priced_races = 0
    total_races = 0
    for meet in meets:
        meet["_odds_source"] = source
        meet["_odds_enriched_at"] = datetime.now(timezone.utc).isoformat()
        for race in meet.get("races", []):
            total_races += 1
            key = (betfair_markets.norm_track(race.get("course")
                                              or meet.get("course")),
                   int(race.get("race_number") or 0))
            market = races.get(key)
            if not market:
                continue
            race_hit = False
            for runner in race.get("runners", []):
                if runner.get("scratched"):
                    continue
                if runner.get("odds"):
                    continue  # never clobber real bookmaker quotes
                quote = market["runners"].get(norm_name(runner.get("horse")))
                if quote is None:
                    unmatched_runners += 1
                    continue
                runner["odds"] = [{"bookmaker": "betfair",
                                   "win_odds": quote["price"],
                                   "source_api": source}]
                matched += 1
                race_hit = True
            if race_hit:
                priced_races += 1

    payload = json.dumps(meets, indent=2)
    for p in card_paths:
        if p.parent.exists():
            p.write_text(payload)

    print(f"[ENRICH] {date_str}: {matched} runners priced across "
          f"{priced_races}/{total_races} races (source={source}, "
          f"unmatched={unmatched_runners})")
    return {"status": "ok" if matched else "no_prices", "matched": matched,
            "priced_races": priced_races, "total_races": total_races,
            "source": source}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write current Betfair prices into the day's racecard")
    parser.add_argument("date", nargs="?",
                        default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--db-only", action="store_true",
                        help="skip the direct Exchange path (no credentials)")
    args = parser.parse_args()
    result = enrich(args.date, prefer_direct=not args.db_only)
    if result["status"] == "no_card":
        return 2
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    sys.exit(main())
