#!/usr/bin/env python3
"""Ingest Betfair's free daily BSP files into a full-field historical corpus.

bsp_settlement.py already reads these files, but it only fills sp on rows we
took a position on. That answers "what did my bet close at". It cannot answer
"is a stated 19.5% win probability actually a 19.5% chance", because that
question needs the horses we passed over as well as the ones we backed —
the denominator, not just the numerator.

So this module stores every runner of every race, keyed by date, track, race
number and normalised horse name, and records per file what the file claimed
versus what reached the table.

Deliberately parasitic on bsp_settlement: the URL, the R+1 publication lag,
the menu_hint belt-filter, the harness exclusion, the cloth-number strip and
the name normaliser are all imported, never re-implemented. Two ingest paths
that disagree about which day a file belongs to would be worse than none, and
that lag is the non-obvious part — the file stamped D holds the races of D-1.

Modes:
    --sample DATE        fetch one file, print the header, the first rows and
                         the counts, write nothing. Proves the format in the
                         runtime that can actually reach the host.
    --since A --until B  ingest the range (inclusive), skipping days whose
                         file is not published.

Exit codes: 0 ok; 3 nothing ingested across the whole range; 4 at least one
date inside the range failed hard (network/parse), which is a finding rather
than a silent gap.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bsp_settlement import (  # noqa: E402  - the single source of these rules
    BSP_URL,
    _file_stamp,
    _hint_matches,
    _norm,
    fetch_csv,
    parse_bsp_csv,
)

# The columns the parser actually reads. Asserted on every file rather than
# assumed: the parser's contract was verified against files published in
# 2026 and a corpus backfill walks years of them. A silently renamed column
# would otherwise land as a table full of NULL bsp that still looks ingested.
REQUIRED_COLUMNS = ("event_name", "menu_hint", "selection_name", "bsp", "win_lose")


def _norm_track(name: str) -> str:
    """The database's own track normaliser, so this table joins to the rest."""
    from identity_normalization import stride_norm_track_py as _t  # type: ignore
    return _t(name or "")


def _fallback_norm_track(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def norm_track(name: str) -> str:
    try:
        return _norm_track(name)
    except Exception:
        # identity_normalization exposes the canonical rule under a few names
        # across versions; a corpus must not fail to ingest over a helper name.
        return _fallback_norm_track(name)


def verify_columns(text: str) -> List[str]:
    """Missing required columns, empty when the file matches expectations."""
    header = text.splitlines()[0] if text else ""
    cols = [c.strip() for c in header.split(",")]
    return [c for c in REQUIRED_COLUMNS if c not in cols]


def daterange(since: str, until: str) -> List[str]:
    a = datetime.strptime(since, "%Y-%m-%d").date()
    b = datetime.strptime(until, "%Y-%m-%d").date()
    if b < a:
        raise ValueError(f"--until {until} precedes --since {since}")
    return [(a + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range((b - a).days + 1)]


def sample(race_date: str) -> int:
    """Print the real format. Writes nothing, touches no database."""
    status, text = fetch_csv(race_date)
    print(f"BSP_SAMPLE date={race_date} stamp={_file_stamp(race_date)} status={status}")
    print(f"BSP_SAMPLE url={BSP_URL.format(stamp=_file_stamp(race_date))}")
    if status != "OK" or not text:
        print("BSP_SAMPLE result=FAIL file not published for this date")
        return 4

    lines = text.splitlines()
    missing = verify_columns(text)
    print(f"BSP_SAMPLE bytes={len(text)} lines={len(lines)}")
    print(f"BSP_SAMPLE missing_columns={','.join(missing) if missing else 'none'}")
    print("----- header -----")
    print(lines[0])
    print("----- first 5 raw rows -----")
    for line in lines[1:6]:
        print(line)
    print("-----")

    all_rows = list(csv.DictReader(io.StringIO(text)))
    hinted = [r for r in all_rows if _hint_matches(r.get("menu_hint"), race_date)]
    parsed = parse_bsp_csv(text, race_date=race_date)
    print(f"BSP_SAMPLE rows_in_file={len(all_rows)} "
          f"hinted_for_{race_date}={len(hinted)} parsed_thoroughbred={len(parsed)}")
    tracks = sorted({p["track_hint"] for p in parsed})
    print(f"BSP_SAMPLE tracks={len(tracks)}: {', '.join(tracks[:12])}")
    print("----- first 3 parsed entries -----")
    for p in parsed[:3]:
        print(f"  track={p['track_hint']!r} race={p['race_number']} "
              f"horse={p['horse']!r} bsp={p['bsp']} win_lose={p['win_lose']}")
    print("-----")
    if missing:
        print("BSP_SAMPLE result=FAIL the file no longer carries the columns "
              "the parser reads; the corpus would ingest nulls that look like data")
        return 4
    print("BSP_SAMPLE result=PASS")
    return 0


UPSERT = """
INSERT INTO betfair_bsp_history
    (race_date, track, track_norm, race_number, horse_name, horse_name_norm,
     bsp, win_lose, source_file)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (race_date, track_norm, race_number, horse_name_norm)
DO UPDATE SET bsp = EXCLUDED.bsp,
              win_lose = EXCLUDED.win_lose,
              source_file = EXCLUDED.source_file,
              ingested_at = NOW()
"""

LOG_UPSERT = """
INSERT INTO betfair_bsp_ingest_log
    (race_date, source_file, rows_in_file, rows_written, harness_skipped, status)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (race_date) DO UPDATE SET
    source_file = EXCLUDED.source_file,
    rows_in_file = EXCLUDED.rows_in_file,
    rows_written = EXCLUDED.rows_written,
    harness_skipped = EXCLUDED.harness_skipped,
    status = EXCLUDED.status,
    ingested_at = NOW()
"""


def ingest_date(conn, race_date: str, commit: bool = False) -> Dict[str, Any]:
    """One day. Returns a record of what the file held and what was stored."""
    stamp = _file_stamp(race_date)
    source_file = f"dwbfpricesauswin{stamp}.csv"
    status, text = fetch_csv(race_date)
    if status != "OK" or not text:
        rec = {"race_date": race_date, "source_file": source_file,
               "rows_in_file": 0, "rows_written": 0, "harness_skipped": 0,
               "status": "FILE_NOT_PUBLISHED"}
        if commit:
            _log(conn, rec)
        return rec

    missing = verify_columns(text)
    if missing:
        # Loudly, not as a zero-row day: a renamed column is a schema change
        # and must not be recorded as "that day had no racing".
        raise RuntimeError(
            f"{source_file} is missing required column(s) {','.join(missing)} — "
            f"the corpus would store nulls indistinguishable from real data")

    hinted = [r for r in csv.DictReader(io.StringIO(text))
              if _hint_matches(r.get("menu_hint"), race_date)]
    entries = parse_bsp_csv(text, race_date=race_date)
    harness_skipped = len(hinted) - len(entries)

    rows = []
    for e in entries:
        track = e["track_hint"] or ""
        horse = e["horse"] or ""
        if not track or not horse:
            continue
        rows.append((race_date, track, norm_track(track), e["race_number"],
                     horse, _norm(horse), e["bsp"], e["win_lose"], source_file))

    written = 0
    if commit and rows:
        with conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
            written = len(rows)
    elif rows:
        written = len(rows)          # dry run reports what it would store

    rec = {"race_date": race_date, "source_file": source_file,
           "rows_in_file": len(hinted), "rows_written": written,
           "harness_skipped": harness_skipped,
           "status": "OK" if rows else "EMPTY_FOR_DATE"}
    if commit:
        _log(conn, rec)
    return rec


def _log(conn, rec: Dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(LOG_UPSERT, (rec["race_date"], rec["source_file"],
                                 rec["rows_in_file"], rec["rows_written"],
                                 rec["harness_skipped"], rec["status"]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sample", metavar="DATE",
                   help="Print one file's real format and exit; writes nothing")
    p.add_argument("--since", help="First race date (YYYY-MM-DD)")
    p.add_argument("--until", help="Last race date inclusive (YYYY-MM-DD)")
    p.add_argument("--commit", action="store_true",
                   help="Write to the database (default is a dry run)")
    args = p.parse_args(argv)

    if args.sample:
        return sample(args.sample)
    if not (args.since and args.until):
        p.error("give --sample DATE, or both --since and --until")

    dates = daterange(args.since, args.until)
    print(f"[BSP-CORPUS] {len(dates)} date(s) {args.since}..{args.until} "
          f"{'COMMIT' if args.commit else 'DRY RUN'}", file=sys.stderr)

    conn = None
    if args.commit:
        # The same connection shape bsp_settlement uses, keepalives included:
        # a years-long backfill holds one connection across many HTTP fetches
        # and a silently dropped socket mid-range is the expensive failure.
        import psycopg2
        from intelligence_common import KEEPALIVE_KWARGS
        conn = psycopg2.connect(os.environ["DATABASE_URL"], **KEEPALIVE_KWARGS)

    total_rows = total_files = failures = 0
    try:
        for d in dates:
            try:
                rec = ingest_date(conn, d, commit=args.commit)
            except Exception as e:
                failures += 1
                print(f"  {d}: FAILED {type(e).__name__}: {e}", file=sys.stderr)
                continue
            if rec["status"] == "OK":
                total_files += 1
                total_rows += rec["rows_written"]
            print(f"  {d}: {rec['status']:19} in_file={rec['rows_in_file']:5} "
                  f"written={rec['rows_written']:5} harness={rec['harness_skipped']}",
                  file=sys.stderr)
            if conn is not None:
                conn.commit()
    finally:
        if conn is not None:
            conn.close()

    print(f"ROWS {total_rows}")
    print(f"FILES {total_files}")
    print(f"FAILURES {failures}")
    if failures:
        return 4
    if total_rows == 0:
        # Not success. A backfill that stores nothing and exits 0 is the
        # silent no-op this repo keeps finding.
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
