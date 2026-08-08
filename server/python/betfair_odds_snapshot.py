#!/usr/bin/env python3
"""Capture Betfair Exchange WIN prices into runner_odds_snapshots.

Chain: session (providers/betfair_auth.py) -> listMarketCatalogue ->
map markets/runners to STRIDE races and horse_ids (betfair_markets.py) ->
listMarketBook (EX_BEST_OFFERS, virtualised) -> append-only rows in
runner_odds_snapshots (migrations/runner_odds_snapshots.sql, extended by
migrations/runner_odds_snapshots_betfair.sql).

Per mapped runner one row: decimal_odds = best back price (last traded when
no back offer), plus back/lay depth, lastPriceTraded and totalMatched in the
Betfair columns. source_api carries provenance — 'betfair_delayed' or
'betfair_live', decided by the app key's own delayData flag
(getDeveloperAppKeys), so activating the live key flips the value without a
code edit. Re-captures create new rows under a new captured_at; a re-run of
the same capture dedups on the table's primary key (ON CONFLICT DO NOTHING).

Unmapped markets/runners are reported in the run summary, never guessed, and
never abort the rest of the capture. Everything else fails loud: one
transaction, rollback on error, non-zero exit.

Usage:
    DATABASE_URL=... BETFAIR_APP_KEY=... BETFAIR_USERNAME=... BETFAIR_PASSWORD=... \
        python3 server/python/betfair_odds_snapshot.py [--date YYYY-MM-DD] [--commit]

Default is a DRY RUN: fetches, maps, prints every row it would write,
writes nothing. --commit performs the inserts.

Exit codes:
    0  capture ok (including a clean run with nothing to capture)
    1  capture/API/write failure
    2  unusable config (missing BETFAIR_* or DATABASE_URL)
    3  login blocked — terminal credential status; see
       intelligence/betfair_login_blocked.json (delete it after fixing the
       credentials on betfair.com.au). NEVER retried automatically.
    4  network/geo edge block — credentials NOT tested; run from an allowed
       country.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import betfair_markets
from pf_backfill_results import norm_name
from providers import betfair_auth
from providers.betfair_auth import (
    BetfairAuthError, BetfairEdgeBlocked, BetfairLoginBlocked,
)

SNAPSHOT_TABLE = "runner_odds_snapshots"

# roi/04's SNAPSHOT_KINDS CHECK constraint. Betfair captures default to
# 'baseline' (a reference capture, not the pipeline's tip_time moment).
SNAPSHOT_KINDS = ("tip_time", "late_t5", "morning", "baseline")
DEFAULT_KIND = "baseline"

# One exchange, one "bookmaker" per roi/04's one-row-per-bookmaker design.
BOOKMAKER = "betfair"

# roi/04 base columns first, then the additive Betfair columns
# (migrations/runner_odds_snapshots_betfair.sql).
SNAPSHOT_COLUMNS = (
    "race_id", "runner_id", "snapshot_kind", "bookmaker", "captured_at",
    "decimal_odds", "source_api", "seconds_to_jump",
    "race_date", "track", "race_number", "horse_name", "horse_name_norm",
    "market_id", "selection_id", "back_size", "lay_price", "lay_size",
    "last_price_traded", "total_matched",
)

# Append-only: same-key re-runs dedup silently (roi/04's constraint handling);
# genuinely new captures land under a new captured_at.
INSERT_SQL = (
    "INSERT INTO runner_odds_snapshots ({cols}) VALUES ({placeholders}) "
    "ON CONFLICT (race_id, runner_id, snapshot_kind, bookmaker, captured_at) "
    "DO NOTHING"
).format(cols=", ".join(SNAPSHOT_COLUMNS),
         placeholders=", ".join(["%s"] * len(SNAPSHOT_COLUMNS)))

_SYDNEY = ZoneInfo("Australia/Sydney")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _best(offers: Any) -> Tuple[Optional[float], Optional[float]]:
    """(price, size) of the top-of-book offer, if any."""
    if isinstance(offers, list) and offers and isinstance(offers[0], dict):
        return offers[0].get("price"), offers[0].get("size")
    return None, None


def build_snapshot_rows(mapped_markets: Sequence[dict], books: Sequence[dict],
                        captured_at: datetime, source_api: str,
                        snapshot_kind: str) -> Tuple[List[dict], List[dict]]:
    """(rows, skipped). Pure and DB-free — persistence is the caller's call.

    Skipped runners (not in the book, non-ACTIVE, or no usable price above
    1.0 — decimal_odds has a > 1.0 CHECK) are reported, never guessed."""
    if snapshot_kind not in SNAPSHOT_KINDS:
        raise ValueError(f"invalid snapshot_kind {snapshot_kind!r} — must be one of {SNAPSHOT_KINDS}")
    books_by_id = {str(b.get("marketId")): b for b in books}
    rows: List[dict] = []
    skipped: List[dict] = []
    for market in mapped_markets:
        market_id = market["market_id"]
        book = books_by_id.get(market_id)
        if book is None:
            skipped.append({"market_id": market_id, "selection_id": None,
                            "reason": "market missing from listMarketBook reply"})
            continue
        book_runners = {r.get("selectionId"): r for r in book.get("runners", [])}
        market_total = book.get("totalMatched")
        for runner in market["runners"]:
            sel = runner["selection_id"]
            br = book_runners.get(sel)
            if br is None:
                skipped.append({"market_id": market_id, "selection_id": sel,
                                "reason": "runner missing from book"})
                continue
            if br.get("status") and br["status"] != "ACTIVE":
                skipped.append({"market_id": market_id, "selection_id": sel,
                                "reason": f"runner status {br['status']}"})
                continue
            ex = br.get("ex") or {}
            back_price, back_size = _best(ex.get("availableToBack"))
            lay_price, lay_size = _best(ex.get("availableToLay"))
            ltp = br.get("lastPriceTraded")
            decimal_odds = back_price if back_price and back_price > 1.0 else (
                ltp if ltp and ltp > 1.0 else None)
            if decimal_odds is None:
                skipped.append({"market_id": market_id, "selection_id": sel,
                                "reason": "no back offer or traded price above 1.0"})
                continue
            # A short price with no lay offer and no trade history is not a
            # market price — it is a lone speculative offer parked at the
            # bottom of an unformed ladder. Measured 2026-08-08: one bot
            # posted ~$209 at 1.12-1.16 across unformed runners on three
            # tracks; both overnight and morning captures read the standing
            # order back as "the price", movement graded 0, and the market
            # pillar published STABLE/50 on runners whose real prices opened
            # 55-60 (or, against a later sane price, fabricated STRONG_DRIFT
            # on 48 runners in one day). Genuine short prices always carry a
            # lay side or trades; genuine one-sided runners are outsiders —
            # hence the guard is conditioned on the price being short. On the
            # 2 462 depth rows of 2026-08-08 it drops exactly the 5 known-bad
            # rows and none of the 96 legitimately one-sided outsiders.
            if decimal_odds < 3.0 and lay_price is None and ltp is None:
                skipped.append({"market_id": market_id, "selection_id": sel,
                                "reason": f"one-sided book at {decimal_odds}"
                                          f" (no lay offer, never traded,"
                                          f" back_size={back_size})"})
                continue
            start = market.get("start_time")
            rows.append({
                "race_id": market["race_id"],
                "runner_id": runner["horse_id"],
                "snapshot_kind": snapshot_kind,
                "bookmaker": BOOKMAKER,
                "captured_at": captured_at,
                "decimal_odds": round(float(decimal_odds), 2),
                "source_api": source_api,
                # Negative = pre-jump, per roi/04's honesty rule.
                "seconds_to_jump": (int(round((captured_at - start).total_seconds()))
                                    if start else None),
                "race_date": market["race_date"],
                "track": betfair_markets.plain_norm(market["track"]),
                "race_number": market["race_number"],
                "horse_name": runner["horse_name"],
                "horse_name_norm": norm_name(runner["horse_name"]),
                "market_id": market_id,
                "selection_id": sel,
                "back_size": back_size,
                "lay_price": lay_price,
                "lay_size": lay_size,
                "last_price_traded": ltp,
                "total_matched": br.get("totalMatched", market_total),
            })
    return rows, skipped


def persist_rows(conn, rows: Sequence[dict]) -> int:
    """All rows in one transaction; rollback + re-raise on any failure so a
    partial capture can never be silently half-written."""
    if not rows:
        return 0
    payload = [tuple(row.get(col) for col in SNAPSHOT_COLUMNS) for row in rows]
    cur = conn.cursor()
    try:
        cur.executemany(INSERT_SQL, payload)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return len(payload)


def _connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    import psycopg2
    from intelligence_common import KEEPALIVE_KWARGS
    return psycopg2.connect(url, **KEEPALIVE_KWARGS)


def _print_report(mapped, unmapped_markets, unmapped_runners, rows, skipped):
    print(f"\nMapped markets: {len(mapped)}   "
          f"unmapped markets: {len(unmapped_markets)}   "
          f"unmapped runners: {len(unmapped_runners)}   "
          f"skipped runners: {len(skipped)}")
    for m in unmapped_markets:
        print(f"  UNMAPPED MARKET {m['market_id']} venue={m['venue']!r} "
              f"name={m['market_name']!r} start={m['start_time']} — {m['reason']}")
    for r in unmapped_runners:
        print(f"  UNMAPPED RUNNER {r['market_id']}/{r['selection_id']} "
              f"{r['runner_name']!r} ({r['race_id']}) — {r['reason']}")
    for s in skipped:
        print(f"  SKIPPED {s['market_id']}/{s['selection_id']} — {s['reason']}")
    print(f"Snapshot rows built: {len(rows)}")


GAPS_SQL = """
WITH days AS (
    SELECT generate_series(%s::date, %s::date, '1 day')::date AS d
),
race_days AS (
    SELECT d FROM days WHERE
        EXISTS (SELECT 1 FROM race_results_history r WHERE r.race_date::date = days.d)
        OR EXISTS (SELECT 1 FROM race_schedule s WHERE s.race_date::date = days.d)
        OR EXISTS (SELECT 1 FROM prediction_audit p WHERE p.race_date::date = days.d)
),
captured AS (
    SELECT DISTINCT race_date::date AS d FROM runner_odds_snapshots
    WHERE snapshot_kind = 'tip_time'
)
SELECT rd.d, (SELECT COUNT(*) FROM race_results_history r
              WHERE r.race_date::date = rd.d) AS results_rows
FROM race_days rd
LEFT JOIN captured c ON c.d = rd.d
WHERE c.d IS NULL
ORDER BY rd.d
"""


def report_gaps(conn, date_from: str, date_to: str) -> list:
    """Race days in [date_from, date_to] with zero tip_time snapshot rows.

    A day counts as a race day when race_results_history, race_schedule or
    prediction_audit knows about it; it counts as captured when at least one
    tip_time row exists. The capture clock only accrues on captured days, so
    every row this prints is a day added to the retrain wait.
    """
    cur = conn.cursor()
    cur.execute(GAPS_SQL, (date_from, date_to))
    gaps = cur.fetchall()
    cur.close()
    if not gaps:
        print(f"NO GAPS: every race day in {date_from}..{date_to} has tip_time rows")
    else:
        print(f"GAPS ({len(gaps)}): race days with no tip_time snapshot rows")
        for d, results_rows in gaps:
            print(f"  {d}  (results rows that day: {results_rows})")
    return gaps


def main(argv=None, post=None, connect=None) -> int:
    # post/connect are test seams: injected fake transport / DB factory.
    parser = argparse.ArgumentParser(
        description="Capture Betfair WIN prices into runner_odds_snapshots (dry-run by default)")
    parser.add_argument("--date", default=datetime.now(_SYDNEY).strftime("%Y-%m-%d"),
                        help="race day, AEST (default today)")
    parser.add_argument("--commit", action="store_true",
                        help="write rows (default: dry run, print only)")
    parser.add_argument("--kind", default=DEFAULT_KIND, choices=SNAPSHOT_KINDS,
                        help=f"snapshot_kind for the rows (default {DEFAULT_KIND})")
    parser.add_argument("--check-gaps", action="store_true",
                        help="report race days missing tip_time rows, then exit "
                             "(no Betfair call; exit 4 when gaps exist)")
    parser.add_argument("--gaps-from", default="2026-08-02",
                        help="gap scan start (capture epoch: first tip_time row)")
    parser.add_argument("--gaps-to", default=None,
                        help="gap scan end (default: --date)")
    args = parser.parse_args(argv)

    if args.check_gaps:
        conn = (connect or _connect)()
        if conn is None:
            print("CONFIG ERROR: DATABASE_URL not set")
            return 2
        try:
            gaps = report_gaps(conn, args.gaps_from, args.gaps_to or args.date)
        finally:
            conn.close()
        return 4 if gaps else 0

    missing = betfair_auth.missing_config()
    if missing:
        print(f"CONFIG ERROR: missing Betfair config: {', '.join(missing)} (see .env)")
        return 2

    try:
        token = betfair_auth.get_session_token()
    except (BetfairLoginBlocked, BetfairEdgeBlocked) as e:
        print(f"AUTH ERROR: {e}")
        return e.exit_code
    except BetfairAuthError as e:
        print(f"AUTH ERROR: {e}")
        return e.exit_code

    app_key = betfair_auth._config()["app_key"]
    conn = None
    try:
        source_api = betfair_markets.resolve_odds_source(app_key, token, post=post)
        catalogue = betfair_markets.list_win_market_catalogue(
            args.date, app_key, token, post=post)
        print(f"Betfair catalogue: {len(catalogue)} AU WIN markets for {args.date} "
              f"(source_api={source_api})")
        if not catalogue:
            print("Nothing to capture.")
            return 0

        conn = (connect or _connect)()
        if conn is None:
            print("CONFIG ERROR: DATABASE_URL not set — cannot map markets to races")
            return 2
        cur = conn.cursor()
        schedule = betfair_markets.load_schedule(cur, args.date)
        bridge = betfair_markets.load_horse_bridge(cur)
        cur.close()
        if not schedule:
            print(f"WARNING: race_schedule has no rows for {args.date} — "
                  "every market will be unmapped")

        mapped, unmapped_markets, unmapped_runners = betfair_markets.map_markets(
            catalogue, schedule, bridge)

        if args.kind == "tip_time":
            # Same poison guard as odds_snapshots.capture_tip_time_snapshots:
            # a tip_time row must never hold a post-jump price, and this
            # adapter is the only place that can stop it for Exchange pulls.
            now = _utcnow()
            pre_jump = [m for m in mapped
                        if m.get("start_time") and m["start_time"] > now]
            dropped = len(mapped) - len(pre_jump)
            if dropped:
                print(f"tip_time guard: dropped {dropped} market(s) at or "
                      f"past their advertised jump")
            mapped = pre_jump

        books = betfair_markets.list_market_books(
            [m["market_id"] for m in mapped], app_key, token, post=post) if mapped else []
        captured_at = _utcnow()
        rows, skipped = build_snapshot_rows(
            mapped, books, captured_at, source_api, args.kind)

        _print_report(mapped, unmapped_markets, unmapped_runners, rows, skipped)

        if not args.commit:
            for row in rows:
                print("  WOULD WRITE "
                      f"{row['race_id']} {row['horse_name']!r} ({row['runner_id']}) "
                      f"back={row['decimal_odds']}x{row['back_size']} "
                      f"lay={row['lay_price']}x{row['lay_size']} "
                      f"ltp={row['last_price_traded']} matched={row['total_matched']} "
                      f"kind={row['snapshot_kind']} source={row['source_api']}")
            print(f"\nDRY RUN — {len(rows)} rows NOT written. Re-run with --commit to write.")
            return 0

        written = persist_rows(conn, rows)
        print(f"\nCommitted {written} snapshot rows to {SNAPSHOT_TABLE} "
              f"(captured_at={captured_at.isoformat()})")

        # Post-condition. Until now this returned 0 whatever `written` was, so
        # every scheduled run since day zero reported success while committing
        # nothing: race_schedule was unseeded, every market went unmapped, and
        # gate 1's clock — the one input that cannot be backfilled — silently
        # never started.
        #
        # The condition is `mapped and not written`, deliberately not
        # `not written`. On a quiet day Betfair lists no target-track markets,
        # `mapped` is empty, and zero rows is the correct outcome; asserting on
        # `written` alone would take this red about three mornings a week and
        # train the operator to ignore it. Mapped markets that produce no row
        # is the real failure and cannot happen on a quiet day.
        if mapped and not written:
            print(f"POST-CONDITION FAILED: {len(mapped)} market(s) mapped for "
                  f"{args.date} but 0 rows were committed. The capture clock "
                  f"did not advance and tip_time rows cannot be backfilled.")
            return 5
        return 0
    except BetfairEdgeBlocked as e:
        print(f"NETWORK ERROR: {e}")
        return e.exit_code
    except Exception as e:
        print(f"CAPTURE FAILED: {type(e).__name__}: {e}")
        return 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
