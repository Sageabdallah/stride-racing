#!/usr/bin/env python3
"""Task 10 step 2: de-vig method comparison harness (build now, run later).

Compares proportional, power and Shin de-vig on settled ledger books:
calibration error of the fair probability against realised outcomes, split
by price band so favourite-longshot bias is visible.

ORDERING GUARD: this comparison reads outcomes, so it must not run before
the retrain/validation windows are registered (docs/project_retrain_gate.md,
task 09 registry). The guard below enforces that: it refuses to run unless
STRIDE_PREREG_ACK=yes is set, which the operator sets only after
registration. Selecting a method and flipping STRIDE_DEVIG is a registered
experiment, never an ad hoc flip.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

import psycopg2

from market_prob import (devig_probabilities_pct,
                         devig_probabilities_pct_power,
                         devig_probabilities_pct_shin)

METHODS = {
    "proportional": devig_probabilities_pct,
    "power": devig_probabilities_pct_power,
    "shin": devig_probabilities_pct_shin,
}


def main() -> int:
    if os.environ.get("STRIDE_PREREG_ACK", "").strip().lower() != "yes":
        print("REFUSING TO RUN: outcome comparison before window registration.")
        print("Register the validation window (task 09 registry / WP-8), then")
        print("re-run with STRIDE_PREREG_ACK=yes.")
        return 2

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(
        """
        SELECT race_date, track, race_number, horse_name, price_taken, won
        FROM selection_ledger
        WHERE settled AND price_taken IS NOT NULL
        ORDER BY race_date, track, race_number
        """)
    books = defaultdict(list)
    for race_date, track, rn, horse, price, won in cur.fetchall():
        books[(race_date, track, rn)].append((float(price), bool(won)))
    conn.close()

    print(f"settled books: {len(books)}")
    for name, fn in METHODS.items():
        sq_err, n = 0.0, 0
        band_err = defaultdict(lambda: [0.0, 0])
        for runners in books.values():
            odds = [o for o, _ in runners]
            fair = fn(odds)
            for (o, won), p in zip(runners, fair):
                err = (p / 100.0 - (1.0 if won else 0.0)) ** 2
                sq_err += err
                n += 1
                band = "fav<4" if o < 4 else ("mid4-10" if o < 10 else "long>10")
                band_err[band][0] += err
                band_err[band][1] += 1
        brier = sq_err / n if n else float("nan")
        bands = {b: round(e / c, 4) for b, (e, c) in band_err.items() if c}
        print(f"{name:>13}: brier={brier:.5f} n={n} bands={bands}")
    print("Record the selected method and reason in docs/validation/registry.md "
          "before flipping STRIDE_DEVIG.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
