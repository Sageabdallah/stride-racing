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
    return psycopg2.connect(url)


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
    args = parser.parse_args(argv)

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
