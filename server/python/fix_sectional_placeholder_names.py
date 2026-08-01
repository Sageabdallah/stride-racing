#!/usr/bin/env python3
"""F-SEC-PLACEHOLDER — sectional_times placeholder-name report + gated fix.

K5's sectionals-alignment check found sectional_times rows whose horse_name
is a literal 'Unknown_<pidata horse id>' placeholder, written by
nsw_sectional_collector.py when a .tol file carried intermediate timing
records but no horse-metadata (H) record. Those names can never join to a
horse, so the sectionals are invisible to every name-bridged consumer.

Two modes, same two-gate rule as the other repair tasks:

  * Report mode (DEFAULT, read-only): count placeholder rows by month and
    how many are join-resolvable against race_results_history. Prod reads
    require the operator's explicit approval.

      DATABASE_URL=... python fix_sectional_placeholder_names.py

  * Apply mode (--apply): fix ONLY the join-resolvable rows. Backs up every
    placeholder row into sectional_times_placeholder_backup_20260801 first
    (idempotent), then UPDATEs horse_name (+ race_results_history_id) on the
    resolvable rows. The UPDATE's WHERE still requires the placeholder
    pattern, so re-runs are no-ops. --apply requires a SEPARATE explicit
    operator write approval and never runs in CI.

      DATABASE_URL=... python fix_sectional_placeholder_names.py --apply

Resolution rule (shared shape with the collector's write-path resolver):
same race (race_date + race_number, best track-name match) + finishing
position taken from the stored splits_json '0m' split; names already
claimed by a named sectional row in the same race are excluded via
pf_results_mapper.norm_name. Exactly one candidate required, else the row
is left untouched.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nsw_sectional_collector import (
    fetch_race_candidates,
    is_placeholder_name,
    track_match_score,
)
from pf_results_mapper import norm_name

BACKUP_TABLE = "sectional_times_placeholder_backup_20260801"
PLACEHOLDER_WHERE = "horse_name ~* '^unknown[ _]'"


def finish_position_from_splits(splits_json):
    """Finishing position from a stored splits_json blob ('0m' = the finish
    line), or None."""
    if not isinstance(splits_json, dict):
        return None
    return (splits_json.get("0m") or {}).get("position")


def fetch_placeholder_rows(cur):
    cur.execute(f"""
        SELECT id, track, race_date, race_number, horse_name, saddle_number,
               splits_json
        FROM sectional_times
        WHERE {PLACEHOLDER_WHERE}
        ORDER BY race_date, track, race_number
    """)
    rows = []
    for rid, track, race_date, race_number, horse_name, saddle, splits in cur.fetchall():
        rows.append({
            "id": rid,
            "track": track,
            "race_date": race_date,
            "race_number": race_number,
            "horse_name": horse_name,
            "saddle_number": saddle,
            "splits_json": splits,
        })
    return rows


def fetch_named_race_names(cur, track, race_date, race_number):
    """Normalised names already stored (non-placeholder) for this race, so a
    fix never assigns a horse that already has a sectional row."""
    cur.execute("""
        SELECT horse_name FROM sectional_times
        WHERE track = %s AND race_date = %s AND race_number = %s
    """, (track, race_date, race_number))
    return {norm_name(r[0]) for r in cur.fetchall()
            if not is_placeholder_name(r[0])}


def resolve_row(row, candidates, claimed_keys):
    """Pure resolution: (rrh_id, horse_name) for one placeholder row, or
    None. candidates are fetch_race_candidates rows (id, horse_name, track,
    position, barrier)."""
    if not candidates:
        return None
    scored = [(track_match_score(row["track"], r[2]), r) for r in candidates]
    best = max(s for s, _ in scored)
    if best <= 0:
        return None
    cands = [r for s, r in scored
             if s == best and norm_name(r[1]) not in claimed_keys]
    finish_pos = finish_position_from_splits(row.get("splits_json"))
    if finish_pos is None:
        return None
    matches = [r for r in cands if r[3] == finish_pos]
    if len(matches) == 1:
        return matches[0][0], matches[0][1]
    return None


def plan_fixes(cur, rows):
    """Resolve every placeholder row. Returns (fixes, unresolved) where
    fixes is a list of dicts: the row plus rrh_id and resolved_name."""
    cand_cache = {}
    claimed_cache = {}
    fixes = []
    unresolved = []
    for row in rows:
        key = (row["track"], row["race_date"], row["race_number"])
        if key not in cand_cache:
            cand_cache[key] = fetch_race_candidates(
                cur, row["race_date"], row["race_number"])
            claimed_cache[key] = fetch_named_race_names(cur, *key)
        resolved = resolve_row(row, cand_cache[key], claimed_cache[key])
        if resolved is None:
            unresolved.append(row)
            continue
        rrh_id, name = resolved
        claimed_cache[key].add(norm_name(name))
        fixes.append({**row, "rrh_id": rrh_id, "resolved_name": name})
    return fixes, unresolved


def build_report(cur):
    rows = fetch_placeholder_rows(cur)
    by_month = {}
    for r in rows:
        month = str(r["race_date"])[:7]
        by_month[month] = by_month.get(month, 0) + 1
    fixes, unresolved = plan_fixes(cur, rows)
    return {
        "total": len(rows),
        "by_month": sorted(by_month.items()),
        "resolvable": len(fixes),
        "unresolvable": len(unresolved),
        "fixes": fixes,
        "unresolved_rows": unresolved,
    }


def print_report(rep):
    print("F-SEC-PLACEHOLDER report — sectional_times placeholder names")
    print(f"  placeholder rows total : {rep['total']}")
    print("  by month:")
    for month, count in rep["by_month"]:
        print(f"    {month}: {count}")
    print(f"  join-resolvable        : {rep['resolvable']}")
    print(f"  unresolvable (skipped) : {rep['unresolvable']}")
    for f in rep["fixes"]:
        print(f"    FIX  {f['race_date']} {f['track']} R{f['race_number']} "
              f"{f['horse_name']} -> {f['resolved_name']} (rrh id {f['rrh_id']})")
    for u in rep["unresolved_rows"]:
        print(f"    SKIP {u['race_date']} {u['track']} R{u['race_number']} "
              f"{u['horse_name']} — no unique race_results_history match")


def apply_fixes(cur, fixes):
    """Backup then update. Idempotent: the backup insert skips ids already
    backed up, and the UPDATE's WHERE still requires the placeholder
    pattern, so a re-run matches nothing."""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {BACKUP_TABLE}
        (LIKE sectional_times INCLUDING ALL)
    """)
    cur.execute(f"""
        INSERT INTO {BACKUP_TABLE}
        SELECT * FROM sectional_times
        WHERE {PLACEHOLDER_WHERE}
          AND id NOT IN (SELECT id FROM {BACKUP_TABLE})
    """)
    backed_up = cur.rowcount
    updated = 0
    for f in fixes:
        cur.execute(f"""
            UPDATE sectional_times
            SET horse_name = %s, race_results_history_id = %s
            WHERE id = %s AND {PLACEHOLDER_WHERE}
        """, (f["resolved_name"], f["rrh_id"], f["id"]))
        updated += cur.rowcount
    return backed_up, updated


def main(argv=None, connect=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="write the fixes (operator write-approval gated; "
                             "default is a read-only report)")
    args = parser.parse_args(argv)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 1
    if connect is None:
        import psycopg2
        connect = psycopg2.connect

    conn = connect(db_url)
    try:
        cur = conn.cursor()
        rep = build_report(cur)
        print_report(rep)
        if not args.apply:
            conn.rollback()
            print("\nREAD-ONLY report — nothing written. With a separate "
                  "operator WRITE approval, run:")
            print("  DATABASE_URL=... python fix_sectional_placeholder_names.py --apply")
            return 0
        if not rep["fixes"]:
            conn.rollback()
            print("\n--apply: no resolvable rows; nothing written.")
            return 0
        backed_up, updated = apply_fixes(cur, rep["fixes"])
        conn.commit()
        print(f"\n--apply: backed up {backed_up} row(s) into {BACKUP_TABLE}; "
              f"updated {updated} row(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
