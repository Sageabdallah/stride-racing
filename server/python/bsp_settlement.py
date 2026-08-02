#!/usr/bin/env python3
"""Fill sp / price_close / clv_pct on settled ledger rows from Betfair's
free BSP files (unblocks roi-roadmap task 11).

Why BSP: VR-001 fixes settlement and CLV to SP; the results tables carry
positions only and PF payloads have no dividend keys. Betfair publishes the
official BSP per named runner daily at
promo.betfair.com/betfairsp/prices/dwbfpricesauswin<DDMMYYYY>.csv — free,
no subscription, and the same venue the tip-time prices come from.

Design rules (work order):
  * Gap-aware and idempotent. Targets are settled ledger rows with sp IS
    NULL, so a late-published file is simply picked up by the next run, and
    re-running rewrites identical values.
  * Never a silent NULL. Every row the pass sees gets sp_status —
    BSP_MATCHED / ZERO_BSP / NO_MATCH_IN_FILE / FILE_NOT_PUBLISHED — so
    "not published yet" and "no SP exists" are distinguishable facts.
    FILE_NOT_PUBLISHED is set only on a real HTTP 404; a network error
    raises instead (the job alarm path), touching nothing.
  * The column contract is the existing one. sp is canonical, price_close
    the legacy alias, clv_pct comes from portfolio_risk.closing_line_value —
    imported, never re-implemented. Bets still settle at price_taken
    (selection_ledger's single settlement contract); BSP only fills the
    closing-line side.
  * won cross-check. The CSV's win_lose flag is compared against the
    results-derived won and disagreements are printed loudly, never
    overwritten — results collection stays the source of truth.

listClearedOrders seam: apply_to_rows() consumes (rows, index) — a
cleared-orders source later builds the same index shape and reuses the
whole apply/update path with sp_source='betfair_cleared_orders'.

Exit codes: 0 ok; 4 = some date older than GRACE_DAYS still has no
published file (the self-healing contract's "report, don't pretend" case).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BSP_URL = "https://promo.betfair.com/betfairsp/prices/dwbfpricesauswin{stamp}.csv"
# The filename stamp is the UK PUBLICATION date: the file stamped D holds the
# races whose menu_hint says D-1, verified 100% clean on the 31-07/01-08
# files (658/658 and 1076/1076 rows). Race date R therefore lives in the
# file stamped R+1, and rows are belt-filtered on the menu_hint date.
FILE_STAMP_LAG_DAYS = 1
GRACE_DAYS = 2
SYD = timezone(timedelta(hours=10))  # display/date-arithmetic only

# The BSP files mix codes; harness races carry the gait in event_name
# (e.g. "R7 1720m Trot M") and must not match thoroughbred runners.
_HARNESS = re.compile(r"\b(Trot|Pace)\b", re.IGNORECASE)
_RACE_NO = re.compile(r"^R(\d+)\b")
_CLOTH = re.compile(r"^\d+[a-zA-Z]?\.\s*")
_AMBIGUOUS = object()


def _syd_today() -> date:
    return datetime.now(SYD).date()


def _file_stamp(race_date: str) -> str:
    """DDMMYYYY filename stamp for a race date (publication is R+1)."""
    d = datetime.strptime(race_date, "%Y-%m-%d").date()
    return (d + timedelta(days=FILE_STAMP_LAG_DAYS)).strftime("%d%m%Y")


def _hint_matches(menu_hint: str, race_date: str) -> bool:
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s*$",
                  menu_hint or "")
    if not m:
        return False
    d = datetime.strptime(race_date, "%Y-%m-%d").date()
    return int(m.group(1)) == d.day and m.group(2).lower() == \
        d.strftime("%b").lower()


def _norm(name: str) -> str:
    """One normaliser for matching. Deliberately the settle pass's own rule."""
    from shadow_pl_tracker import _normalize_name
    return _normalize_name(name or "")


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def parse_bsp_csv(text: str,
                  race_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """CSV rows -> matchable entries; harness races filtered out. With a
    race_date, rows are also belt-filtered on the menu_hint date so a
    boundary row from the adjacent day can never cross-match."""
    out = []
    for rec in csv.DictReader(io.StringIO(text)):
        event_name = (rec.get("event_name") or "").strip()
        if _HARNESS.search(event_name):
            continue
        if race_date and not _hint_matches(rec.get("menu_hint"), race_date):
            continue
        m = _RACE_NO.match(event_name)
        sel = (rec.get("selection_name") or "").strip()
        try:
            bsp = float(rec.get("bsp") or 0.0)
        except ValueError:
            bsp = 0.0
        try:
            win_lose: Optional[int] = int(float(rec.get("win_lose")))
        except (TypeError, ValueError):
            win_lose = None
        out.append({
            "track_hint": (rec.get("menu_hint") or "").split("(")[0].strip(),
            "race_number": int(m.group(1)) if m else None,
            "horse": _CLOTH.sub("", sel),
            "bsp": bsp,
            "win_lose": win_lose,
        })
    return out


def build_index(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """(race_number, norm horse) primary; horse-only fallback when unique."""
    by_key: Dict[Tuple[Any, str], Any] = {}
    by_horse: Dict[str, Any] = {}
    for e in entries:
        h = _norm(e["horse"])
        if not h:
            continue
        k = (e["race_number"], h)
        by_key[k] = _AMBIGUOUS if k in by_key else e
        by_horse[h] = _AMBIGUOUS if h in by_horse else e
    return {"by_key": by_key, "by_horse": by_horse, "n": len(entries)}


def _local_cache(d: str):
    from evidence_store import local_dir
    cache = local_dir() / "bsp"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"dwbfpricesauswin{_file_stamp(d)}.csv"


def _cache_valid(text: Optional[str], d: str) -> bool:
    """A cached copy must actually contain rows hinted with the race date —
    a misfiled or stale cache must never silently serve the wrong day."""
    if not text:
        return False
    return any(_hint_matches(r.get("menu_hint"), d)
               for r in csv.DictReader(io.StringIO(text)))


def fetch_csv(d: str, http_get=None) -> Tuple[str, Optional[str]]:
    """('OK'|'FILE_NOT_PUBLISHED', text). Cache order: local, S3, HTTP.

    Raw files are kept in the evidence store for provenance and so a Lambda
    re-run does not depend on promo.betfair.com staying up forever. Cached
    copies are date-validated before use and refetched when they fail.
    """
    path = _local_cache(d)
    if path.exists():
        text = path.read_text()
        if _cache_valid(text, d):
            return "OK", text
        print(f"  [BSP] local cache for {d} fails date validation; refetching",
              file=sys.stderr)

    from evidence_store import EvidenceStoreError, fetch_evidence, put_evidence
    fname = f"bsp_raw_{d}.csv"
    try:
        cached = fetch_evidence(fname)
    except EvidenceStoreError as e:
        print(f"  [BSP] provenance fetch degraded ({e}); trying HTTP",
              file=sys.stderr)
        cached = None
    if cached and _cache_valid(cached, d):
        path.write_text(cached)
        return "OK", cached

    if http_get is None:
        import requests
        def http_get(url):
            r = requests.get(url, timeout=30)
            return r.status_code, r.text
    status, text = http_get(BSP_URL.format(stamp=_file_stamp(d)))
    if status == 404:
        return "FILE_NOT_PUBLISHED", None
    if status != 200 or not text or "selection_name" not in text.splitlines()[0]:
        raise RuntimeError(f"BSP fetch for {d} failed: HTTP {status}")
    path.write_text(text)
    out = put_evidence(fname, text)
    if out.get("s3_error"):
        print(f"  [BSP] provenance upload failed (non-fatal): {out['s3_error']}",
              file=sys.stderr)
    return "OK", text


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def target_rows(conn, since: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, race_date::text AS race_date, track, race_number, "
            "horse_name, price_taken, won, sp_status FROM selection_ledger "
            "WHERE race_date >= %s AND settled = TRUE AND sp IS NULL "
            "ORDER BY race_date, track, race_number", (since,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        cur.close()


def apply_to_rows(rows: List[Dict[str, Any]], index: Dict[str, Any],
                  source: str = "betfair_bsp_file") -> Dict[str, Any]:
    """Pure matching pass: returns updates + stats, writes nothing."""
    from portfolio_risk import closing_line_value

    updates, stats = [], {"matched": 0, "zero_bsp": 0, "no_match": 0,
                          "won_disagree": 0, "track_warns": 0}
    for row in rows:
        h = _norm(row["horse_name"])
        entry = index["by_key"].get((row["race_number"], h))
        if entry is None:
            entry = index["by_horse"].get(h)
        if entry is _AMBIGUOUS or entry is None:
            stats["no_match"] += 1
            updates.append({"id": row["id"], "sp": None, "clv_pct": None,
                            "sp_source": None, "sp_status": "NO_MATCH_IN_FILE"})
            continue
        if _norm(entry["track_hint"]) and _norm(row["track"]) and \
                _norm(entry["track_hint"])[:4] not in _norm(row["track"]) and \
                _norm(row["track"])[:4] not in _norm(entry["track_hint"]):
            stats["track_warns"] += 1
            print(f"  [BSP] track mismatch (matched on horse+race anyway): "
                  f"ledger '{row['track']}' vs file '{entry['track_hint']}' "
                  f"({row['horse_name']})", file=sys.stderr)
        bsp = float(entry["bsp"] or 0.0)
        if bsp <= 1.0:
            stats["zero_bsp"] += 1
            updates.append({"id": row["id"], "sp": None, "clv_pct": None,
                            "sp_source": source, "sp_status": "ZERO_BSP"})
            continue
        if row.get("won") is not None and entry.get("win_lose") is not None \
                and bool(row["won"]) != bool(entry["win_lose"]):
            stats["won_disagree"] += 1
            print(f"  [BSP] *** RESULT DISAGREEMENT (not overwritten): "
                  f"{row['race_date']} {row['track']} R{row['race_number']} "
                  f"{row['horse_name']}: ledger won={row['won']} vs "
                  f"file win_lose={entry['win_lose']}", file=sys.stderr)
        taken = row.get("price_taken")
        clv = (round(closing_line_value(float(taken), bsp), 4)
               if taken else None)
        stats["matched"] += 1
        updates.append({"id": row["id"], "sp": round(bsp, 4), "clv_pct": clv,
                        "sp_source": source, "sp_status": "BSP_MATCHED"})
    return {"updates": updates, "stats": stats}


def write_updates(conn, updates: List[Dict[str, Any]]) -> int:
    cur = conn.cursor()
    n = 0
    try:
        for u in updates:
            if u["sp"] is not None:
                cur.execute(
                    "UPDATE selection_ledger SET sp = %s, price_close = %s, "
                    "clv_pct = %s, sp_source = %s, sp_status = %s, "
                    "updated_at = NOW() WHERE id = %s",
                    (u["sp"], u["sp"], u["clv_pct"], u["sp_source"],
                     u["sp_status"], u["id"]))
            else:
                cur.execute(
                    "UPDATE selection_ledger SET sp_source = %s, sp_status = %s, "
                    "updated_at = NOW() WHERE id = %s",
                    (u["sp_source"], u["sp_status"], u["id"]))
            n += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return n


def mark_file_missing(conn, d: str, commit: bool) -> int:
    """Only rows the pass has not already classified from a published file."""
    if not commit:
        return 0
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE selection_ledger SET sp_status = 'FILE_NOT_PUBLISHED', "
            "updated_at = NOW() WHERE race_date = %s AND settled = TRUE "
            "AND sp IS NULL AND (sp_status IS NULL "
            "OR sp_status = 'FILE_NOT_PUBLISHED')", (d,))
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Reconciliation (verification mode, read-only)
# ---------------------------------------------------------------------------

def reconcile_results(conn, d: str, http_get=None) -> int:
    """Match a day's BSP file against race_results_history — proves parsing,
    matching and the winner flag on known races before any ledger write."""
    status, text = fetch_csv(d, http_get=http_get)
    if status != "OK":
        print(f"RECONCILE {d}: file not published yet")
        return 1
    index = build_index(parse_bsp_csv(text, race_date=d))
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT track, race_number, horse_name, position "
            "FROM race_results_history WHERE race_date = %s", (d,))
        results = cur.fetchall()
    finally:
        cur.close()
    matched = agree = winners = 0
    samples: List[str] = []
    for track, race_no, horse, pos in results:
        entry = index["by_key"].get((race_no, _norm(horse))) \
            or index["by_horse"].get(_norm(horse))
        if entry is _AMBIGUOUS or entry is None:
            continue
        matched += 1
        is_win = (pos == 1)
        winners += int(is_win)
        if entry["win_lose"] is not None and bool(entry["win_lose"]) == is_win:
            agree += 1
        if len(samples) < 12 and entry["bsp"] > 1.0:
            samples.append(
                f"  {track} R{race_no} {horse}: BSP {entry['bsp']:.2f} "
                f"win_lose={entry['win_lose']} our_position={pos}")
    print(f"RECONCILE {d}: {len(results)} result rows, {matched} matched in "
          f"BSP file ({matched / len(results) * 100:.1f}%), winner-flag "
          f"agreement {agree}/{matched}, winners seen {winners}"
          if results else f"RECONCILE {d}: no result rows in DB")
    for s in samples:
        print(s)
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill sp/price_close/clv_pct on settled ledger rows from BSP files")
    parser.add_argument("--since", default="2026-08-02",
                        help="Day zero for the gap-aware sweep")
    parser.add_argument("--date", default=None,
                        help="Restrict to one race date")
    parser.add_argument("--commit", action="store_true",
                        help="Write updates (default: dry-run report)")
    parser.add_argument("--reconcile-results", metavar="DATE", default=None,
                        help="Read-only: match a day's file against race_results_history")
    args = parser.parse_args()

    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        if args.reconcile_results:
            return reconcile_results(conn, args.reconcile_results)

        rows = target_rows(conn, args.date or args.since)
        if args.date:
            rows = [r for r in rows if r["race_date"] == args.date]
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_date.setdefault(r["race_date"], []).append(r)

        filled = 0
        missing_dates: List[str] = []
        for d in sorted(by_date):
            status, text = fetch_csv(d)
            if status == "FILE_NOT_PUBLISHED":
                n = mark_file_missing(conn, d, args.commit)
                missing_dates.append(d)
                print(f"{d}: file not published yet "
                      f"({len(by_date[d])} row(s) waiting, {n} marked)")
                continue
            result = apply_to_rows(
                by_date[d], build_index(parse_bsp_csv(text, race_date=d)))
            s = result["stats"]
            if args.commit:
                n = write_updates(conn, result["updates"])
            else:
                n = 0
            filled += s["matched"]
            print(f"{d}: {len(by_date[d])} target row(s) -> matched "
                  f"{s['matched']}, zero_bsp {s['zero_bsp']}, no_match "
                  f"{s['no_match']}, won_disagreements {s['won_disagree']}"
                  f"{' (dry-run, nothing written)' if not args.commit else f', {n} updated'}")

        print(f"FILLED {filled} row(s) across {len(by_date)} date(s)")
        # The honesty exit: a file this old should exist — report as a gap.
        stale = [d for d in missing_dates
                 if datetime.strptime(d, "%Y-%m-%d").date()
                 <= _syd_today() - timedelta(days=GRACE_DAYS)]
        if stale:
            print(f"BSP GAP: no published file after {GRACE_DAYS} day(s) for: "
                  f"{', '.join(stale)}", file=sys.stderr)
            return 4
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
