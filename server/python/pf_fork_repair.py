#!/usr/bin/env python3
"""Country-suffix fork repair for the known-bad 2026-07-31 prod writes.

The pre-fix horse-ID bridge projected LOWER(horse_name), so norm_name could
never strip an uppercase country suffix and every DB horse stored as e.g.
"Ocean Deep (NZ)" was unmatchable — the 2026-07-31 backfill (10,503 rows,
578 new pf% ids) and the 2026-07-31 evening proof run (48 races / 495
runners) minted fresh pf<runnerId> ids for horses that already existed.
Each fork splits one horse's history across two ids for every feature that
joins on horse_id. The corrected bridge (pf_results_mapper.BRIDGE_SQL /
load_bridge, original-case projection) is the reference for what SHOULD
have happened; this script finds the forks and, behind a separate write
gate, rewrites them.

Report mode (default, read-only):
  1. discovers the blast radius mechanically — information_schema.columns
     for every table with a horse_id column, cross-checked with
     `git grep -l "horse_id" -- server/`; no hardcoded table list;
  2. builds the corrected reference bridge over NON-pf rows only (the
     mapper's BRIDGE_SQL plus `WHERE race_id NOT LIKE 'pf%'`, so a forked
     id can never "match" itself);
  3. detects forks: a pf% horse id whose stored name (original case)
     normalises to a key that resolves in the reference bridge is a
     spurious fork. A pf id carrying >1 distinct stored name is a
     report-only anomaly (never remapped). Two pf ids remapping to one
     real id is valid — both were forks of the same horse;
  4. prints totals, a by-suffix breakdown ((NZ)/(GB)/(IRE)/other/none),
     per-table affected-row counts, a 20-fork eyeball sample, and writes
     the full remap list to pf_fork_remap.json.

Apply mode (--apply, SEPARATE operator write approval required; run it
only AFTER pf_track_dedup --apply — deleting alias-doubled meetings first
shrinks this remap to real cases):
  single transaction; every row to be touched is pre-imaged into
  pf_fork_repair_backup (table_name, row jsonb, backed_up_at) BEFORE any
  UPDATE; updates are restricted to rows whose race_id is pf% where the
  table has a race_id column (non-pf rows never carried forked ids);
  tables without race_id are listed and updated only when named via
  --include-table (i.e. named in the operator's approval); post-apply
  verification re-detects (zero forks) and prints the new bridged-vs-new
  percentage for the Phase B numbers. Idempotent: a second apply finds
  zero forks and writes nothing.

Usage:
    DATABASE_URL=... python3 server/python/pf_fork_repair.py
        [--remap-out pf_fork_remap.json]
    DATABASE_URL=... python3 server/python/pf_fork_repair.py --apply
        [--include-table some_table_without_race_id]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pf_results_mapper import BRIDGE_SQL, norm_name  # noqa: E402

BACKUP_TABLE = "pf_fork_repair_backup"
KNOWN_SUFFIXES = ("NZ", "GB", "IRE")
EYEBALL_SAMPLE = 20
TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Mechanical blast-radius discovery: every public table carrying a horse_id
# column, plus whether it also has race_id (the pf%-row restriction hinge).
TABLES_SQL = """
    SELECT table_name,
           BOOL_OR(column_name = 'race_id') AS has_race_id
    FROM information_schema.columns
    WHERE table_schema = 'public' AND column_name IN ('horse_id', 'race_id')
    GROUP BY table_name
    HAVING BOOL_OR(column_name = 'horse_id')
    ORDER BY table_name
"""

PF_ID_NAMES_SQL = """
    SELECT DISTINCT horse_id, horse_name
    FROM race_results_history
    WHERE horse_id LIKE 'pf%'
"""

RECENT_NONPF_NAME_SQL = """
    SELECT horse_name, race_date::text
    FROM race_results_history
    WHERE horse_id = %s AND race_id NOT LIKE 'pf%%'
    ORDER BY race_date DESC
    LIMIT 1
"""

BRIDGED_VS_NEW_SQL = """
    SELECT COUNT(*) FILTER (WHERE horse_id LIKE 'pf%'), COUNT(*)
    FROM race_results_history
    WHERE race_id LIKE 'pf%'
"""

BACKUP_DDL = f"""
CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
    id BIGSERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    row_data JSONB NOT NULL,
    backed_up_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def connect(db_url):
    """Lazy psycopg2 import (tests never touch a real driver)."""
    import psycopg2
    return psycopg2.connect(db_url)


def reference_bridge_sql():
    """The mapper's BRIDGE_SQL as the base (original-case projection — never
    re-derive it, never project LOWER(horse_name)) restricted to NON-pf rows
    so a forked pf id can never match its own PF-written name."""
    new_sql, n = re.subn(r"(?i)\border\s+by\b",
                         "WHERE race_id NOT LIKE 'pf%' ORDER BY",
                         BRIDGE_SQL, count=1)
    if n != 1:
        raise RuntimeError("BRIDGE_SQL shape changed — cannot derive the "
                           "reference bridge safely")
    return new_sql


def load_reference_bridge(cur):
    """normalised name -> existing NON-pf horse_id; most recent prior row
    wins (inherited from the mapper's DISTINCT ON / ORDER BY)."""
    cur.execute(reference_bridge_sql())
    return {norm_name(name): hid for name, hid in cur.fetchall()}


def discover_horse_id_tables(cur):
    """Every public table with a horse_id column: [{table, has_race_id}]."""
    cur.execute(TABLES_SQL)
    tables = [{"table": t, "has_race_id": bool(has)}
              for t, has in cur.fetchall()]
    for t in tables:
        if not TABLE_NAME_RE.match(t["table"]):
            raise RuntimeError(f"unexpected table name {t['table']!r} — refusing "
                               "to interpolate it into SQL")
    return tables


def git_grep_horse_id_files():
    """Cross-check: files under server/ mentioning horse_id. None if git is
    unavailable (the information_schema discovery is the source of truth)."""
    try:
        out = subprocess.run(["git", "grep", "-l", "horse_id", "--", "server/"],
                             capture_output=True, text=True, check=True)
        return sorted(line.strip() for line in out.stdout.splitlines()
                      if line.strip())
    except Exception:
        return None


def load_pf_id_names(cur):
    """pf% horse id -> set of stored original-case names."""
    cur.execute(PF_ID_NAMES_SQL)
    names = {}
    for hid, name in cur.fetchall():
        names.setdefault(hid, set()).add(name or "")
    return names


def suffix_of(name):
    """Report bucket for a stored name: (NZ)/(GB)/(IRE)/other/none."""
    m = re.search(r"\(([A-Z]{2,3})\)\s*$", str(name or "").strip())
    if not m:
        return "none"
    sfx = m.group(1)
    return f"({sfx})" if sfx in KNOWN_SUFFIXES else "other"


def compute_remap(pf_id_names, bridge):
    """Pure fork detection.

    pf_id_names: pf id -> set of stored names. bridge: norm key -> real id.
    Returns (remap, anomalies, clean):
      remap[pf_id] = {real_id, name, suffix} for spurious forks;
      anomalies  = [(pf_id, [names...])] for pf ids with >1 stored name
                   (reported, never remapped);
      clean      = [pf_id] genuinely new horses, untouched.
    """
    remap, anomalies, clean = {}, [], []
    for pf_id in sorted(pf_id_names):
        names = sorted(pf_id_names[pf_id])
        if len(names) > 1:
            anomalies.append((pf_id, names))
            continue
        name = names[0]
        real_id = bridge.get(norm_name(name))
        if real_id:
            remap[pf_id] = {"real_id": real_id, "name": name,
                            "suffix": suffix_of(name)}
        else:
            clean.append(pf_id)
    return remap, anomalies, clean


def suffix_breakdown(remap):
    buckets = {}
    for info in remap.values():
        buckets[info["suffix"]] = buckets.get(info["suffix"], 0) + 1
    return {k: buckets.get(k, 0) for k in ("(NZ)", "(GB)", "(IRE)",
                                           "other", "none")}


def per_table_counts(cur, tables, remap_ids):
    """Per table: rows holding any pf% horse id, and rows the remap would
    touch (restricted to pf% race_id rows where race_id exists)."""
    out = {}
    for t in tables:
        name = t["table"]
        cur.execute(f'SELECT COUNT(*) FROM "{name}" WHERE horse_id LIKE \'pf%\'')
        pf_rows = cur.fetchall()[0][0]
        fork_rows = 0
        if remap_ids and pf_rows:
            sql = f'SELECT COUNT(*) FROM "{name}" WHERE horse_id = ANY(%s)'
            if t["has_race_id"]:
                # parametrized below: the literal % must be escaped for psycopg2
                sql += " AND race_id LIKE 'pf%%'"
            cur.execute(sql, (list(remap_ids),))
            fork_rows = cur.fetchall()[0][0]
        out[name] = {"pf_rows": pf_rows, "fork_rows": fork_rows,
                     "has_race_id": t["has_race_id"]}
    return out


def most_recent_nonpf_name(cur, real_id):
    cur.execute(RECENT_NONPF_NAME_SQL, (real_id,))
    rows = cur.fetchall()
    return (rows[0][0], rows[0][1]) if rows else (None, None)


def bridged_vs_new(cur):
    cur.execute(BRIDGED_VS_NEW_SQL)
    new, total = cur.fetchall()[0]
    bridged = total - new
    pct = (100.0 * bridged / total) if total else 0.0
    return bridged, new, total, pct


def detect(cur):
    """Full read-only detection pass; returns the report dict."""
    tables = discover_horse_id_tables(cur)
    bridge = load_reference_bridge(cur)
    pf_id_names = load_pf_id_names(cur)
    remap, anomalies, clean = compute_remap(pf_id_names, bridge)
    counts = per_table_counts(cur, tables, sorted(remap))
    bridged, new, total, pct = bridged_vs_new(cur)
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds"),
        "tables": tables,
        "bridge_size": len(bridge),
        "pf_ids_scanned": len(pf_id_names),
        "forks": len(remap),
        "clean": len(clean),
        "anomalies": [{"pf_id": p, "names": n} for p, n in anomalies],
        "suffix_breakdown": suffix_breakdown(remap),
        "per_table": counts,
        "bridged_vs_new": {"bridged_rows": bridged, "new_id_rows": new,
                           "pf_race_rows": total, "bridged_pct": round(pct, 2)},
        "remap": remap,
    }


def eyeball_sample(cur, remap, limit=EYEBALL_SAMPLE):
    """pf name -> real id -> that horse's most recent non-pf row name/date."""
    sample = []
    for pf_id in sorted(remap)[:limit]:
        info = remap[pf_id]
        recent_name, recent_date = most_recent_nonpf_name(cur, info["real_id"])
        sample.append({"pf_id": pf_id, "pf_name": info["name"],
                       "real_id": info["real_id"],
                       "recent_nonpf_name": recent_name,
                       "recent_nonpf_date": recent_date})
    return sample


def print_report(report, sample, grep_files, out=print):
    bvn = report["bridged_vs_new"]
    out("=" * 64)
    out("PF FORK REPAIR — REPORT (read-only)")
    out(f"generated: {report['generated_at']}")
    out("=" * 64)
    out(f"reference bridge (non-pf rows only): {report['bridge_size']} horses")
    out(f"pf% horse ids scanned:  {report['pf_ids_scanned']}")
    out(f"spurious forks:         {report['forks']}")
    out(f"genuinely new (clean):  {report['clean']}")
    out(f"anomalies (>1 name):    {len(report['anomalies'])}")
    out(f"current PF rows bridged vs new-id: {bvn['bridged_rows']} vs "
        f"{bvn['new_id_rows']} of {bvn['pf_race_rows']} "
        f"({bvn['bridged_pct']}% bridged)")
    out("")
    out("forks by name suffix:")
    for bucket in ("(NZ)", "(GB)", "(IRE)", "other", "none"):
        out(f"  {bucket:>6}: {report['suffix_breakdown'][bucket]}")
    if report["anomalies"]:
        out("")
        out("ANOMALIES — pf ids with >1 stored name (reported, NOT remapped):")
        for a in report["anomalies"]:
            out(f"  {a['pf_id']}: {a['names']}")
    out("")
    out("per-table blast radius (tables with a horse_id column):")
    no_race_id_with_rows = []
    for name, c in sorted(report["per_table"].items()):
        flag = "" if c["has_race_id"] else "  [NO race_id COLUMN]"
        out(f"  {name}: {c['pf_rows']} pf% rows, "
            f"{c['fork_rows']} rows the remap would touch{flag}")
        if not c["has_race_id"] and (c["pf_rows"] or c["fork_rows"]):
            no_race_id_with_rows.append(name)
    if no_race_id_with_rows:
        out("")
        out("tables WITHOUT race_id holding pf ids — excluded from --apply "
            "unless the operator's write approval names them "
            f"(--include-table): {', '.join(no_race_id_with_rows)}")
    out("")
    out(f"eyeball sample (first {len(sample)} forks): "
        "pf name -> real id -> most recent non-pf row name (date)")
    for s in sample:
        out(f"  {s['pf_name']}  ->  {s['real_id']}  ->  "
            f"{s['recent_nonpf_name']} ({s['recent_nonpf_date']})")
    out("")
    if grep_files is None:
        out("git grep cross-check: SKIPPED (git unavailable)")
    else:
        out(f"git grep cross-check: {len(grep_files)} files under server/ "
            "mention horse_id (information_schema discovery above is the "
            "authoritative table list)")


def apply_remap(cur, conn, tables, remap, include_tables=(), out=print):
    """Single-transaction repair. Backup BEFORE any UPDATE; updates
    restricted to pf% race_id rows where the table has race_id; tables
    without race_id only when explicitly named. Caller commits via conn."""
    if not remap:
        out("nothing to apply — zero forks")
        return {}
    include_tables = set(include_tables)
    pf_ids = sorted(remap)
    real_ids = [remap[p]["real_id"] for p in pf_ids]
    cur.execute(BACKUP_DDL)
    updated = {}
    for t in tables:
        name = t["table"]
        if not t["has_race_id"] and name not in include_tables:
            continue
        # parametrized below: the literal % must be escaped for psycopg2
        race_filter = " AND race_id LIKE 'pf%%'" if t["has_race_id"] else ""
        # pre-image every row to be touched BEFORE the UPDATE
        cur.execute(
            f'INSERT INTO {BACKUP_TABLE} (table_name, row_data) '
            f'SELECT %s, row_to_json(src.*) FROM "{name}" AS src '
            f'WHERE src.horse_id = ANY(%s){race_filter}',
            (name, pf_ids))
        cur.execute(
            f'UPDATE "{name}" AS t SET horse_id = v.real_id '
            f'FROM (SELECT unnest(%s::text[]) AS pf_id, '
            f'      unnest(%s::text[]) AS real_id) AS v '
            f'WHERE t.horse_id = v.pf_id{race_filter}',
            (pf_ids, real_ids))
        updated[name] = cur.rowcount
        out(f"  {name}: {cur.rowcount} rows remapped "
            f"(pre-imaged into {BACKUP_TABLE})")
    return updated


def run(cur, conn, apply=False, include_tables=(), out=print):
    """Detection + report (+ apply when gated). Returns the report dict."""
    report = detect(cur)
    sample = eyeball_sample(cur, report["remap"])
    grep_files = git_grep_horse_id_files()
    print_report(report, sample, grep_files, out=out)
    report["eyeball_sample"] = sample
    if not apply:
        out("")
        out("REPORT ONLY — no writes. --apply requires the operator's "
            "separate prod WRITE approval and must run AFTER "
            "pf_track_dedup --apply (DM-G2 before DM-G1).")
        return report
    out("")
    out("APPLYING REPAIR (single transaction) ...")
    apply_remap(cur, conn, report["tables"], report["remap"],
                include_tables=include_tables, out=out)
    # post-apply verification inside the same run: re-detect -> zero forks
    post = detect(cur)
    out("")
    out("post-apply verification:")
    out(f"  forks remaining: {post['forks']} "
        f"(anomalies: {len(post['anomalies'])})")
    bvn = post["bridged_vs_new"]
    out(f"  bridged vs new-id rows now: {bvn['bridged_rows']} vs "
        f"{bvn['new_id_rows']} of {bvn['pf_race_rows']} "
        f"({bvn['bridged_pct']}% bridged)")
    if post["forks"]:
        raise RuntimeError(f"{post['forks']} forks still detected after "
                           "apply — rolling back")
    conn.commit()
    out("committed.")
    report["post_apply"] = {"forks_remaining": post["forks"],
                            "bridged_vs_new": post["bridged_vs_new"]}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="perform the repair (SEPARATE operator prod WRITE "
                         "approval required; run only AFTER pf_track_dedup "
                         "--apply). Default is a read-only report.")
    ap.add_argument("--include-table", action="append", default=[],
                    metavar="TABLE",
                    help="a no-race_id table the operator's write approval "
                         "named for repair (repeatable)")
    ap.add_argument("--remap-out", default="pf_fork_remap.json",
                    help="where to write the full remap list "
                         "(default: pf_fork_remap.json)")
    args = ap.parse_args(argv)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is required (prod READ approval for the "
              "report; separate WRITE approval for --apply)")
        return 1

    conn = None
    try:
        conn = connect(db_url)
        cur = conn.cursor()
        report = run(cur, conn, apply=args.apply,
                     include_tables=args.include_table)
        with open(args.remap_out, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nfull remap list written to {args.remap_out} "
              f"({report['forks']} forks)")
        return 0
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"ERROR: {e} — nothing committed", file=sys.stderr)
        return 1
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
