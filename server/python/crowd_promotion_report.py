#!/usr/bin/env python3
"""Task 07 step 3: weekly report on blocked crowd promotions.

Counts the promotions the gate-only flag blocked, and, for settled rows,
their shadow P&L net of commission and CLV. Expect roughly zero to negative
value; if this report ever shows genuine promotion value, re-opening the
promotion path is a pre-registered experiment under task 09, never a code
flip.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2

SQL = """
SELECT COUNT(*)                                   AS blocked,
       COUNT(*) FILTER (WHERE settled)            AS settled,
       ROUND(COALESCE(SUM(settled_pnl) FILTER (WHERE settled), 0)::numeric, 2) AS shadow_pnl_net,
       ROUND(COALESCE(AVG(clv_pct)   FILTER (WHERE clv_pct IS NOT NULL), 0)::numeric, 4) AS avg_clv_pct,
       COUNT(*) FILTER (WHERE price_taken IS NOT NULL) AS with_price
FROM selection_ledger
WHERE refusal_reason = 'crowd_promotion_blocked'
  AND race_date >= CURRENT_DATE - %s
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(SQL, (args.days,))
    blocked, settled, pnl, clv, with_price = cur.fetchone()
    conn.close()
    print(f"blocked promotions (last {args.days}d): {blocked} "
          f"({with_price} with a would-be price)")
    print(f"settled: {settled} | shadow P&L net of commission: {pnl}u | "
          f"avg CLV: {clv}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
