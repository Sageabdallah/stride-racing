#!/usr/bin/env python3
"""
Collects results for races STRIDE has tipped, scores tip accuracy,
fetches sectional times, and triggers franking updates.

Usage:
    python stride_results_collector.py 2026-03-28
    python stride_results_collector.py 2026-03-25 2026-03-28       (date range)
    python stride_results_collector.py 2026-03-28 --parallel
    python stride_results_collector.py 2026-03-28 --dry-run
    python stride_results_collector.py 2026-03-28 --skip-sectionals --skip-franking
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# CONN_ERRORS: connection-level failures that mean OUR infrastructure broke,
# as opposed to a provider having nothing for the date. The warn-and-continue
# handlers below must not swallow these — a dead socket turned into
# "0 rows, exit 0".
from intelligence_common import CONN_ERRORS


@contextmanager
def _db_phase():
    """One connection per DB-touching phase. The whole-run connection died
    under Step 3/4's minutes of network and subprocess time (Neon closes
    idle sockets) and took every later step with it."""
    conn = _get_connection()
    try:
        yield conn
    finally:
        conn.close()

from sp_health import compute_sp_health

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RACECARDS_DIR = PROJECT_ROOT / "racecards"


def _load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"')
                if key not in os.environ:
                    os.environ[key] = val


def _get_connection():
    import psycopg2
    from intelligence_common import KEEPALIVE_KWARGS
    _load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(url, **KEEPALIVE_KWARGS)
    conn.autocommit = True
    return conn



CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stride_tip_results (
    id                SERIAL PRIMARY KEY,
    race_date         TEXT,
    tip_date          TEXT,
    track             TEXT NOT NULL,
    race_number       INTEGER NOT NULL,
    race_id           TEXT,
    horse_name        TEXT,
    tipped_horse_id   TEXT,
    tipped_horse_name TEXT,
    actual_winner_id  TEXT,
    actual_winner_name TEXT,
    tip_type          TEXT,
    tip_rank          INTEGER,
    tipped_odds       NUMERIC(8,2),
    tipped_win_pct    NUMERIC(6,2),
    tipped_edge_pct   NUMERIC(6,2),
    confidence        TEXT,
    actual_position   INTEGER,
    field_size        INTEGER,
    api_sp            NUMERIC(8,2),
    tipped_horse_sp   REAL,
    winner_sp         REAL,
    edge_pct          REAL,
    result            TEXT NOT NULL,
    profit_loss       NUMERIC(10,2),
    distance          TEXT,
    race_class        TEXT,
    going             TEXT,
    created_at        TIMESTAMP DEFAULT NOW(),
    collected_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS race_date TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tip_date TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS race_id TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS horse_name TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_horse_id TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_horse_name TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS actual_winner_id TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS actual_winner_name TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tip_type TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tip_rank INTEGER;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_odds NUMERIC(8,2);
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_win_pct NUMERIC(6,2);
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_edge_pct NUMERIC(6,2);
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS confidence TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS actual_position INTEGER;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS field_size INTEGER;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS api_sp NUMERIC(8,2);
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS tipped_horse_sp REAL;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS winner_sp REAL;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS edge_pct REAL;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS profit_loss NUMERIC(10,2);
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS distance TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS race_class TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS going TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS collected_at TIMESTAMPTZ DEFAULT NOW();

UPDATE stride_tip_results
SET race_date = COALESCE(race_date, tip_date),
    tip_date = COALESCE(tip_date, race_date),
    horse_name = COALESCE(horse_name, tipped_horse_name),
    tipped_horse_name = COALESCE(tipped_horse_name, horse_name),
    api_sp = COALESCE(api_sp, tipped_horse_sp),
    tipped_horse_sp = COALESCE(tipped_horse_sp, api_sp::REAL),
    tipped_edge_pct = COALESCE(tipped_edge_pct, edge_pct::NUMERIC(6,2)),
    edge_pct = COALESCE(edge_pct, tipped_edge_pct::REAL),
    collected_at = COALESCE(collected_at, created_at, NOW())
WHERE race_date IS NULL
   OR tip_date IS NULL
   OR horse_name IS NULL
   OR tipped_horse_name IS NULL
   OR api_sp IS NULL
   OR tipped_horse_sp IS NULL
   OR tipped_edge_pct IS NULL
   OR edge_pct IS NULL
   OR collected_at IS NULL;

ALTER TABLE stride_tip_results DROP CONSTRAINT IF EXISTS stride_tip_results_result_check;
ALTER TABLE stride_tip_results
    ADD CONSTRAINT stride_tip_results_result_check
    CHECK (result IN ('WIN', 'PLACE', 'LOSS', 'SCRATCHED'));

CREATE INDEX IF NOT EXISTS idx_stride_tip_results_date ON stride_tip_results(race_date);
CREATE INDEX IF NOT EXISTS idx_stride_tip_results_result ON stride_tip_results(result);
CREATE INDEX IF NOT EXISTS stride_tip_results_tip_date_track_race_idx
    ON stride_tip_results(tip_date, track, race_number);
CREATE INDEX IF NOT EXISTS stride_tip_results_race_id_idx
    ON stride_tip_results(race_id);
CREATE UNIQUE INDEX IF NOT EXISTS stride_tip_results_race_horse_tip_idx
    ON stride_tip_results(race_date, track, race_number, horse_name, tip_type);
"""


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)



@dataclass
class TippedHorse:
    horse_name: str
    tip_type: str
    tip_rank: int
    tipped_odds: float
    tipped_win_pct: float
    tipped_edge_pct: float
    confidence: str


@dataclass
class TippedRace:
    date: str
    track: str
    race_number: int
    distance: str
    race_class: str
    going: str
    horses: List[TippedHorse] = field(default_factory=list)



def find_tipped_races(dates: List[str]) -> List[TippedRace]:
    """Scan tips files for given dates, extract races with BET or COVERAGE picks."""
    tipped = []
    for date_str in dates:
        tips_path = RACECARDS_DIR / f"tips_{date_str}.json"
        if not tips_path.exists():
            print(f"  [RESULTS] No tips file for {date_str}", file=sys.stderr)
            continue

        with open(tips_path) as f:
            tips = json.load(f)

        for race in tips.get("races", []):
            horses = []

            bet_pick = race.get("bet_pick") or race.get("primary_pick")
            if bet_pick and race.get("bet_status") == "BET":
                horses.append(TippedHorse(
                    horse_name=bet_pick.get("horse", ""),
                    tip_type="BET",
                    tip_rank=bet_pick.get("rank", 1),
                    tipped_odds=float(bet_pick.get("odds", 0) or 0),
                    tipped_win_pct=float(bet_pick.get("win_pct", 0) or 0),
                    tipped_edge_pct=float(bet_pick.get("edge_pct", 0) or 0),
                    confidence=bet_pick.get("confidence", ""),
                ))

            coverage = race.get("coverage_pick")
            if coverage and coverage.get("horse"):
                # Don't double-count if same horse as bet pick
                cov_name = coverage.get("horse", "")
                if not any(h.horse_name.lower() == cov_name.lower() for h in horses):
                    horses.append(TippedHorse(
                        horse_name=cov_name,
                        tip_type="COVERAGE",
                        tip_rank=coverage.get("rank", 2),
                        tipped_odds=float(coverage.get("odds", 0) or 0),
                        tipped_win_pct=float(coverage.get("win_pct", 0) or 0),
                        tipped_edge_pct=float(coverage.get("edge_pct", 0) or 0),
                        confidence=coverage.get("confidence", ""),
                    ))

            if not horses:
                continue

            tipped.append(TippedRace(
                date=date_str,
                track=race.get("track", ""),
                race_number=int(race.get("race_number", 0)),
                distance=race.get("distance", ""),
                race_class=race.get("race_class", ""),
                going=race.get("going", ""),
                horses=horses,
            ))

    print(f"  [RESULTS] Found {len(tipped)} tipped races across {len(dates)} date(s)", file=sys.stderr)
    return tipped



def _normalize_track(track: str) -> str:
    """Strip sponsor prefixes for track matching."""
    import re
    track = re.sub(
        r"^(Sportsbet|Ladbrokes|bet365|TAB|Picklebet|Neds|Betfair)\s+",
        "", track, flags=re.IGNORECASE
    ).strip()
    return track.lower()


def check_existing_results(conn, tipped_races: List[TippedRace]) -> Tuple[List[TippedRace], List[TippedRace]]:
    """Split tipped races into (need_fetch, already_have) based on DB."""
    if not tipped_races:
        return [], []

    dates = list({tr.date for tr in tipped_races})
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT race_date, LOWER(track), race_number
            FROM race_results_history
            WHERE race_date = ANY(%s)
        """, (dates,))
        existing_keys = set()
        for row in cur.fetchall():
            existing_keys.add((str(row[0])[:10], row[1], row[2]))

    needed = []
    have = []
    for tr in tipped_races:
        key = (tr.date, _normalize_track(tr.track), tr.race_number)
        if key in existing_keys:
            have.append(tr)
        else:
            needed.append(tr)

    print(f"  [RESULTS] Already have: {len(have)} races, need: {len(needed)} races", file=sys.stderr)
    return needed, have



def fetch_and_store_results(needed: List[TippedRace], parallel: bool = False) -> Dict:
    """Fetch race results from API and store in race_results_history.

    Opens its own connection only after the fetch loop: the fetching can run
    minutes, and a connection opened before it arrives at import time dead.
    """
    dates_needed = sorted({tr.date for tr in needed})
    if not dates_needed:
        print("  [RESULTS] No races to fetch", file=sys.stderr)
        return {"dates_fetched": 0, "rows_imported": 0}

    from fetch_and_import_date import fetch_date, import_meetings

    summary = {"dates_fetched": 0, "rows_imported": 0, "errors": []}

    def _fetch_one(date_str):
        try:
            meetings = fetch_date(date_str)
            return date_str, meetings, None
        except Exception as e:
            return date_str, [], str(e)

    if parallel and len(dates_needed) > 1:
        print(f"  [RESULTS] Fetching {len(dates_needed)} dates in parallel...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_fetch_one, d): d for d in dates_needed}
            all_meetings = []
            for fut in as_completed(futures):
                d, meetings, err = fut.result()
                if err:
                    summary["errors"].append(f"{d}: {err}")
                    print(f"  [RESULTS] Error fetching {d}: {err}", file=sys.stderr)
                else:
                    all_meetings.extend(meetings)
                    summary["dates_fetched"] += 1
    else:
        all_meetings = []
        for d in dates_needed:
            print(f"  [RESULTS] Fetching results for {d}...", file=sys.stderr)
            try:
                meetings = fetch_date(d)
                all_meetings.extend(meetings)
                summary["dates_fetched"] += 1
            except Exception as e:
                summary["errors"].append(f"{d}: {e}")
                print(f"  [RESULTS] Error fetching {d}: {e}", file=sys.stderr)

    if all_meetings:
        with _db_phase() as conn:
            import_meetings(all_meetings, conn)
        for m in all_meetings:
            for race in m.get("races", []):
                runners = race.get("runners", [])
                active = [r for r in runners if not r.get("scratched", False)]
                summary["rows_imported"] += len(active)

    print(f"  [RESULTS] Fetched {summary['dates_fetched']} date(s), ~{summary['rows_imported']} runners imported", file=sys.stderr)
    return summary



def fetch_sectional_times(dates: List[str], parallel: bool = False) -> Dict:
    """Trigger sectional collection for all states."""
    summary = {"qld": 0, "vic_sa": 0, "nsw": 0, "errors": []}

    def _collect_qld():
        try:
            from auto_results_collector import collect_sectional_times
            return collect_sectional_times(dates)
        except Exception as e:
            summary["errors"].append(f"QLD: {e}")
            print(f"  [SECTIONALS] QLD error: {e}", file=sys.stderr)
            return 0

    def _collect_vic_sa():
        try:
            from auto_results_collector import collect_racing_com_sectionals
            return collect_racing_com_sectionals(dates)
        except Exception as e:
            summary["errors"].append(f"VIC/SA: {e}")
            print(f"  [SECTIONALS] VIC/SA error: {e}", file=sys.stderr)
            return 0

    def _collect_nsw():
        try:
            total = 0
            for d in dates:
                result = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / "nsw_sectional_collector.py"), "--date", d],
                    capture_output=True, text=True, timeout=300,
                )
                if result.returncode == 0:
                    total += 1
                else:
                    print(f"  [SECTIONALS] NSW {d}: {result.stderr[:200]}", file=sys.stderr)
            return total
        except Exception as e:
            summary["errors"].append(f"NSW: {e}")
            print(f"  [SECTIONALS] NSW error: {e}", file=sys.stderr)
            return 0

    print(f"  [SECTIONALS] Collecting sectional times for {len(dates)} date(s)...", file=sys.stderr)

    if parallel:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fq = pool.submit(_collect_qld)
            fv = pool.submit(_collect_vic_sa)
            fn = pool.submit(_collect_nsw)
            summary["qld"] = fq.result()
            summary["vic_sa"] = fv.result()
            summary["nsw"] = fn.result()
    else:
        summary["qld"] = _collect_qld()
        summary["vic_sa"] = _collect_vic_sa()
        summary["nsw"] = _collect_nsw()

    total = summary["qld"] + summary["vic_sa"] + summary["nsw"]
    if total > 0:
        print(f"  [SECTIONALS] Total imported: {total} (QLD:{summary['qld']} VIC/SA:{summary['vic_sa']} NSW:{summary['nsw']})", file=sys.stderr)
    else:
        print(f"  [SECTIONALS] No sectional times available (SECTIONALS_UNAVAILABLE)", file=sys.stderr)

    return summary



def _match_horse_name(tipped_name: str, result_name: str) -> bool:
    """Case-insensitive substring match (same as auto_results_collector)."""
    t = tipped_name.strip().lower()
    r = result_name.strip().lower()
    return t in r or r in t


def score_tip_accuracy(conn, tipped_races: List[TippedRace]) -> List[Dict]:
    """Compare tipped horses against actual results. Upsert into stride_tip_results."""
    if not tipped_races:
        return []

    results = []

    with conn.cursor() as cur:
        for tr in tipped_races:
            cur.execute("""
                SELECT horse_name, position, sp_odds, field_size
                FROM race_results_history
                WHERE race_date = %s AND race_number = %s
            """, (tr.date, tr.race_number))
            race_results = cur.fetchall()

            if not race_results:
                continue

            # Also try track-filtered query if we got too many rows
            # (multiple tracks on same date with same race number)
            if len(race_results) > 30:
                cur.execute("""
                    SELECT horse_name, position, sp_odds, field_size
                    FROM race_results_history
                    WHERE race_date = %s AND race_number = %s
                      AND LOWER(track) LIKE %s
                """, (tr.date, tr.race_number, f"%{_normalize_track(tr.track)}%"))
                filtered = cur.fetchall()
                if filtered:
                    race_results = filtered

            field_size = race_results[0][3] if race_results else 0

            for th in tr.horses:
                matched = None
                for rr in race_results:
                    if _match_horse_name(th.horse_name, rr[0]):
                        matched = rr
                        break

                if matched:
                    pos = matched[1]
                    api_sp = float(matched[2]) if matched[2] else 0.0
                    if pos == 1:
                        result_label = "WIN"
                        pnl = (api_sp - 1) * 100 if api_sp > 0 else 0.0
                    elif pos and pos <= 3:
                        result_label = "PLACE"
                        pnl = -100.0
                    else:
                        result_label = "LOSS"
                        pnl = -100.0
                else:
                    pos = None
                    api_sp = 0.0
                    result_label = "SCRATCHED"
                    pnl = 0.0

                row = {
                    "race_date": tr.date,
                    "tip_date": tr.date,
                    "track": tr.track,
                    "race_number": tr.race_number,
                    "horse_name": th.horse_name,
                    "tipped_horse_name": th.horse_name,
                    "tip_type": th.tip_type,
                    "tip_rank": th.tip_rank,
                    "tipped_odds": th.tipped_odds,
                    "tipped_win_pct": th.tipped_win_pct,
                    "tipped_edge_pct": th.tipped_edge_pct,
                    "confidence": th.confidence,
                    "actual_position": pos,
                    "field_size": field_size,
                    "api_sp": api_sp,
                    "tipped_horse_sp": api_sp,
                    "edge_pct": th.tipped_edge_pct,
                    "result": result_label,
                    "profit_loss": round(pnl, 2),
                    "distance": tr.distance,
                    "race_class": tr.race_class,
                    "going": tr.going,
                }
                results.append(row)

                cur.execute("""
                    INSERT INTO stride_tip_results
                        (race_date, tip_date, track, race_number, horse_name, tipped_horse_name, tip_type,
                         tip_rank, tipped_odds, tipped_win_pct, tipped_edge_pct,
                         confidence, actual_position, field_size, api_sp, tipped_horse_sp, edge_pct,
                         result, profit_loss, distance, race_class, going)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (race_date, track, race_number, horse_name, tip_type)
                    DO UPDATE SET
                        tip_date = EXCLUDED.tip_date,
                        tipped_horse_name = EXCLUDED.tipped_horse_name,
                        actual_position = EXCLUDED.actual_position,
                        field_size = EXCLUDED.field_size,
                        api_sp = EXCLUDED.api_sp,
                        tipped_horse_sp = EXCLUDED.tipped_horse_sp,
                        edge_pct = EXCLUDED.edge_pct,
                        result = EXCLUDED.result,
                        profit_loss = EXCLUDED.profit_loss,
                        collected_at = NOW()
                """, (
                    row["race_date"], row["tip_date"], row["track"], row["race_number"],
                    row["horse_name"], row["tipped_horse_name"], row["tip_type"], row["tip_rank"],
                    row["tipped_odds"], row["tipped_win_pct"], row["tipped_edge_pct"],
                    row["confidence"], row["actual_position"], row["field_size"],
                    row["api_sp"], row["tipped_horse_sp"], row["edge_pct"],
                    row["result"], row["profit_loss"],
                    row["distance"], row["race_class"], row["going"],
                ))

    by_date = defaultdict(list)
    for r in results:
        by_date[r["race_date"]].append(r)

    for date_str, day_results in by_date.items():
        out_path = RACECARDS_DIR / f"tip_results_{date_str}.json"
        payload = {
            "date": date_str,
            "collected_at": datetime.now().isoformat(),
            "results": day_results,
        }
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"  [ACCURACY] Wrote {out_path.name} ({len(day_results)} tips)", file=sys.stderr)

    bet_results = [r for r in results if r["tip_type"] == "BET"]
    if bet_results:
        wins = sum(1 for r in bet_results if r["result"] == "WIN")
        places = sum(1 for r in bet_results if r["result"] in ("WIN", "PLACE"))
        total = len(bet_results)
        print(f"  [ACCURACY] BET tips: {wins}/{total} winners ({wins/total*100:.0f}%), "
              f"{places}/{total} placed ({places/total*100:.0f}%)", file=sys.stderr)

    return results



def trigger_franking_updates(conn, dates: List[str]) -> Dict:
    """Re-compute franking for horses whose opponent results are now known."""
    summary = {"horses_recomputed": 0, "error": None}

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT horse_id
                FROM race_results_history
                WHERE race_date = ANY(%s) AND horse_id IS NOT NULL
            """, (dates,))
            raced_ids = {row[0] for row in cur.fetchall()}

        if not raced_ids:
            print("  [FRANKING] No horse_ids found for these dates", file=sys.stderr)
            return summary

        # Find horses in franking_scores with low confidence (PENDING)
        # that may have had opponents from today's results
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT horse_id
                FROM franking_scores
                WHERE franking_confidence < 0.15
            """)
            pending_ids = {row[0] for row in cur.fetchall()}

        if not pending_ids:
            print("  [FRANKING] No PENDING horses to update", file=sys.stderr)
            return summary

        from form_franking import compute_all_franking_scores
        target_ids = list(pending_ids)[:500]
        print(f"  [FRANKING] Re-computing franking for {len(target_ids)} PENDING horses...", file=sys.stderr)

        results = compute_all_franking_scores(target_horse_ids=target_ids)
        summary["horses_recomputed"] = len(results) if results else 0
        print(f"  [FRANKING] Re-computed {summary['horses_recomputed']} horses", file=sys.stderr)

    except CONN_ERRORS:
        raise
    except Exception as e:
        summary["error"] = str(e)
        print(f"  [FRANKING] Error: {e}", file=sys.stderr)

    return summary



def build_summary(conn, tip_results: List[Dict], dates: List[str]) -> Dict:
    """Aggregate tip accuracy stats and print formatted summary."""
    bet_tips = [r for r in tip_results if r["tip_type"] == "BET" and r["result"] != "SCRATCHED"]
    cov_tips = [r for r in tip_results if r["tip_type"] == "COVERAGE" and r["result"] != "SCRATCHED"]
    sp_health = compute_sp_health(conn, dates)

    summary = {
        "dates": dates,
        "total_bet_tips": len(bet_tips),
        "total_coverage_tips": len(cov_tips),
        "sp_health": sp_health,
    }

    if not bet_tips:
        print("\n  No BET tips to summarize.", file=sys.stderr)
        return summary

    wins = [r for r in bet_tips if r["result"] == "WIN"]
    places = [r for r in bet_tips if r["result"] in ("WIN", "PLACE")]
    total = len(bet_tips)

    summary["wins"] = len(wins)
    summary["places"] = len(places)
    summary["win_strike_rate"] = round(len(wins) / total * 100, 1) if total else 0
    summary["place_strike_rate"] = round(len(places) / total * 100, 1) if total else 0
    summary["avg_sp_winners"] = round(sum(w["api_sp"] for w in wins) / len(wins), 2) if wins else 0
    summary["total_profit_loss"] = round(sum(r["profit_loss"] for r in bet_tips), 2)
    summary["roi"] = round(summary["total_profit_loss"] / (total * 100) * 100, 1) if total else 0

    by_track = defaultdict(lambda: {"tips": 0, "wins": 0, "pnl": 0.0})
    for r in bet_tips:
        t = r["track"]
        by_track[t]["tips"] += 1
        if r["result"] == "WIN":
            by_track[t]["wins"] += 1
        by_track[t]["pnl"] += r["profit_loss"]

    summary["by_track"] = {
        t: {
            "tips": d["tips"],
            "wins": d["wins"],
            "strike_rate": round(d["wins"] / d["tips"] * 100, 1) if d["tips"] else 0,
            "pnl": round(d["pnl"], 2),
        }
        for t, d in sorted(by_track.items())
    }

    by_going = defaultdict(lambda: {"tips": 0, "wins": 0})
    for r in bet_tips:
        g = r["going"] or "Unknown"
        by_going[g]["tips"] += 1
        if r["result"] == "WIN":
            by_going[g]["wins"] += 1

    summary["by_going"] = {
        g: {
            "tips": d["tips"],
            "wins": d["wins"],
            "strike_rate": round(d["wins"] / d["tips"] * 100, 1) if d["tips"] else 0,
        }
        for g, d in sorted(by_going.items())
    }

    print("\n" + "=" * 60, file=sys.stderr)
    print("  STRIDE TIP RESULTS SUMMARY", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Dates: {', '.join(dates)}", file=sys.stderr)
    print(f"  Total BET tips: {total}", file=sys.stderr)
    print(f"  Winners: {len(wins)} ({summary['win_strike_rate']}%)", file=sys.stderr)
    print(f"  Placed:  {len(places)} ({summary['place_strike_rate']}%)", file=sys.stderr)
    if wins:
        print(f"  Avg SP winners: ${summary['avg_sp_winners']:.2f}", file=sys.stderr)
    print(f"  P/L: ${summary['total_profit_loss']:+.2f}  (ROI: {summary['roi']:+.1f}%)", file=sys.stderr)
    print("", file=sys.stderr)

    if summary["by_track"]:
        print("  By Track:", file=sys.stderr)
        for t, d in summary["by_track"].items():
            print(f"    {t:25s}  {d['wins']}/{d['tips']} ({d['strike_rate']}%)  P/L: ${d['pnl']:+.2f}", file=sys.stderr)
        print("", file=sys.stderr)

    if summary["by_going"]:
        print("  By Going:", file=sys.stderr)
        for g, d in summary["by_going"].items():
            print(f"    {g:15s}  {d['wins']}/{d['tips']} ({d['strike_rate']}%)", file=sys.stderr)

    print("", file=sys.stderr)
    print("  SP Health:", file=sys.stderr)
    print(
        f"    Status: {sp_health['status']} | Coverage: {sp_health['coverage_pct']}% "
        f"| Avg SP: ${sp_health['avg_sp']:.2f}" if sp_health["avg_sp"] is not None
        else f"    Status: {sp_health['status']} | Coverage: {sp_health['coverage_pct']}% | Avg SP: n/a",
        file=sys.stderr,
    )
    print(
        f"    Suspect SP==race_number rows: {sp_health['suspect_sp_equals_race_number']}",
        file=sys.stderr,
    )
    for note in sp_health.get("notes", []):
        print(f"    Note: {note}", file=sys.stderr)

    print("=" * 60, file=sys.stderr)
    return summary



def collect_results(dates: List[str], parallel: bool = False,
                    skip_sectionals: bool = False,
                    skip_franking: bool = False,
                    dry_run: bool = False) -> Dict:
    """Main entry point. Runs all 7 steps."""
    print(f"\nSTRIDE Post-Race Results Collector", file=sys.stderr)
    print(f"Dates: {', '.join(dates)}", file=sys.stderr)
    print(f"Mode: {'parallel' if parallel else 'sequential'}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    t_start = time.time()

    print("\n[Step 1] Scanning tips files...", file=sys.stderr)
    tipped_races = find_tipped_races(dates)
    if not tipped_races:
        print("  No tipped races found for these dates.", file=sys.stderr)
        return {"status": "no_tips", "dates": dates}

    with _db_phase() as conn:
        ensure_table(conn)

        print("\n[Step 2] Checking existing results in database...", file=sys.stderr)
        needed, existing = check_existing_results(conn, tipped_races)

    if dry_run:
        print(f"\n[DRY RUN] Would fetch results for {len(needed)} races:", file=sys.stderr)
        for tr in needed:
            tips_str = ", ".join(h.horse_name for h in tr.horses)
            print(f"  {tr.track} R{tr.race_number} ({tr.date}): {tips_str}", file=sys.stderr)
        print(f"\n[DRY RUN] Already have results for {len(existing)} races", file=sys.stderr)
        return {"status": "dry_run", "needed": len(needed), "existing": len(existing)}

    # Steps 3-4 are minutes of provider API and subprocess time; no
    # connection may be open across them.
    fetch_summary = {"dates_fetched": 0, "rows_imported": 0}
    if needed:
        print(f"\n[Step 3] Fetching results for {len(needed)} races...", file=sys.stderr)
        fetch_summary = fetch_and_store_results(needed, parallel=parallel)
    else:
        print(f"\n[Step 3] All results already in database", file=sys.stderr)

    sectional_summary = {"qld": 0, "vic_sa": 0, "nsw": 0}
    if not skip_sectionals and needed:
        fetch_dates = sorted({tr.date for tr in needed})
        print(f"\n[Step 4] Fetching sectional times...", file=sys.stderr)
        sectional_summary = fetch_sectional_times(fetch_dates, parallel=parallel)
    elif skip_sectionals:
        print(f"\n[Step 4] Skipping sectionals (--skip-sectionals)", file=sys.stderr)
    else:
        print(f"\n[Step 4] No new races to fetch sectionals for", file=sys.stderr)

    with _db_phase() as conn:
        print(f"\n[Step 5] Scoring tip accuracy...", file=sys.stderr)
        tip_results = score_tip_accuracy(conn, tipped_races)

        print(f"\n[Step 5b] Matching shadow tracker results...", file=sys.stderr)
        try:
            from shadow_pl_tracker import cmd_results as shadow_cmd_results
            shadow_total = 0
            for date in dates:
                shadow_total += (shadow_cmd_results(date) or 0)
            print(f"  Shadow tracker: {shadow_total} rows matched", file=sys.stderr)
        except CONN_ERRORS:
            raise
        except Exception as e:
            print(f"  Shadow matching failed (non-fatal): {e}", file=sys.stderr)

        print(f"\n[Step 5c] Settling selection ledger rows...", file=sys.stderr)
        try:
            from selection_ledger import settle_pending_rows
            ledger_settled = 0
            for date in dates:
                out = settle_pending_rows(conn, date) or {}
                ledger_settled += out.get("settled") or 0
                if out.get("reason") and "net_settlement" in str(out["reason"]):
                    # Missing migration: the loud warning is already emitted —
                    # once per run is enough, and every date would fail the same way.
                    break
            print(f"  Selection ledger: {ledger_settled} rows settled", file=sys.stderr)
        except CONN_ERRORS:
            raise
        except Exception as e:
            print(f"  Ledger settlement failed (non-fatal): {e}", file=sys.stderr)

    franking_summary = {"horses_recomputed": 0}
    if not skip_franking and needed:
        print(f"\n[Step 6] Triggering franking updates...", file=sys.stderr)
        # Own phase: compute_all_franking_scores is minutes of work and the
        # scoring connection would sit idle under it.
        with _db_phase() as conn:
            franking_summary = trigger_franking_updates(conn, dates)
    elif skip_franking:
        print(f"\n[Step 6] Skipping franking (--skip-franking)", file=sys.stderr)
    else:
        print(f"\n[Step 6] No new results to trigger franking for", file=sys.stderr)

    print(f"\n[Step 7] Building summary...", file=sys.stderr)
    with _db_phase() as conn:
        summary = build_summary(conn, tip_results, dates)

    elapsed = time.time() - t_start
    print(f"\nCompleted in {elapsed:.1f}s", file=sys.stderr)

    return {
        "status": "completed",
        "dates": dates,
        "elapsed_seconds": round(elapsed, 1),
        "fetch": fetch_summary,
        "sectionals": sectional_summary,
        "franking": franking_summary,
        "accuracy": summary,
    }



def expand_date_range(start: str, end: str) -> List[str]:
    """Expand two dates into a list of all dates between (inclusive)."""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    if e < s:
        s, e = e, s
    dates = []
    current = s
    while current <= e:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def main():
    parser = argparse.ArgumentParser(
        description="STRIDE Post-Race Results Collector",
        epilog="Examples:\n"
               "  python stride_results_collector.py 2026-03-28\n"
               "  python stride_results_collector.py 2026-03-25 2026-03-28\n"
               "  python stride_results_collector.py 2026-03-28 --parallel\n"
               "  python stride_results_collector.py 2026-03-28 --dry-run\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("dates", nargs="+", help="YYYY-MM-DD date(s), or two dates for a range")
    parser.add_argument("--parallel", action="store_true", help="Concurrent fetching")
    parser.add_argument("--skip-sectionals", action="store_true", help="Skip sectional time collection")
    parser.add_argument("--skip-franking", action="store_true", help="Skip franking re-computation")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be collected")
    args = parser.parse_args()

    if len(args.dates) == 2:
        try:
            datetime.strptime(args.dates[0], "%Y-%m-%d")
            datetime.strptime(args.dates[1], "%Y-%m-%d")
            dates = expand_date_range(args.dates[0], args.dates[1])
        except ValueError:
            dates = args.dates
    else:
        dates = args.dates

    result = collect_results(
        dates,
        parallel=args.parallel,
        skip_sectionals=args.skip_sectionals,
        skip_franking=args.skip_franking,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
