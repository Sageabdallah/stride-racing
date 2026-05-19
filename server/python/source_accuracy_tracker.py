#!/usr/bin/env python3
"""
Joins consensus_mentions against race_results_history to determine
which tipster picks were winners. Stores results in source_accuracy table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "intelligence"))

from intelligence.common import (
    get_connection,
    normalize_name,
    normalize_track,
)


def track_tipster_accuracy(date_str: str, dry_run: bool = False) -> None:
    """
    Join consensus_mentions for a date against race_results_history.
    Record was_winner = (position == 1) per tip.
    """
    from dotenv import load_dotenv
    load_dotenv()

    conn = get_connection()

    mentions = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, race_date, track, race_number, horse_name, horse_name_norm,
                          source_name, source_bucket, confidence_language,
                          tipster_id, is_panel_source
                   FROM consensus_mentions
                   WHERE race_date = %s""",
                (date_str,),
            )
            columns = [desc[0] for desc in cur.description]
            for row in cur.fetchall():
                mentions.append(dict(zip(columns, row)))
    except Exception as e:
        print(f"[ACCURACY] Failed to load mentions: {e}", file=sys.stderr)
        conn.close()
        return

    if not mentions:
        print(f"[ACCURACY] No consensus_mentions for {date_str}. Exiting.", file=sys.stderr)
        conn.close()
        return

    print(f"[ACCURACY] Found {len(mentions)} mentions for {date_str}", file=sys.stderr)

    results_lookup: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT track, race_number, horse_name, position, sp_odds
                   FROM race_results_history
                   WHERE race_date = %s""",
                (date_str,),
            )
            for row in cur.fetchall():
                track, race_num, horse, position, sp_odds = row
                key = f"{normalize_track(track)}|{race_num}|{normalize_name(horse)}"
                results_lookup[key] = {
                    "position": position,
                    "sp_odds": float(sp_odds) if sp_odds else None,
                }
    except Exception as e:
        print(f"[ACCURACY] Failed to load results: {e}", file=sys.stderr)
        conn.close()
        return

    if not results_lookup:
        print(f"[ACCURACY] No race_results_history for {date_str}. Results may not be available yet.", file=sys.stderr)
        conn.close()
        return

    print(f"[ACCURACY] Found {len(results_lookup)} result rows for {date_str}", file=sys.stderr)

    accuracy_rows = []
    matched = 0
    winners = 0

    for m in mentions:
        track_norm = normalize_track(m["track"])
        horse_norm = m["horse_name_norm"]
        key = f"{track_norm}|{m['race_number']}|{horse_norm}"

        result = results_lookup.get(key)
        if not result:
            continue

        matched += 1
        position = result["position"]
        was_winner = position == 1
        if was_winner:
            winners += 1

        accuracy_rows.append({
            "tipster_id": m.get("tipster_id") or m.get("source_name", "")[:50],
            "tipster_name": m.get("source_name", "")[:100],
            "source_bucket": m.get("source_bucket"),
            "race_date": date_str,
            "track": m["track"],
            "race_number": m["race_number"],
            "horse_tipped": m["horse_name"],
            "finish_position": position,
            "was_winner": was_winner,
            "starting_price": result["sp_odds"],
        })

    print(
        f"[ACCURACY] Matched {matched}/{len(mentions)} mentions to results. "
        f"Winners: {winners}",
        file=sys.stderr,
    )

    if dry_run:
        tipster_stats: dict[str, dict] = {}
        for row in accuracy_rows:
            tid = row["tipster_name"]
            if tid not in tipster_stats:
                tipster_stats[tid] = {"tips": 0, "winners": 0}
            tipster_stats[tid]["tips"] += 1
            if row["was_winner"]:
                tipster_stats[tid]["winners"] += 1

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  {'Tipster':<30} {'Tips':>6} {'Wins':>6} {'Rate':>8}", file=sys.stderr)
        print(f"  {'-'*30} {'-'*6} {'-'*6} {'-'*8}", file=sys.stderr)
        for name, stats in sorted(tipster_stats.items()):
            rate = (stats["winners"] / stats["tips"] * 100) if stats["tips"] > 0 else 0
            print(
                f"  {name:<30} {stats['tips']:>6} {stats['winners']:>6} {rate:>7.1f}%",
                file=sys.stderr,
            )
        print(f"{'='*60}\n", file=sys.stderr)
        conn.close()
        return

    try:
        with conn.cursor() as cur:
            for row in accuracy_rows:
                cur.execute(
                    """INSERT INTO source_accuracy
                       (tipster_id, tipster_name, source_bucket, race_date,
                        track, race_number, horse_tipped,
                        finish_position, was_winner, starting_price)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    (
                        row["tipster_id"], row["tipster_name"],
                        row["source_bucket"], row["race_date"],
                        row["track"], row["race_number"],
                        row["horse_tipped"],
                        row["finish_position"], row["was_winner"],
                        row["starting_price"],
                    ),
                )
        print(f"[ACCURACY] Stored {len(accuracy_rows)} accuracy records", file=sys.stderr)
    except Exception as e:
        print(f"[ACCURACY] DB write error: {e}", file=sys.stderr)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="STRIDE Source Accuracy Tracker")
    parser.add_argument("date", help="Race date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing to DB")
    args = parser.parse_args()

    track_tipster_accuracy(args.date, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
