#!/usr/bin/env python3
"""Backfill selection_ledger prices from tip_time snapshots (WP-1 step 4).

Ledger rows written while the serve side was blind carry no price_taken and
no price_source. Where a tip_time snapshot exists for the same runner, that
IS the price the tip was struck at, so fill it in and tag the row
betfair_delayed_backfill. Where no snapshot exists, tag the row 'none'
explicitly rather than leaving null-and-ambiguous.

Only rows with price_taken IS NULL are touched; a row that already has a
price is never rewritten. Dry run by default.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2

from pf_backfill_results import norm_name

FILL_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (race_date, track, race_number, horse_name_norm)
           race_date, track, race_number, horse_name_norm,
           decimal_odds, captured_at
    FROM runner_odds_snapshots
    WHERE snapshot_kind = 'tip_time' AND (%(date)s::date IS NULL OR race_date = %(date)s)
    ORDER BY race_date, track, race_number, horse_name_norm, captured_at DESC
)
SELECT l.id, l.race_date, l.track, l.race_number, l.horse_name, s.decimal_odds
FROM selection_ledger l
LEFT JOIN latest s
  ON s.race_date = l.race_date
 AND s.race_number = l.race_number
 AND s.track = regexp_replace(lower(l.track), '[^a-z0-9]+', '', 'g')
 AND s.horse_name_norm = %(norm_fn)s
WHERE l.price_taken IS NULL
  AND (%(date)s::date IS NULL OR l.race_date = %(date)s)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="limit to one race day")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    # Pull candidates, match in python via norm_name so the horse-name
    # normalisation is byte-identical to the capture side (the LOWER()
    # bridge bug is not getting a third run).
    cur.execute(
        """
        SELECT id, race_date, track, race_number, horse_name
        FROM selection_ledger
        WHERE price_taken IS NULL
          AND (%(date)s::date IS NULL OR race_date = %(date)s)
        """, {"date": args.date})
    ledger_rows = cur.fetchall()
    cur.execute(
        """
        SELECT DISTINCT ON (race_date, track, race_number, horse_name_norm)
               race_date, track, race_number, horse_name_norm, decimal_odds
        FROM runner_odds_snapshots
        WHERE snapshot_kind = 'tip_time'
          AND (%(date)s::date IS NULL OR race_date = %(date)s)
        ORDER BY race_date, track, race_number, horse_name_norm,
                 captured_at DESC
        """, {"date": args.date})
    snap = {}
    import re
    for race_date, track, race_number, horse_norm, odds in cur.fetchall():
        snap[(str(race_date), track, int(race_number), horse_norm)] = float(odds)

    filled, marked_none = [], []
    for row_id, race_date, track, race_number, horse in ledger_rows:
        tkey = re.sub(r"[^a-z0-9]+", "", str(track or "").lower())
        price = snap.get((str(race_date), tkey, int(race_number),
                          norm_name(horse)))
        if price:
            filled.append((row_id, price))
        else:
            marked_none.append(row_id)

    print(f"candidates={len(ledger_rows)} fill={len(filled)} "
          f"no_snapshot={len(marked_none)}")
    if not args.commit:
        for row_id, price in filled[:20]:
            print(f"  WOULD FILL id={row_id} price_taken={price} "
                  "price_source=betfair_delayed_backfill")
        print("DRY RUN - re-run with --commit to write")
        return 0

    for row_id, price in filled:
        cur.execute(
            "UPDATE selection_ledger SET price_taken=%s, "
            "price_source='betfair_delayed_backfill', updated_at=NOW() "
            "WHERE id=%s AND price_taken IS NULL", (price, row_id))
    if marked_none:
        cur.execute(
            "UPDATE selection_ledger SET price_source='none', "
            "updated_at=NOW() WHERE id = ANY(%s) AND price_taken IS NULL "
            "AND price_source IS NULL", (marked_none,))
    conn.commit()
    print(f"committed: {len(filled)} filled, {len(marked_none)} marked none")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
