#!/usr/bin/env python3
"""pf_track_dedup — find and (only with explicit operator write approval)
repair F-TRACK-ALIAS doubled race-days in race_results_history.

THE DEFECT (K5, 2026-08-01): the same physical meeting was stored twice
under alias track spellings (PF 'Belmont Park' vs old 'Belmont',
'Fannie Bay' vs 'Darwin', sponsor-prefixed names, ...). The dedup key
keyed on lower(track), so both spellings inserted. The key now
canonicalises (pf_results_mapper.canonical_track_key) so NO NEW doubles
can be written; this script finds the EXISTING ones.

REPORT MODE (default — prod READ approval required):
  every (race_date, canonical_track_key(track), race_number) group stored
  under >=2 track spellings is a doubled race-day. Per group, row-sets are
  the distinct (track, race_id) populations; the survivor is:
    1. the non-'pf%' race_id set (old-pipeline rows) if present, else
    2. the earliest-inserted set (MIN(created_at)).
  Prints per-pair rows-to-delete counts and a 10-double eyeball sample
  (with horse names from each side so a human can confirm same-race).

APPLY MODE (--apply — SEPARATE prod WRITE approval required; never run
in CI; the operator runs this by hand):
  apply implies report-first: the report is computed in the SAME
  invocation and apply acts on that fresh state (there is no code path
  that deletes without reporting first). Single transaction:
    1. CREATE TABLE IF NOT EXISTS pf_track_dedup_backup
    2. pre-image every to-be-deleted row into pf_track_dedup_backup
       (row as jsonb, deleted_at) — ALL backups before ANY delete
    3. DELETE the duplicate row-sets (precise
       race_date+track+race_number+race_id predicates)
    4. commit
  Idempotent: a second --apply finds zero doubles and deletes zero rows.

Exit codes: 0 = report produced (and apply committed if requested);
1 = no DATABASE_URL / DB error / apply requested but nothing to do is
NOT an error (idempotent re-apply prints so and exits 0).
"""
import argparse
import os
import sys
from collections import defaultdict

from pf_results_mapper import canonical_track_key

BACKUP_TABLE = "pf_track_dedup_backup"

BACKUP_DDL = f"""
CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
    id BIGSERIAL PRIMARY KEY,
    race_date TEXT,
    track TEXT,
    race_number INTEGER,
    race_id TEXT,
    row_json JSONB NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

GROUP_SQL = """
    SELECT race_date, track, race_number, race_id, COUNT(*), MIN(created_at)
    FROM race_results_history
    GROUP BY 1, 2, 3, 4
"""

NAMES_SQL = """
    SELECT DISTINCT horse_name FROM race_results_history
    WHERE race_date = %s AND track = %s AND race_number = %s AND race_id = %s
    LIMIT 3
"""

BACKUP_INSERT_SQL = f"""
    INSERT INTO {BACKUP_TABLE} (race_date, track, race_number, race_id, row_json)
    SELECT race_date, track, race_number, race_id, row_to_json(t)
    FROM race_results_history t
    WHERE race_date = %s AND track = %s AND race_number = %s AND race_id = %s
"""

DELETE_SQL = """
    DELETE FROM race_results_history
    WHERE race_date = %s AND track = %s AND race_number = %s AND race_id = %s
"""


# --------------------------------------------------------------------------
# Pure logic (unit-tested with fakes; no DB)
# --------------------------------------------------------------------------

def find_doubles(group_rows):
    """group_rows: iterable of (race_date, track, race_number, race_id,
    n_rows, first_created). Returns a list of doubles, each:
      {"key": (race_date, canonical_key, race_number),
       "sets": [(track, race_id, n_rows, first_created), ...]}
    A double = one canonical race stored under >=2 track spellings."""
    groups = defaultdict(list)
    for race_date, track, rnum, race_id, n, first_created in group_rows:
        groups[(str(race_date), canonical_track_key(track), int(rnum))].append(
            (track, race_id, int(n), first_created))
    doubles = []
    for key, sets in sorted(groups.items()):
        if len({s[0] for s in sets}) >= 2:
            doubles.append({"key": key, "sets": sorted(sets)})
    return doubles


def choose_survivor(sets):
    """Survivor rule: prefer the non-'pf%' race_id set (old-pipeline rows);
    else the earliest-inserted set (MIN(created_at)); race_id as final
    tie-break for determinism. Returns (survivor_set, [delete_sets])."""
    def rank(s):
        track, race_id, _n, first_created = s
        return (race_id.startswith("pf"),  # False (old) sorts first
                first_created is None,     # known time beats unknown
                str(first_created),
                race_id)
    ordered = sorted(sets, key=rank)
    return ordered[0], ordered[1:]


def plan_deletes(doubles):
    """[(double, survivor, delete_sets)] for every double."""
    plan = []
    for d in doubles:
        survivor, deletes = choose_survivor(d["sets"])
        plan.append((d, survivor, deletes))
    return plan


# --------------------------------------------------------------------------
# Report + apply
# --------------------------------------------------------------------------

def run_report(cur, plan):
    """Print the report; returns total rows-to-delete."""
    total_sets = sum(len(deletes) for _d, _s, deletes in plan)
    total_rows = sum(s[2] for _d, _s, deletes in plan for s in deletes)
    print("=" * 72)
    print("PF TRACK DEDUP — REPORT (read-only unless --apply)")
    print("=" * 72)
    print(f"doubled race-days (same date + canonical key + race number under "
          f">=2 spellings): {len(plan)}")
    print(f"duplicate row-sets to delete: {total_sets}")
    print(f"rows to delete: {total_rows}")
    by_pair = defaultdict(int)
    for _d, _s, deletes in plan:
        for track, _rid, n, _c in deletes:
            by_pair[track] += n
    print("\nrows-to-delete by doomed spelling:")
    for track, n in sorted(by_pair.items(), key=lambda kv: -kv[1]):
        print(f"    {n:5d}  {track}")
    print("\neyeball sample (up to 10 doubles; KEEP vs DELETE):")
    for d, survivor, deletes in plan[:10]:
        race_date, canon, rnum = d["key"]
        print(f"  {race_date} [{canon}] R{rnum}")
        for label, s in (("KEEP  ", survivor),
                         *[("DELETE", x) for x in deletes]):
            track, race_id, n, created = s
            cur.execute(NAMES_SQL, (race_date, track, rnum, race_id))
            names = ", ".join(r[0] for r in cur.fetchall())
            print(f"    {label} {track} / {race_id} — {n} rows, "
                  f"first inserted {created} — e.g. {names}")
    if len(plan) > 10:
        print(f"    ... and {len(plan) - 10} more doubles")
    return total_rows


def apply_plan(cur, plan):
    """Single-transaction repair. Caller commits. ALL backup pre-images are
    written before ANY delete. Returns rows deleted."""
    cur.execute(BACKUP_DDL)
    for d, _survivor, deletes in plan:
        race_date, _canon, rnum = d["key"]
        for track, race_id, _n, _c in deletes:
            cur.execute(BACKUP_INSERT_SQL, (race_date, track, rnum, race_id))
    deleted = 0
    for d, _survivor, deletes in plan:
        race_date, _canon, rnum = d["key"]
        for track, race_id, n, _c in deletes:
            cur.execute(DELETE_SQL, (race_date, track, rnum, race_id))
            deleted += n
    return deleted


def main():
    ap = argparse.ArgumentParser(
        description="F-TRACK-ALIAS doubled race-day report / repair")
    ap.add_argument("--apply", action="store_true",
                    help="WRITE MODE — requires the operator's separate "
                         "explicit prod write approval. Never run in CI.")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        return 1
    import psycopg2
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        if not args.apply:
            cur.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        # Report ALWAYS runs first, in the same invocation; apply acts on
        # this fresh state (no stale plan can be deleted by accident).
        cur.execute(GROUP_SQL)
        plan = plan_deletes(find_doubles(cur.fetchall()))
        run_report(cur, plan)
        if not args.apply:
            print("\nReport only. Re-run with --apply (after the "
                  "operator's explicit prod WRITE approval) to repair.")
            return 0
        if not plan:
            print("\n--apply: zero doubles — nothing to do (idempotent).")
            return 0
        print(f"\n--apply: backing up pre-images to {BACKUP_TABLE}, then "
              f"deleting duplicate row-sets, ONE transaction...")
        deleted = apply_plan(cur, plan)
        conn.commit()
        print(f"--apply committed: {deleted} rows deleted, pre-images in "
              f"{BACKUP_TABLE}. Re-run to confirm zero doubles.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
