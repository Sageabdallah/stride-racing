#!/usr/bin/env python3
"""
Tracks level-stakes P/L for ALL convergence tiers (LOCK, CONFIRMED, FLAG, CROWD_OVERRIDE, SKIP, etc.) — not just the BET picks. This answers:
  - Does model confirmation add lift over crowd-only?
  - Do FLAG picks (model says yes, crowd says no) have latent edge?
  - Should CROWD_OVERRIDE picks be promoted to live betting?
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from glob import glob
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import roi_stats  # noqa: E402  (must follow the sys.path setup)

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
    _load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    return conn



MIGRATION_SQL = """
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS convergence_tier TEXT;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS convergence_score REAL;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS selection_score REAL;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS pillars_strong INTEGER;
ALTER TABLE stride_tip_results ADD COLUMN IF NOT EXISTS is_shadow BOOLEAN DEFAULT FALSE;

-- Relax the result CHECK constraint to allow PENDING for shadow rows
ALTER TABLE stride_tip_results DROP CONSTRAINT IF EXISTS stride_tip_results_result_check;
ALTER TABLE stride_tip_results
    ADD CONSTRAINT stride_tip_results_result_check
    CHECK (result IN ('WIN', 'PLACE', 'LOSS', 'SCRATCHED', 'PENDING'));

-- Index for shadow queries
CREATE INDEX IF NOT EXISTS idx_shadow_tier ON stride_tip_results(convergence_tier)
    WHERE convergence_tier IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_shadow_pending ON stride_tip_results(result)
    WHERE result = 'PENDING';
"""


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(MIGRATION_SQL)
    print("  [SHADOW] Schema migration applied", file=sys.stderr)



def _normalize_name(name):
    """Lowercase, strip punctuation for matching."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _find_tips_file(race_date):
    """Find the canonical tips file for a date."""
    canonical = RACECARDS_DIR / f"tips_{race_date}.json"
    if canonical.exists():
        return canonical
    # Fall back to any tips file for this date (e.g. audit runs)
    matches = sorted(RACECARDS_DIR.glob(f"tips_{race_date}*.json"))
    return matches[-1] if matches else None


def _load_tips(race_date):
    """Load tips JSON for a date, return races list or empty."""
    path = _find_tips_file(race_date)
    if not path:
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("races", [])


def _infer_tier_from_json(race, horse_name):
    """Infer convergence tier from tips JSON fields when DB tier unavailable."""
    norm = _normalize_name(horse_name)

    for pick in race.get("top_picks", []):
        if _normalize_name(pick.get("horse")) == norm:
            origin = pick.get("selection_origin", "")
            should_bet = pick.get("should_bet", False)
            if should_bet:
                return "CONFIRMED"
            if "crowd_gated" in origin or "flag" in origin.lower():
                return "FLAG"
            if "crowd" in origin.lower():
                return "CROWD_ONLY"
            return "MODEL_ONLY"

    for runner in race.get("full_field", []):
        if _normalize_name(runner.get("horse")) == norm:
            crowd_class = runner.get("crowd_classification", "")
            if crowd_class:
                return crowd_class
            return "SKIP"

    return "SKIP"



BET_TIERS = {"CONFIRMED", "CROWD_ONLY", "LOCK"}

def cmd_record(race_date, conn=None):
    close_conn = conn is None
    if conn is None:
        conn = _get_connection()
        ensure_schema(conn)
    cur = conn.cursor()

    cur.execute("""
        SELECT track, race_number, horse_name, convergence_tier,
               final_convergence_score, stride_score, pillars_strong, field_size
        FROM convergence_output
        WHERE race_date = %s
    """, (race_date,))
    db_rows = cur.fetchall()

    races = _load_tips(race_date)
    tips_lookup = {}
    for race in races:
        track = race.get("track", "")
        rnum = race.get("race_number", 0)
        for runner in race.get("full_field", []):
            key = (_normalize_name(track), rnum, _normalize_name(runner.get("horse", "")))
            tips_lookup[key] = runner
        for pick in race.get("top_picks", []):
            key = (_normalize_name(track), rnum, _normalize_name(pick.get("horse", "")))
            tips_lookup[key] = {**tips_lookup.get(key, {}), **pick}

    cur.execute("""
        SELECT track, race_number, horse_name, tip_type
        FROM stride_tip_results WHERE race_date = %s
    """, (race_date,))
    existing = {(r[0], r[1], r[2], r[3]) for r in cur.fetchall()}

    to_insert = []

    if db_rows:
        for track, rnum, horse, tier, conv_score, stride_score, pillars, fsize in db_rows:
            if (track, rnum, horse, tier) in existing:
                continue
            key = (_normalize_name(track), rnum, _normalize_name(horse))
            tip = tips_lookup.get(key, {})
            to_insert.append((
                race_date, race_date, track, rnum, horse, horse, tier,
                float(tip.get("odds", 0) or 0),
                float(tip.get("win_pct", 0) or 0),
                float(tip.get("edge_pct", 0) or 0),
                tip.get("confidence", ""), fsize, "PENDING",
                tier, conv_score, float(tip.get("selection_score", 0) or 0),
                pillars, tier not in BET_TIERS,
            ))
    else:
        print(f"  [SHADOW] No convergence_output for {race_date}, using JSON fallback",
              file=sys.stderr)
        for race in races:
            track = race.get("track", "")
            rnum = race.get("race_number", 0)
            fsize = race.get("field_size", 0)
            runners = race.get("full_field", [])
            if not runners:
                runners = race.get("top_picks", [])
            for runner in runners:
                horse = runner.get("horse", "")
                if not horse:
                    continue
                tier = _infer_tier_from_json(race, horse)
                if (track, rnum, horse, tier) in existing:
                    continue
                to_insert.append((
                    race_date, race_date, track, rnum, horse, horse, tier,
                    float(runner.get("odds", 0) or 0),
                    float(runner.get("win_pct", 0) or 0),
                    float(runner.get("edge_pct", 0) or 0),
                    runner.get("confidence", ""), fsize, "PENDING",
                    tier, None, float(runner.get("selection_score", 0) or 0),
                    None, tier not in BET_TIERS,
                ))

    inserted = len(to_insert)
    skipped = len(existing)
    if to_insert:
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO stride_tip_results
                (race_date, tip_date, track, race_number, horse_name,
                 tipped_horse_name, tip_type, tipped_odds, tipped_win_pct,
                 tipped_edge_pct, confidence, field_size, result,
                 convergence_tier, convergence_score, selection_score,
                 pillars_strong, is_shadow)
            VALUES %s
        """, to_insert)

    cur.close()
    if close_conn:
        conn.close()
    print(f"  [SHADOW] Recorded {inserted} rows for {race_date} ({skipped} duplicates skipped)",
          file=sys.stderr)
    return inserted



def cmd_results(race_date):
    conn = _get_connection()
    ensure_schema(conn)
    cur = conn.cursor()

    cur.execute("""
        SELECT track, race_number, horse_name, tipped_odds, tip_type
        FROM stride_tip_results
        WHERE race_date = %s AND result = 'PENDING'
    """, (race_date,))
    pending = cur.fetchall()

    if not pending:
        print(f"  [SHADOW] No PENDING rows for {race_date}", file=sys.stderr)
        cur.close()
        conn.close()
        return 0

    cur.execute("""
        SELECT track, race_number, horse_name, actual_position, starting_price, won, placed
        FROM prediction_audit
        WHERE race_date = %s AND result_status = 'resulted'
    """, (race_date,))
    results_rows = cur.fetchall()

    results_map = {}
    if results_rows:
        for track, rnum, horse, pos, sp, won, placed in results_rows:
            key = (_normalize_name(track), rnum, _normalize_name(horse))
            results_map[key] = {"position": pos, "sp": sp, "won": won, "placed": placed}

    # Always supplement with race_results_history for horses not in prediction_audit
    # (prediction_audit only contains tipped horses, not the full field)
    cur.execute("""
        SELECT track, race_number, horse_name, position, sp_odds
        FROM race_results_history
        WHERE race_date = %s
    """, (race_date,))
    for track, rnum, horse, pos, sp in cur.fetchall():
        key = (_normalize_name(track), rnum, _normalize_name(horse))
        if key not in results_map:
            results_map[key] = {"position": pos, "sp": sp}

    if not results_map:
        print(f"  [SHADOW] 0 results matched — has results_collector run for {race_date}?",
              file=sys.stderr)
        cur.close()
        conn.close()
        return 0

    matched = 0
    for track, rnum, horse, tipped_odds, tip_type in pending:
        key = (_normalize_name(track), rnum, _normalize_name(horse))
        res = results_map.get(key)
        if not res:
            continue

        pos = res.get("position")
        sp = res.get("sp") or tipped_odds or 0
        won = res.get("won") or (pos == 1)
        placed = res.get("placed") or (pos is not None and pos <= 3)

        if pos is None:
            result = "SCRATCHED"
            pl = 0
        elif won:
            result = "WIN"
            pl = round(float(sp) - 1, 2) if sp else 0
        elif placed:
            result = "PLACE"
            pl = -1
        else:
            result = "LOSS"
            pl = -1

        cur.execute("""
            UPDATE stride_tip_results
            SET result = %s, actual_position = %s, profit_loss = %s,
                api_sp = %s, tipped_horse_sp = %s, collected_at = NOW()
            WHERE race_date = %s AND track = %s AND race_number = %s
              AND horse_name = %s AND tip_type = %s AND result = 'PENDING'
        """, (result, pos, pl, sp, sp, race_date, track, rnum, horse, tip_type))
        matched += 1

    cur.close()
    conn.close()
    print(f"  [SHADOW] Updated {matched}/{len(pending)} rows for {race_date}", file=sys.stderr)
    return matched



MIN_BETS_REPORTABLE = roi_stats.MIN_BETS_REPORTABLE  # single source (roi_stats, task 02)

def cmd_report():
    conn = _get_connection()
    ensure_schema(conn)
    cur = conn.cursor()

    cur.execute("""
        SELECT convergence_tier,
               COUNT(*) FILTER (WHERE result != 'PENDING' AND result != 'SCRATCHED') AS bets,
               COUNT(*) FILTER (WHERE result = 'WIN') AS wins,
               COALESCE(SUM(profit_loss) FILTER (WHERE result != 'PENDING' AND result != 'SCRATCHED'), 0) AS total_pl,
               COALESCE(AVG(tipped_edge_pct) FILTER (WHERE result != 'PENDING'), 0) AS avg_edge,
               COALESCE(AVG(tipped_odds) FILTER (WHERE result != 'PENDING'), 0) AS avg_odds,
               COUNT(*) FILTER (WHERE result = 'PENDING') AS pending
        FROM stride_tip_results
        WHERE convergence_tier IS NOT NULL
        GROUP BY convergence_tier
        ORDER BY bets DESC
    """)
    rows = cur.fetchall()

    if not rows:
        print("  [SHADOW] No tier data available yet. Run 'record' after pipeline runs.",
              file=sys.stderr)
        cur.close()
        conn.close()
        return

    print()
    print("=" * 95)
    print("  SHADOW P/L TRACKER — ROI by Convergence Tier (level stakes)")
    print("=" * 95)
    print(f"  {'Tier':<20s} {'Bets':>5s} {'Wins':>5s} {'Win%':>6s} {'ROI%':>7s} "
          f"{'AvgEdge':>8s} {'AvgOdds':>8s} {'Pending':>8s}  Status")
    print("-" * 95)

    for tier, bets, wins, total_pl, avg_edge, avg_odds, pending in rows:
        win_pct = (wins / bets * 100) if bets > 0 else 0
        roi = (float(total_pl) / bets * 100) if bets > 0 else 0
        status = "✓ reportable" if bets >= MIN_BETS_REPORTABLE else f"⚠ <{MIN_BETS_REPORTABLE} bets"

        print(f"  {tier or 'UNKNOWN':<20s} {bets:>5d} {wins:>5d} {win_pct:>5.1f}% "
              f"{roi:>+6.1f}% {avg_edge:>+7.1f}% {avg_odds:>7.1f} {pending:>8d}  {status}")

    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE result != 'PENDING' AND result != 'SCRATCHED'),
               COUNT(*) FILTER (WHERE result = 'WIN'),
               COALESCE(SUM(profit_loss) FILTER (WHERE result != 'PENDING' AND result != 'SCRATCHED'), 0),
               COUNT(*) FILTER (WHERE result = 'PENDING'),
               MIN(race_date), MAX(race_date)
        FROM stride_tip_results
        WHERE convergence_tier IS NOT NULL
    """)
    total_bets, total_wins, total_pl, total_pending, min_date, max_date = cur.fetchone()
    total_roi = (float(total_pl) / total_bets * 100) if total_bets > 0 else 0

    print("-" * 95)
    print(f"  {'TOTAL':<20s} {total_bets:>5d} {total_wins:>5d} "
          f"{(total_wins/total_bets*100 if total_bets else 0):>5.1f}% "
          f"{total_roi:>+6.1f}%{' ':>18s}{total_pending:>8d}")
    print(f"\n  Date range: {min_date} to {max_date}")
    print()

    cur.close()
    conn.close()



def cmd_backfill():
    tips_files = sorted(RACECARDS_DIR.glob("tips_*.json"))
    # Filter to canonical files (no suffix like _audit-fix1)
    canonical = []
    for f in tips_files:
        name = f.stem
        if re.match(r"^tips_\d{4}-\d{2}-\d{2}$", name):
            canonical.append(f)

    if not canonical:
        print("  [SHADOW] No canonical tips files found in racecards/", file=sys.stderr)
        return

    print(f"  [SHADOW] Backfilling from {len(canonical)} tips files", file=sys.stderr)
    conn = _get_connection()
    ensure_schema(conn)
    total = 0
    for f in canonical:
        date_str = f.stem.replace("tips_", "")
        n = cmd_record(date_str, conn=conn)
        total += n

    conn.close()
    print(f"  [SHADOW] Backfill complete: {total} total rows inserted", file=sys.stderr)



def main():
    parser = argparse.ArgumentParser(description="Shadow P/L tracker by convergence tier")
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser("record", help="Log tier assignments after pipeline run")
    p_record.add_argument("race_date", help="Race date YYYY-MM-DD")

    p_results = sub.add_parser("results", help="Match outcomes for a date")
    p_results.add_argument("race_date", help="Race date YYYY-MM-DD")

    sub.add_parser("report", help="Print tier ROI summary")
    sub.add_parser("backfill", help="Backfill from all existing tips files")

    args = parser.parse_args()

    if args.command == "record":
        cmd_record(args.race_date)
    elif args.command == "results":
        cmd_results(args.race_date)
    elif args.command == "report":
        cmd_report()
    elif args.command == "backfill":
        cmd_backfill()


if __name__ == "__main__":
    main()
