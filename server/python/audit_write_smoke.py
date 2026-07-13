"""
Audit-write smoke test: prove mc_api.log_prediction_audit can write to the
database behind DATABASE_URL, end to end, without running a full card.

Why: the coverage report (docs/12 §4c) showed prediction_audit held one
week of rows after 15 months of daily runs — failures were silent. This
script exercises the EXACT production write path (mc_api's own function,
connection handling and upsert SQL) with an unmistakable sentinel row,
verifies it landed, then deletes it.

The sentinel (track 'AUDIT SMOKE TEST', race_date '1970-01-01') can never
join race_results_history, so even if cleanup were interrupted it could
never enter training_view_v2 or any training path — zero contamination.

What a pass proves: code + DATABASE_URL + table accept audit writes from
this environment. What it cannot prove: that the PRODUCTION BOX's own env
is correct — run one real card there and watch stderr for the
'[WARN] prediction_audit logging FAILED' line mc_api now emits.
"""

import os
import sys

SENTINEL_TRACK = "AUDIT SMOKE TEST"
SENTINEL_DATE = "1970-01-01"


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("FAIL: DATABASE_URL not set", file=sys.stderr)
        return 1

    import psycopg2
    import mc_api

    rows = [
        (SENTINEL_TRACK, 99, SENTINEL_DATE, None, "SMOKE ONE",
         0.4100, 0.7100, 3.5, 0.62, 11.4, "pending"),
        (SENTINEL_TRACK, 99, SENTINEL_DATE, None, "SMOKE TWO",
         0.1200, 0.3900, 9.0, 0.41, 0.9, "pending"),
    ]

    print(f"[1] Writing {len(rows)} sentinel rows via mc_api.log_prediction_audit ...")
    mc_api.log_prediction_audit(rows)
    print("    (any '[WARN] prediction_audit logging FAILED' line above is the diagnosis)")

    print("[2] Verifying the rows landed ...")
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT horse_name, predicted_win_prob FROM prediction_audit "
            "WHERE track = %s AND race_date = %s ORDER BY horse_name",
            (SENTINEL_TRACK, SENTINEL_DATE),
        )
        found = cur.fetchall()
        ok = len(found) == len(rows)
        for name, prob in found:
            print(f"    found: {name}  win_prob={prob}")

        print("[3] Cleaning up sentinel rows ...")
        cur.execute(
            "DELETE FROM prediction_audit WHERE track = %s AND race_date = %s",
            (SENTINEL_TRACK, SENTINEL_DATE),
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        print(f"    deleted {deleted} sentinel rows")
    finally:
        conn.close()

    if ok:
        print("\nPASS: the production audit-write path works against this "
              "DATABASE_URL. If the live box still writes nothing, its own "
              "env/connection is the problem — the new mc_api stderr warning "
              "will name the reason on the first real run.")
        return 0
    print(f"\nFAIL: expected {len(rows)} sentinel rows, found {len(found)} — "
          f"the write path itself is broken against this database; see any "
          f"warning printed at step [1].", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
