#!/usr/bin/env python3
"""
STRIDE Odds Movement — Smart Money / Steam / Drift Detection.

Captures multi-timepoint odds snapshots and detects price movements.
Prices come from betfair_prices (direct Exchange when credentials work on
this machine, freshest runner_odds_snapshots rows otherwise). The Racing
API client that served Phase 1 is gone with the provider migration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "intelligence"))

import target_tracks

from intelligence.common import (
    INTELLIGENCE_DIR,
    RACECARDS_DIR,
    flatten_races,
    get_connection,
    load_racecard_meetings,
    normalize_name,
    normalize_track,
    safe_float,
)


_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

TARGET_TRACKS = [
    "flemington", "caulfield", "caulfield heath", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "ascot", "belmont", "pinjarra",
    "morphettville", "murray bridge", "gawler",
    "kensington", "randwick-kensington",
    "cranbourne", "pakenham", "geelong", "ballarat", "bendigo",
    "gold coast", "sunshine coast", "ipswich", "toowoomba",
    "gosford", "newcastle", "kembla grange", "hawkesbury", "wyong",
]


def _is_target_track(course: str) -> bool:
    # The list above stays this module's own — it is the market pillar's
    # universe, not the card's, and they differ deliberately. Only the
    # comparison is shared. Measured over 516 distinct course spellings
    # (stored cards + race_results_history + the track-distance profiles):
    # identical results to the bidirectional test this replaces, 95 matched
    # either way, zero disagreements.
    return target_tracks.matches(course, TARGET_TRACKS)


def fetch_fresh_odds(date_str: str) -> dict[str, dict[str, float]]:
    """Current Betfair prices for every runner on date. Same contract the
    Racing API version served: {"{track_key}_R{N}": {"horse_name": odds}}.
    Fed by betfair_prices: direct Exchange when credentials work, otherwise
    the freshest runner_odds_snapshots rows the AU runner captured."""
    import betfair_prices
    price_map = betfair_prices.fetch_price_map(date_str)
    odds_map: dict[str, dict[str, float]] = {}
    for (_key, race_number), race in price_map["races"].items():
        course = race["track"] or ""
        if not _is_target_track(course):
            continue
        race_key = f"{normalize_track(course)}_R{int(race_number)}"
        race_odds = {q["horse"]: round(float(q["price"]), 2)
                     for q in race["runners"].values() if q.get("price")}
        if race_odds:
            odds_map[race_key] = race_odds
    if not odds_map:
        print(f"[ODDS] No Betfair prices for {date_str} from any source",
              file=sys.stderr)
    return odds_map


def read_racecard_odds(date_str: str) -> dict[str, dict[str, float]]:
    """Read odds from the cached racecard file as fallback."""
    try:
        meetings = load_racecard_meetings(date_str)
    except FileNotFoundError:
        return {}

    races = flatten_races(meetings)
    odds_map: dict[str, dict[str, float]] = {}

    for race in races:
        race_key = f"{race['track_key']}_R{race['race_number']}"
        race_odds: dict[str, float] = {}
        for runner in race["runners"]:
            horse = runner.get("horse", "")
            if not horse:
                continue
            odds_list = runner.get("odds", [])
            if isinstance(odds_list, list):
                from market_prob import reference_price
                ref = reference_price(odds_list)
                if ref:
                    race_odds[horse] = round(ref, 2)
            if horse not in race_odds:
                sp = safe_float(runner.get("sp"))
                if sp and sp > 1.0:
                    race_odds[horse] = round(sp, 2)
        if race_odds:
            odds_map[race_key] = race_odds

    return odds_map


# Thresholds aligned with market_velocity.py _classify_momentum()
def classify_signal(movement_pct: float) -> tuple[str, float]:
    """Classify price movement and return (signal_type, market_signal_score). Phase 1: no volume_change_pct available (Racing API = prices only)."""
    if movement_pct >= 20:
        return "STEAM", min(85.0 + movement_pct - 20, 95.0)
    elif movement_pct >= 10:
        return "FIRMING", 65.0 + (movement_pct - 10)
    elif movement_pct <= -15:
        return "STRONG_DRIFT", max(15.0, 30.0 + movement_pct + 15)
    elif movement_pct <= -8:
        return "DRIFT", max(30.0, 45.0 + movement_pct + 8)
    else:
        return "STABLE", 50.0


def capture_snapshot(date_str: str, snapshot_type: str, dry_run: bool = False) -> dict:
    """Capture current odds for all runners and store as a snapshot. Returns the odds map."""
    print(f"[ODDS] Capturing {snapshot_type} snapshot for {date_str}", file=sys.stderr)

    odds_map = fetch_fresh_odds(date_str)
    if not odds_map:
        print("[ODDS] Fresh API returned no data, falling back to cached racecard", file=sys.stderr)
        odds_map = read_racecard_odds(date_str)

    if not odds_map:
        print(f"[ODDS] No odds data available for {date_str}", file=sys.stderr)
        return {}

    total_runners = sum(len(v) for v in odds_map.values())
    print(f"[ODDS] Found odds for {total_runners} runners across {len(odds_map)} races", file=sys.stderr)

    if dry_run:
        for race_key, runners in list(odds_map.items())[:3]:
            print(f"  [DRY-RUN] {race_key}:", file=sys.stderr)
            for horse, odds in list(runners.items())[:3]:
                print(f"    {horse}: ${odds:.2f}", file=sys.stderr)
        return odds_map

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for race_key, runners in odds_map.items():
                parts = race_key.rsplit("_R", 1)
                if len(parts) != 2:
                    continue
                track_key, race_num_str = parts
                race_num = int(race_num_str)

                for horse, odds_val in runners.items():
                    cur.execute(
                        """INSERT INTO betfair_odds_snapshots
                           (race_date, track, race_number, horse_name, horse_name_norm,
                            snapshot_type, back_price)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (race_date, track, race_number, horse_name_norm, snapshot_type)
                           DO UPDATE SET
                            back_price = EXCLUDED.back_price,
                            snapshot_time = NOW()""",
                        (date_str, track_key, race_num, horse, normalize_name(horse),
                         snapshot_type, odds_val),
                    )
        print(f"[ODDS] Stored {total_runners} snapshots ({snapshot_type})", file=sys.stderr)
    except Exception as e:
        print(f"[ODDS] DB write error: {e}", file=sys.stderr)
    finally:
        conn.close()

    return odds_map


def compute_market_signals(date_str: str, dry_run: bool = False) -> dict:
    """Compare morning prices against baseline. Produces market_signals_<DATE>.json and writes to market_signal_scores table."""
    morning_odds = capture_snapshot(date_str, "MORNING_CHECK", dry_run=dry_run)

    baseline_odds = {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT track, race_number, horse_name, back_price
                   FROM betfair_odds_snapshots
                   WHERE race_date = %s AND snapshot_type = 'BASELINE_NIGHT'""",
                (date_str,),
            )
            for row in cur.fetchall():
                track_key = row[0]
                race_num = row[1]
                horse = row[2]
                price = row[3]
                race_key = f"{track_key}_R{race_num}"
                if race_key not in baseline_odds:
                    baseline_odds[race_key] = {}
                baseline_odds[race_key][horse] = price
    except Exception as e:
        print(f"[ODDS] Failed to load baseline: {e}", file=sys.stderr)
    finally:
        conn.close()

    if not baseline_odds:
        print(f"[ODDS] No baseline snapshot found for {date_str}. "
              f"Market pillar will default to neutral (50). "
              f"This is expected on day one.", file=sys.stderr)

    signal_results: dict = {}
    signal_rows: list[dict] = []

    for race_key, morning_runners in morning_odds.items():
        race_signals = {}
        baseline_race = baseline_odds.get(race_key, {})

        for horse, morning_price in morning_runners.items():
            baseline_price = baseline_race.get(horse)

            if not baseline_price or baseline_price <= 1.0 or morning_price <= 1.0:
                race_signals[horse] = {
                    "market_signal_score": 50.0,
                    "signal_type": "UNKNOWN",
                    "baseline_price": baseline_price,
                    "morning_price": morning_price,
                    "movement_pct": 0.0,
                }
                continue

            movement_pct = round(((baseline_price - morning_price) / baseline_price) * 100, 2)
            signal_type, score = classify_signal(movement_pct)

            race_signals[horse] = {
                "market_signal_score": score,
                "signal_type": signal_type,
                "baseline_price": baseline_price,
                "morning_price": morning_price,
                "movement_pct": movement_pct,
            }

            parts = race_key.rsplit("_R", 1)
            if len(parts) == 2:
                signal_rows.append({
                    "track": parts[0],
                    "race_number": int(parts[1]),
                    "horse_name": horse,
                    "baseline_price": baseline_price,
                    "morning_price": morning_price,
                    "movement_pct": movement_pct,
                    "signal_type": signal_type,
                    "market_signal_score": score,
                })

        if race_signals:
            signal_results[race_key] = race_signals

    for race_key, signals in signal_results.items():
        notable = [(h, s) for h, s in signals.items() if s["signal_type"] not in ("STABLE", "UNKNOWN")]
        if notable:
            for horse, sig in notable:
                print(
                    f"  {race_key} {horse}: {sig['signal_type']} "
                    f"({sig['baseline_price']:.2f} → {sig['morning_price']:.2f}, "
                    f"{sig['movement_pct']:+.1f}%)",
                    file=sys.stderr,
                )

    if not dry_run and signal_rows:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for r in signal_rows:
                    cur.execute(
                        """INSERT INTO market_signal_scores
                           (race_date, track, race_number, horse_name, horse_name_norm,
                            baseline_price, morning_price, price_movement_pct,
                            signal_type, market_signal_score)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (race_date, track, race_number, horse_name_norm)
                           DO UPDATE SET
                            baseline_price = EXCLUDED.baseline_price,
                            morning_price = EXCLUDED.morning_price,
                            price_movement_pct = EXCLUDED.price_movement_pct,
                            signal_type = EXCLUDED.signal_type,
                            market_signal_score = EXCLUDED.market_signal_score,
                            created_at = NOW()""",
                        (date_str, r["track"], r["race_number"],
                         r["horse_name"], normalize_name(r["horse_name"]),
                         r["baseline_price"], r["morning_price"],
                         r["movement_pct"], r["signal_type"],
                         r["market_signal_score"]),
                    )
            print(f"[ODDS] Stored {len(signal_rows)} market signal scores", file=sys.stderr)
        except Exception as e:
            print(f"[ODDS] DB write error: {e}", file=sys.stderr)
        finally:
            conn.close()

    output_path = INTELLIGENCE_DIR / f"market_signals_{date_str}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(signal_results, indent=2, default=str))
    print(f"[ODDS] Written to {output_path}", file=sys.stderr)

    return signal_results


def main():
    parser = argparse.ArgumentParser(description="STRIDE Odds Movement Detection")
    parser.add_argument("date", help="Race date YYYY-MM-DD")
    parser.add_argument(
        "--snapshot",
        required=True,
        choices=["baseline_night", "morning"],
        help="Snapshot type: baseline_night (12:30 AM) or morning (8 AM with movement calc)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show data without writing to DB")
    args = parser.parse_args()

    if args.snapshot == "baseline_night":
        capture_snapshot(args.date, "BASELINE_NIGHT", dry_run=args.dry_run)
    elif args.snapshot == "morning":
        compute_market_signals(args.date, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
