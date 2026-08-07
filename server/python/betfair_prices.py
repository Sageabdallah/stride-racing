#!/usr/bin/env python3
"""Serve-side Betfair price source (PUNTINGFORM_MIGRATION.md Phase C item [C]).

One module answers "what is the market right now for this race day" for every
serve-side consumer: odds_movement snapshots, the late-odds watcher, and the
racecard enrichment the tips pipeline scores from. Two paths, tried in order:

  1. DIRECT   - the same betfair_auth + betfair_markets stack the capture job
                uses, mapped against race_schedule. Needs working Betfair
                credentials on this machine and no lockout marker.
  2. DB       - the newest runner_odds_snapshots rows for the date, written
                by the betfair-odds-snapshot workflow on the AU runner. Needs
                no Betfair credentials; staleness equals the capture cadence.

Both paths return the same shape, so a caller cannot tell them apart except
by the source tag, which must follow the price into everything written
downstream (ledger price_source, snapshot source_api).

Return shape of fetch_price_map():
    {
      "source":  "betfair_delayed" | "betfair_live" | "betfair_snapshot_db",
      "fetched_at": datetime,
      "races": {
        (norm_track_key, race_number): {
          "track":   <schedule/display track string>,
          "race_number": int,
          "runners": { horse_name_norm: {"horse": display, "price": float,
                                          "captured_at": datetime|None} },
        }
      }
    }

Loud failure over silent emptiness: if neither path yields a single price,
callers get an empty races dict AND a printed reason, never a quiet {}.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import betfair_markets
from pf_backfill_results import norm_name

# Per-process direct-path context so the watcher's repeated calls reuse one
# session token and one catalogue+mapping per date instead of re-listing on
# every poll. Books are always re-pulled: prices move, mappings do not.
_DIRECT_CTX: Dict[str, Any] = {"date": None, "token": None, "app_key": None,
                               "source": None, "mapped": None}


def _connect():
    import os
    import psycopg2
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    return psycopg2.connect(url)


def _direct_context(date_str: str):
    """(token, app_key, source_api, mapped_markets) for the date, cached.

    Raises on any auth problem (missing config, lockout marker, edge block)
    so the caller can fall back to the DB path with the reason in hand.
    """
    if _DIRECT_CTX["date"] == date_str and _DIRECT_CTX["mapped"] is not None:
        return (_DIRECT_CTX["token"], _DIRECT_CTX["app_key"],
                _DIRECT_CTX["source"], _DIRECT_CTX["mapped"])

    from providers import betfair_auth
    missing = betfair_auth.missing_config()
    if missing:
        raise RuntimeError(f"missing Betfair config: {', '.join(missing)}")
    token = betfair_auth.get_session_token()
    app_key = betfair_auth._config()["app_key"]
    source = betfair_markets.resolve_odds_source(app_key, token)
    catalogue = betfair_markets.list_win_market_catalogue(date_str, app_key, token)

    conn = _connect()
    if conn is None:
        raise RuntimeError("DATABASE_URL not set; cannot map markets to races")
    try:
        cur = conn.cursor()
        schedule = betfair_markets.load_schedule(cur, date_str)
        bridge = betfair_markets.load_horse_bridge(cur)
        cur.close()
    finally:
        conn.close()
    mapped, unmapped_markets, _ = betfair_markets.map_markets(
        catalogue, schedule, bridge)
    if not mapped and catalogue:
        venues = sorted({str(m.get("venue") or "?") for m in unmapped_markets})
        print(f"[BETFAIR_PRICES] {len(catalogue)} markets but 0 mapped for "
              f"{date_str}; unmapped venues: {', '.join(venues)}",
              file=sys.stderr)
    _DIRECT_CTX.update({"date": date_str, "token": token, "app_key": app_key,
                        "source": source, "mapped": mapped})
    return token, app_key, source, mapped


def _races_from_rows(rows) -> Dict[Tuple[str, int], Dict[str, Any]]:
    races: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for track, race_number, horse, price, captured_at in rows:
        if not track or race_number is None or not horse or not price:
            continue
        key = (betfair_markets.norm_track(track), int(race_number))
        race = races.setdefault(key, {"track": track,
                                      "race_number": int(race_number),
                                      "runners": {}})
        hkey = norm_name(horse)
        existing = race["runners"].get(hkey)
        if existing is None or (captured_at and existing.get("captured_at")
                                and captured_at > existing["captured_at"]):
            race["runners"][hkey] = {"horse": horse, "price": float(price),
                                     "captured_at": captured_at}
    return races


def fetch_direct(date_str: str) -> Dict[str, Any]:
    """Live prices straight from the Exchange. Raises on auth/network."""
    token, app_key, source, mapped = _direct_context(date_str)
    if not mapped:
        return {"source": source, "fetched_at": datetime.now(timezone.utc),
                "races": {}}
    from betfair_odds_snapshot import build_snapshot_rows
    books = betfair_markets.list_market_books(
        [m["market_id"] for m in mapped], app_key, token)
    captured_at = datetime.now(timezone.utc)
    rows, _skipped = build_snapshot_rows(mapped, books, captured_at, source,
                                         "morning")
    tuples = [(r["track"], r["race_number"], r["horse_name"],
               r["decimal_odds"], r["captured_at"]) for r in rows]
    return {"source": source, "fetched_at": captured_at,
            "races": _races_from_rows(tuples)}


def fetch_from_db(date_str: str) -> Dict[str, Any]:
    """Newest snapshot row per runner for the date, any snapshot_kind."""
    conn = _connect()
    if conn is None:
        raise RuntimeError("DATABASE_URL not set")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT ON (race_id, runner_id)
                   track, race_number, horse_name, decimal_odds, captured_at
            FROM runner_odds_snapshots
            WHERE race_date = %s
            ORDER BY race_id, runner_id, captured_at DESC
            """,
            (date_str,))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return {"source": "betfair_snapshot_db",
            "fetched_at": datetime.now(timezone.utc),
            "races": _races_from_rows(rows)}


def fetch_price_map(date_str: str, prefer_direct: bool = True) -> Dict[str, Any]:
    """Direct Exchange prices when credentials work, DB snapshots otherwise."""
    if prefer_direct:
        try:
            result = fetch_direct(date_str)
            if result["races"]:
                return result
            print(f"[BETFAIR_PRICES] direct path returned no mapped prices "
                  f"for {date_str}; trying DB snapshots", file=sys.stderr)
        except Exception as e:
            print(f"[BETFAIR_PRICES] direct path unavailable "
                  f"({type(e).__name__}: {e}); falling back to DB snapshots",
                  file=sys.stderr)
    result = fetch_from_db(date_str)
    if not result["races"]:
        print(f"[BETFAIR_PRICES] no prices from any source for {date_str}. "
              "Run the betfair-odds-snapshot workflow (or fix Betfair "
              "credentials) before expecting market-aware picks.",
              file=sys.stderr)
    return result


def fetch_race_prices(date_str: str, track: Any, race_number: Any,
                      prefer_direct: bool = True) -> Optional[Dict[str, Any]]:
    """One race's runners dict, or None when the race has no prices.

    Direct path re-pulls the market book on every call (prices move near the
    jump); the catalogue+mapping context is cached per date.
    """
    key = (betfair_markets.norm_track(track), int(race_number))
    return fetch_price_map(date_str, prefer_direct=prefer_direct)[
        "races"].get(key)
