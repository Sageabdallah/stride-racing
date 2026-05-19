#!/usr/bin/env python3
"""Form Franking & Collateral Form Analysis Engine — evaluates the quality of a horse's form by analysing what opponents did in subsequent races."""

import os
import sys
import json
import math
import time
import logging
import argparse
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import psycopg2
from psycopg2.extras import execute_values, Json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("form_franking")

DATABASE_URL = os.environ.get("DATABASE_URL")

CLASS_MULTIPLIERS = {
    10: 3.0,
    9: 2.5,
    8: 2.0,
    7: 1.7,
    6: 1.4,
    5: 1.2,
    4: 1.0,
    3: 0.9,
    2: 0.8,
    1: 0.7,
}

# Class-weighted franking: weight franking score by the class of the reference race.
# A franking chain through Group 1 races is worth 2x more than one through BM72.
CLASS_FRANKING_WEIGHTS = {
    'Group 1':    2.0,
    'Group 2':    1.8,
    'Group 3':    1.6,
    'Listed':     1.4,
    'BM90+':      1.3,
    'BM84':       1.2,
    'BM78':       1.1,
    'BM72':       1.0,
    'BM64':       0.9,
    'BM58':       0.8,
    'Maiden':     0.7,
    'Provincial': 0.85,
    'Country':    0.75,
}


def _get_class_franking_weight(race_class_text):
    """
    Map a race_class text string to a CLASS_FRANKING_WEIGHTS value.

    Handles the varied formats in race_results_history:
      'Group 1', 'Listed', 'BM78', 'MDN-SW', 'CTRY BM58', 'HIGHWAY-C3',
      'F&M BM78', '3Y MDN', 'CL1', 'OPEN-BT', etc.

    Returns (weight: float, canonical_class: str).
    """
    if not race_class_text:
        return 1.0, 'Unknown'

    rc = race_class_text.upper().strip()

    if 'GROUP 1' in rc or 'GRP 1' in rc or 'G1 ' in rc or rc == 'G1':
        return 2.0, 'Group 1'
    if 'GROUP 2' in rc or 'GRP 2' in rc or 'G2 ' in rc or rc == 'G2':
        return 1.8, 'Group 2'
    if 'GROUP 3' in rc or 'GRP 3' in rc or 'G3 ' in rc or rc == 'G3':
        return 1.6, 'Group 3'
    if 'LISTED' in rc or rc.startswith('LR'):
        return 1.4, 'Listed'

    # Country prefix — check before BM to catch "CTRY BM58"
    is_country = 'CTRY' in rc or 'COUNTRY' in rc
    is_provincial = 'HIGHWAY' in rc or 'MIDWAY' in rc or 'PROVINCIAL' in rc

    import re
    bm_match = re.search(r'BM\s*(\d+)', rc)
    if bm_match:
        bm_num = int(bm_match.group(1))
        if is_country:
            return 0.75, 'Country'
        if is_provincial:
            return 0.85, 'Provincial'
        if bm_num >= 90:
            return 1.3, 'BM90+'
        if bm_num >= 84:
            return 1.2, 'BM84'
        if bm_num >= 78:
            return 1.1, 'BM78'
        if bm_num >= 72:
            return 1.0, 'BM72'
        if bm_num >= 64:
            return 0.9, 'BM64'
        return 0.8, 'BM58'

    # Class races (CL1-CL6) — map to approximate BM equivalents
    cl_match = re.search(r'CL\s*(\d+)', rc)
    if cl_match:
        cl_num = int(cl_match.group(1))
        if cl_num >= 5:
            return 1.1, 'BM78'
        if cl_num >= 3:
            return 1.0, 'BM72'
        if cl_num >= 2:
            return 0.9, 'BM64'
        return 0.8, 'BM58'

    if 'MDN' in rc or 'MAIDEN' in rc:
        if is_country:
            return 0.7, 'Country'
        return 0.7, 'Maiden'

    if is_country:
        return 0.75, 'Country'
    if is_provincial:
        return 0.85, 'Provincial'

    # Open / HCP / other higher-class races
    if 'OPEN' in rc:
        return 1.1, 'BM78'
    if 'HCP' in rc:
        return 0.9, 'BM64'

    # 0-XX format (e.g., "0-58", "0 - 56") — treat like BM equivalent
    zero_match = re.search(r'0\s*-\s*(\d+)', rc)
    if zero_match:
        rating = int(zero_match.group(1))
        if rating >= 78:
            return 1.1, 'BM78'
        if rating >= 72:
            return 1.0, 'BM72'
        if rating >= 64:
            return 0.9, 'BM64'
        return 0.8, 'BM58'

    # Trial / Jump Out — low value
    if 'TRL' in rc or 'TRIAL' in rc or 'JUMP' in rc:
        return 0.5, 'Trial'

    return 1.0, 'BM72'

GOING_ADJUSTMENTS = {
    "heavy": 0.85,
    "soft": 0.92,
    "good": 1.0,
    "firm": 1.05,
    "synthetic": 0.95,
}

ELO_INITIAL = 1500
ELO_K_BASE = 32
ELO_HALF_LIFE_DAYS = 90
FRANKING_HALF_LIFE_DAYS = 60
MAX_FRANKING_RAW = 40.0

ELO_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "elo_cache.json")


def _compute_elo_staleness_key(conn=None):
    """Return a hash of (max_race_date, row_count) from race_results_history. If the key matches the cached value, skip recomputation."""
    own_conn = conn is None
    if own_conn:
        conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(race_date)::text, COUNT(*)
        FROM race_results_history
        WHERE position IS NOT NULL
    """)
    row = cur.fetchone()
    cur.close()
    if own_conn:
        conn.close()
    key_str = f"{row[0]}|{row[1]}" if row else "empty"
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


def _load_elo_cache():
    """Load cached ELO ratings if they exist and staleness key matches."""
    if not os.path.exists(ELO_CACHE_PATH):
        return None
    try:
        with open(ELO_CACHE_PATH, "r") as f:
            cache = json.load(f)
        current_key = _compute_elo_staleness_key()
        if cache.get("staleness_key") == current_key:
            logger.info("ELO cache hit (key=%s, %d horses)", current_key, len(cache.get("ratings", {})))
            return cache["ratings"]
        logger.info("ELO cache stale (cached=%s, current=%s)", cache.get("staleness_key"), current_key)
        return None
    except (json.JSONDecodeError, KeyError, OSError) as e:
        logger.warning("ELO cache load failed: %s", e)
        return None


def _save_elo_cache(ratings):
    """Save ELO ratings to disk with current staleness key."""
    try:
        os.makedirs(os.path.dirname(ELO_CACHE_PATH), exist_ok=True)
        key = _compute_elo_staleness_key()
        cache = {
            "staleness_key": key,
            "saved_at": datetime.now().isoformat(),
            "n_horses": len(ratings),
            "ratings": ratings,
        }
        with open(ELO_CACHE_PATH, "w") as f:
            json.dump(cache, f)
        logger.info("ELO cache saved (key=%s, %d horses)", key, len(ratings))
    except OSError as e:
        logger.warning("ELO cache save failed: %s", e)


def _to_native(value):
    """Convert numpy/container values to psycopg2/json-safe native Python types."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_to_native(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: _to_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_native(v) for v in value]
    return value


def _get_connection():
    """Return a new psycopg2 connection using DATABASE_URL."""
    url = DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(url)


def _parse_going(going_str):
    """Normalise a going string to a canonical key."""
    if not going_str:
        return "good"
    g = going_str.lower().strip()
    for key in GOING_ADJUSTMENTS:
        if key in g:
            return key
    return "good"


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _days_between(date_str_a, date_str_b):
    """Return absolute days between two YYYY-MM-DD date strings."""
    try:
        da = datetime.strptime(str(date_str_a)[:10], "%Y-%m-%d")
        db = datetime.strptime(str(date_str_b)[:10], "%Y-%m-%d")
        return abs((da - db).days)
    except (ValueError, TypeError):
        return 9999


def _date_from_str(date_str):
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None



def adjust_margin(raw_margin, distance_m, class_level, going, field_size,
                  weight_kg=None, base_weight=56.0):
    """
    Multi-layer margin normalisation.

    Adjusts a raw margin (in lengths) to account for:
    - Weight carried vs base weight
    - Class level of the race
    - Going (track condition)
    - Field size
    - Distance category

    Returns the adjusted margin as a float.
    """
    raw_margin = _safe_float(raw_margin, 0.0)
    distance_m = _safe_int(distance_m, 1400)
    class_level = _safe_int(class_level, 1)
    field_size = _safe_int(field_size, 10)
    if field_size < 1:
        field_size = 1

    if distance_m < 1200:
        distance_factor = 0.15
        dist_norm = 1.2
    elif distance_m <= 1800:
        distance_factor = 0.12
        dist_norm = 1.0
    else:
        distance_factor = 0.10
        dist_norm = 0.85

    weight_adj = 0.0
    if weight_kg is not None:
        weight_kg = _safe_float(weight_kg, base_weight)
        weight_adj = (weight_kg - base_weight) * distance_factor

    class_mult = CLASS_MULTIPLIERS.get(class_level, 0.7)
    going_key = _parse_going(going)
    going_adj = GOING_ADJUSTMENTS.get(going_key, 1.0)
    field_norm = math.sqrt(field_size / 10.0) if field_size > 0 else 1.0
    if field_norm < 0.3:
        field_norm = 0.3

    adjusted = raw_margin * class_mult * going_adj * dist_norm / field_norm + weight_adj
    return round(adjusted, 4)



def compute_global_elo(iterations=20, force_recompute=False):
    """
    Compute global ELO ratings for every horse using iterative pairwise updates.

    Loads all race results, groups by race, and for each iteration processes races chronologically updating ELO ratings based on adjusted margin differences.

    Uses a disk cache keyed on (max_race_date, row_count) to skip recomputation when no new results have been added. Pass force_recompute=True or use --refresh-elo on the CLI to bypass the cache.

    Returns dict {horse_id: normalised_elo (0-100)}.
    """
    if not force_recompute:
        cached = _load_elo_cache()
        if cached is not None:
            return cached

    logger.info("Computing global ELO ratings (%d iterations)...", iterations)
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT horse_id, race_id, race_date, position, margin_lengths,
               distance_m, class_level, going, field_size, weight_kg
        FROM race_results_history
        WHERE position IS NOT NULL
        ORDER BY race_date ASC, race_id ASC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        logger.warning("No race results found for ELO computation")
        return {}

    races = defaultdict(list)
    for r in rows:
        races[r[1]].append({
            "horse_id": r[0],
            "race_id": r[1],
            "race_date": str(r[2])[:10] if r[2] else "2020-01-01",
            "position": _safe_int(r[3]),
            "margin_lengths": _safe_float(r[4]),
            "distance_m": _safe_int(r[5], 1400),
            "class_level": _safe_int(r[6], 1),
            "going": r[7],
            "field_size": _safe_int(r[8], 10),
            "weight_kg": r[9],
        })

    race_ids_sorted = sorted(races.keys(),
                             key=lambda rid: races[rid][0]["race_date"])

    today_str = datetime.now().strftime("%Y-%m-%d")
    elo = defaultdict(lambda: ELO_INITIAL)

    for iteration in range(iterations):
        for rid in race_ids_sorted:
            runners = races[rid]
            if len(runners) < 2:
                continue

            race_date = runners[0]["race_date"]
            class_level = runners[0]["class_level"]
            days_since = _days_between(race_date, today_str)
            recency_weight = math.exp(-0.693 * days_since / ELO_HALF_LIFE_DAYS)
            k_factor = ELO_K_BASE * (class_level / 10.0) * recency_weight

            adj_margins = {}
            for rn in runners:
                adj_margins[rn["horse_id"]] = adjust_margin(
                    rn["margin_lengths"], rn["distance_m"], rn["class_level"],
                    rn["going"], rn["field_size"], rn["weight_kg"]
                )

            n = len(runners)
            for i in range(n):
                for j in range(i + 1, n):
                    h_a = runners[i]["horse_id"]
                    h_b = runners[j]["horse_id"]

                    elo_diff = elo[h_a] - elo[h_b]
                    expected_margin = elo_diff / 100.0

                    actual_margin = adj_margins[h_b] - adj_margins[h_a]
                    if runners[i]["position"] < runners[j]["position"]:
                        actual_margin = abs(actual_margin)
                    elif runners[i]["position"] > runners[j]["position"]:
                        actual_margin = -abs(actual_margin)
                    else:
                        actual_margin = 0.0

                    delta = k_factor * (actual_margin - expected_margin) / max(n, 2)
                    elo[h_a] += delta
                    elo[h_b] -= delta

        if iteration % 5 == 0:
            logger.debug("ELO iteration %d/%d complete", iteration + 1, iterations)

    if not elo:
        return {}

    values = np.array(list(elo.values()))
    elo_min, elo_max = values.min(), values.max()
    spread = elo_max - elo_min if elo_max > elo_min else 1.0

    normalised = {}
    for horse_id, rating in elo.items():
        normalised[horse_id] = round(((rating - elo_min) / spread) * 100.0, 2)

    logger.info("ELO computation complete: %d horses rated", len(normalised))
    _save_elo_cache(normalised)
    return normalised



def compute_direct_franking(horse_id, lookback_races=10, forward_window_days=90,
                            conn=None):
    """
    Compute the direct franking score for a horse.

    For each of the horse's last N races, evaluates how opponents performed in subsequent races. Returns a weighted franking score.
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT race_id, race_date, opponents_json, position, margin_lengths,
               class_level
        FROM race_results_history
        WHERE horse_id = %s AND position IS NOT NULL
        ORDER BY race_date DESC
        LIMIT %s
    """, (horse_id, lookback_races))
    horse_races = cur.fetchall()

    if not horse_races:
        if own_conn:
            cur.close()
            conn.close()
        return 0.0, 0, []

    total_score = 0.0
    total_weight = 0.0
    data_points = 0
    individual_scores = []

    for race_row in horse_races:
        race_id, race_date, opponents_json, horse_pos, horse_margin, race_class_level = race_row
        race_date_str = str(race_date)[:10] if race_date else "2020-01-01"
        race_class_level = _safe_int(race_class_level, 1)

        opponents = []
        if opponents_json:
            if isinstance(opponents_json, str):
                try:
                    opponents = json.loads(opponents_json)
                except (json.JSONDecodeError, TypeError):
                    opponents = []
            elif isinstance(opponents_json, list):
                opponents = opponents_json

        for opp in opponents:
            opp_id = opp.get("horse_id")
            if not opp_id:
                continue

            opp_margin = _safe_float(opp.get("margin"), 0.0)
            margin_diff = abs(opp_margin - _safe_float(horse_margin, 0.0))

            if margin_diff <= 3.0:
                margin_weight = 1.5
            elif margin_diff <= 5.0:
                margin_weight = 1.2
            else:
                margin_weight = 1.0

            cur.execute("""
                SELECT position, margin_lengths, class_level, race_date, field_size
                FROM race_results_history
                WHERE horse_id = %s AND race_date > %s
                  AND race_date <= %s
                  AND position IS NOT NULL
                ORDER BY race_date ASC
            """, (opp_id, race_date_str,
                  (datetime.strptime(race_date_str, "%Y-%m-%d") +
                   timedelta(days=forward_window_days)).strftime("%Y-%m-%d")))
            subsequent = cur.fetchall()

            for sub in subsequent:
                sub_pos, sub_margin, sub_class, sub_date, sub_field = sub
                sub_pos = _safe_int(sub_pos, 99)
                sub_class = _safe_int(sub_class, 1)
                sub_field = _safe_int(sub_field, 10)
                sub_margin = _safe_float(sub_margin, 0.0)

                class_factor = sub_class / 10.0

                if sub_pos == 1:
                    pts = 12.0 * class_factor
                elif sub_pos == 2:
                    pts = 8.0 * class_factor
                elif sub_pos == 3:
                    pts = 5.0 * class_factor
                elif sub_pos <= 5:
                    pts = 2.0 * class_factor
                elif sub_margin >= 10.0:
                    pts = -4.0
                else:
                    pts = 0.0

                sub_date_str = str(sub_date)[:10] if sub_date else race_date_str
                days_from_orig = _days_between(race_date_str, sub_date_str)
                recency = math.exp(-0.693 * days_from_orig / FRANKING_HALF_LIFE_DAYS)

                w = margin_weight * recency * (1.0 + class_factor * 0.5)
                total_score += pts * w
                total_weight += w
                data_points += 1
                individual_scores.append(pts * w)

    if own_conn:
        cur.close()
        conn.close()

    if total_weight > 0:
        raw_score = total_score / total_weight
    else:
        raw_score = 0.0

    normalised = max(0.0, min(100.0, (raw_score / MAX_FRANKING_RAW) * 100.0 + 50.0))
    return round(normalised, 2), data_points, individual_scores



def compute_recursive_franking(horse_id, conn=None):
    """
    Compute recursive (2-level deep) franking score.

    Goes one level deeper than direct franking: follows opponents' opponents to validate form chains.
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_connection()
    cur = conn.cursor()

    direct_score, direct_dp, _ = compute_direct_franking(
        horse_id, lookback_races=10, forward_window_days=90, conn=conn
    )

    cur.execute("""
        SELECT race_id, race_date, opponents_json
        FROM race_results_history
        WHERE horse_id = %s AND position IS NOT NULL
        ORDER BY race_date DESC
        LIMIT 10
    """, (horse_id,))
    horse_races = cur.fetchall()

    recursive_scores = []
    for race_row in horse_races:
        race_id, race_date, opponents_json = race_row
        race_date_str = str(race_date)[:10] if race_date else "2020-01-01"

        opponents = []
        if opponents_json:
            if isinstance(opponents_json, str):
                try:
                    opponents = json.loads(opponents_json)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(opponents_json, list):
                opponents = opponents_json

        for opp in opponents:
            opp_id = opp.get("horse_id")
            if not opp_id:
                continue

            cur.execute("""
                SELECT position FROM race_results_history
                WHERE horse_id = %s AND race_date > %s
                  AND position IS NOT NULL AND position <= 3
                ORDER BY race_date ASC
                LIMIT 3
            """, (opp_id, race_date_str))
            good_runs = cur.fetchall()

            if good_runs:
                opp_frank, opp_dp, _ = compute_direct_franking(
                    opp_id, lookback_races=5, forward_window_days=60, conn=conn
                )
                if opp_dp > 0:
                    recursive_scores.append(opp_frank)

    if own_conn:
        cur.close()
        conn.close()

    recursive_avg = float(np.mean(recursive_scores)) if recursive_scores else 50.0
    composite = direct_score * 0.7 + recursive_avg * 0.3
    return round(max(0.0, min(100.0, composite)), 2)



def detect_anti_franking(horse_id, lookback_races=5, conn=None):
    """
    Detect anti-franking: did beaten opponents perform poorly afterwards?

    Returns (anti_franked: bool, anti_score: float).
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT race_id, race_date, opponents_json, position
        FROM race_results_history
        WHERE horse_id = %s AND position IS NOT NULL
        ORDER BY race_date DESC
        LIMIT %s
    """, (horse_id, lookback_races))
    horse_races = cur.fetchall()

    if not horse_races:
        if own_conn:
            cur.close()
            conn.close()
        return False, 0.5

    total_checked = 0
    poor_results = 0

    for race_row in horse_races:
        race_id, race_date, opponents_json, horse_pos = race_row
        race_date_str = str(race_date)[:10] if race_date else "2020-01-01"
        horse_pos = _safe_int(horse_pos, 99)

        opponents = []
        if opponents_json:
            if isinstance(opponents_json, str):
                try:
                    opponents = json.loads(opponents_json)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(opponents_json, list):
                opponents = opponents_json

        for opp in opponents:
            opp_id = opp.get("horse_id")
            opp_pos = _safe_int(opp.get("position"), 99)
            if not opp_id or opp_pos <= horse_pos:
                continue

            cur.execute("""
                SELECT position, field_size
                FROM race_results_history
                WHERE horse_id = %s AND race_date > %s
                  AND position IS NOT NULL
                ORDER BY race_date ASC
                LIMIT 1
            """, (opp_id, race_date_str))
            next_run = cur.fetchone()
            if not next_run:
                continue

            next_pos = _safe_int(next_run[0], 99)
            next_field = _safe_int(next_run[1], 10)
            if next_field < 3:
                next_field = 3

            total_checked += 1
            bottom_third = next_field * 2 / 3.0
            if next_pos > bottom_third:
                poor_results += 1

    if own_conn:
        cur.close()
        conn.close()

    if total_checked == 0:
        return False, 0.5

    ratio = poor_results / total_checked
    anti_franked = ratio > 0.6
    return anti_franked, round(ratio, 4)



_global_elo_cache = {}


def compute_field_strength(race_id, elo_ratings=None):
    """
    Compute field strength for a race based on runners' ELO ratings.

    Returns (avg_elo, std_elo).
    """
    global _global_elo_cache
    if elo_ratings is None:
        elo_ratings = _global_elo_cache

    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT horse_id FROM race_results_history
        WHERE race_id = %s AND position IS NOT NULL
    """, (race_id,))
    runners = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    if not runners:
        return 50.0, 0.0

    elos = np.array([elo_ratings.get(hid, 50.0) for hid in runners])
    return round(float(elos.mean()), 2), round(float(elos.std()), 2)



def compute_form_quality_trend(horse_id, lookback_races=5, elo_ratings=None):
    """
    Compute slope of field strength over a horse's recent races.

    Positive = racing against progressively stronger fields.
    """
    global _global_elo_cache
    if elo_ratings is None:
        elo_ratings = _global_elo_cache

    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT race_id FROM race_results_history
        WHERE horse_id = %s AND position IS NOT NULL
        ORDER BY race_date DESC
        LIMIT %s
    """, (horse_id, lookback_races))
    race_ids = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    if len(race_ids) < 2:
        return 0.0

    race_ids.reverse()

    strengths = []
    for rid in race_ids:
        avg, _ = compute_field_strength(rid, elo_ratings)
        strengths.append(avg)

    if len(strengths) < 2:
        return 0.0

    x = np.arange(len(strengths), dtype=float)
    y = np.array(strengths, dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    return round(float(slope), 4)



def compute_franking_confidence(data_points, individual_scores):
    """
    Compute Bayesian confidence in the franking score.

    Based on number of data points and consistency of individual scores.
    """
    if data_points == 0:
        return 0.05

    data_factor = min(1.0, data_points / 30.0)

    if len(individual_scores) >= 2:
        scores = np.array(individual_scores)
        std_dev = float(scores.std())
        max_possible_std = MAX_FRANKING_RAW * 2.0
        consistency = 1.0 - min(0.5, std_dev / max_possible_std)
    else:
        consistency = 0.5

    confidence = data_factor * consistency
    return round(max(0.05, min(1.0, confidence)), 4)



def compute_all_franking_scores(target_horse_ids=None):
    """
    Orchestrate full franking score computation.

    Steps:
    1. Compute global ELO ratings
    2. Compute direct + recursive franking scores per horse
    3. Detect anti-franking
    4. Compute field strength and form quality trend
    5. Write results to DB
    """
    global _global_elo_cache
    t_start = time.time()

    elo_ratings = compute_global_elo(iterations=20)
    _global_elo_cache = elo_ratings
    t_elo = time.time()
    logger.info("Phase 1 - ELO: %d horses rated in %.1fs",
                len(elo_ratings), t_elo - t_start)

    conn = _get_connection()
    cur = conn.cursor()

    logger.info("Phase 2 - Loading ALL race_results_history into memory...")
    cur.execute("""
        SELECT horse_id, horse_name, race_id, race_date, position,
               margin_lengths, class_level, opponents_json, distance_m,
               going, field_size, weight_kg, race_class
        FROM race_results_history
        WHERE position IS NOT NULL
        ORDER BY race_date DESC
    """)
    all_rows = cur.fetchall()
    t_load = time.time()
    logger.info("Phase 2 - Loaded %d rows in %.1fs", len(all_rows), t_load - t_elo)

    horse_races = defaultdict(list)
    race_runners = defaultdict(list)
    horse_names = {}

    for row in all_rows:
        h_id, h_name, r_id, r_date, pos, margin, cl, opp_json, dist, going_val, fs, wt, rc_text = row
        r_date_str = str(r_date)[:10] if r_date else "2020-01-01"
        pos_int = _safe_int(pos)
        margin_f = _safe_float(margin)
        cl_int = _safe_int(cl, 1)
        fs_int = _safe_int(fs, 10)
        dist_int = _safe_int(dist, 1400)

        horse_races[h_id].append((r_id, r_date_str, pos_int, margin_f, cl_int,
                                   opp_json, dist_int, going_val, fs_int,
                                   wt, rc_text))
        race_runners[r_id].append((h_id, pos_int, margin_f, cl_int))
        if h_id not in horse_names:
            horse_names[h_id] = h_name

    t_index = time.time()
    logger.info("Phase 2 - Built indexes: %d horses, %d races in %.1fs",
                len(horse_races), len(race_runners), t_index - t_load)

    if target_horse_ids is not None:
        target_set = set(target_horse_ids)
        horses_to_process = [(hid, horse_names.get(hid, "")) for hid in target_set if hid in horse_races]
    else:
        horses_to_process = [(hid, horse_names.get(hid, "")) for hid in horse_races]

    logger.info("Phase 3 - Computing franking for %d horses...", len(horses_to_process))

    race_field_strength_cache = {}

    def _get_field_strength_cached(rid):
        if rid in race_field_strength_cache:
            return race_field_strength_cache[rid]
        runners_in_race = race_runners.get(rid, [])
        if not runners_in_race:
            race_field_strength_cache[rid] = (50.0, 0.0)
            return 50.0, 0.0
        elos = np.array([elo_ratings.get(r[0], 50.0) for r in runners_in_race])
        avg_e = round(float(elos.mean()), 2)
        std_e = round(float(elos.std()), 2)
        race_field_strength_cache[rid] = (avg_e, std_e)
        return avg_e, std_e

    results = []
    batch_rows = []

    for idx, (horse_id, horse_name) in enumerate(horses_to_process):
        try:
            races = horse_races.get(horse_id, [])
            if not races:
                continue

            last_10 = races[:10]
            last_5 = races[:5]

            total_score = 0.0
            total_weight = 0.0
            data_pts = 0
            individual_scores = []

            # Track the highest-class reference race for output
            best_ref_class_weight = 0.0
            best_ref_class_name = 'Unknown'
            any_group_listed = False

            for race_tuple in last_10:
                r_id, r_date_str, h_pos, h_margin, r_class, opp_json, dist, going_val, fs, wt, rc_text = race_tuple

                # Class weight of the REFERENCE race (the race where our horse ran)
                ref_class_weight, ref_class_name = _get_class_franking_weight(rc_text)
                if ref_class_weight > best_ref_class_weight:
                    best_ref_class_weight = ref_class_weight
                    best_ref_class_name = ref_class_name
                if ref_class_name in ('Group 1', 'Group 2', 'Group 3', 'Listed'):
                    any_group_listed = True

                opponents = []
                if opp_json:
                    if isinstance(opp_json, str):
                        try:
                            opponents = json.loads(opp_json)
                        except (json.JSONDecodeError, TypeError):
                            opponents = []
                    elif isinstance(opp_json, list):
                        opponents = opp_json

                race_date_dt = _date_from_str(r_date_str)
                if race_date_dt is None:
                    continue
                forward_limit_str = (race_date_dt + timedelta(days=90)).strftime("%Y-%m-%d")

                for opp in opponents:
                    opp_id = opp.get("horse_id")
                    if not opp_id:
                        continue

                    opp_margin_raw = _safe_float(opp.get("margin"), 0.0)
                    margin_diff = abs(opp_margin_raw - h_margin)
                    if margin_diff <= 3.0:
                        margin_weight = 1.5
                    elif margin_diff <= 5.0:
                        margin_weight = 1.2
                    else:
                        margin_weight = 1.0

                    opp_races = horse_races.get(opp_id, [])
                    for opp_race in opp_races:
                        o_rid, o_date, o_pos, o_margin, o_class, _, o_dist, o_going, o_fs, o_wt, o_rc_text = opp_race
                        if o_date <= r_date_str:
                            break
                        if o_date > forward_limit_str:
                            continue

                        sub_pos = o_pos
                        sub_class = o_class
                        sub_margin = o_margin
                        class_factor = sub_class / 10.0

                        if sub_pos == 1:
                            pts = 12.0 * class_factor
                        elif sub_pos == 2:
                            pts = 8.0 * class_factor
                        elif sub_pos == 3:
                            pts = 5.0 * class_factor
                        elif sub_pos <= 5:
                            pts = 2.0 * class_factor
                        elif sub_margin >= 10.0:
                            pts = -4.0
                        else:
                            pts = 0.0

                        # Apply class weight of the REFERENCE race
                        pts *= ref_class_weight

                        days_from_orig = _days_between(r_date_str, o_date)
                        recency = math.exp(-0.693 * days_from_orig / FRANKING_HALF_LIFE_DAYS)
                        w = margin_weight * recency * (1.0 + class_factor * 0.5)
                        total_score += pts * w
                        total_weight += w
                        data_pts += 1
                        individual_scores.append(pts * w)

            if total_weight > 0:
                raw_frank = total_score / total_weight
            else:
                raw_frank = 0.0
            direct_score = round(max(0.0, min(100.0, (raw_frank / MAX_FRANKING_RAW) * 100.0 + 50.0)), 2)

            total_checked = 0
            poor_results = 0
            for race_tuple in last_5:
                r_id, r_date_str, h_pos, h_margin, r_class, opp_json, dist, going_val, fs, wt, rc_text = race_tuple
                opponents = []
                if opp_json:
                    if isinstance(opp_json, str):
                        try:
                            opponents = json.loads(opp_json)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    elif isinstance(opp_json, list):
                        opponents = opp_json

                for opp in opponents:
                    opp_id = opp.get("horse_id")
                    opp_pos_raw = _safe_int(opp.get("position"), 99)
                    if not opp_id or opp_pos_raw <= h_pos:
                        continue

                    opp_races = horse_races.get(opp_id, [])
                    next_run = None
                    for opp_race in opp_races:
                        if opp_race[1] > r_date_str:
                            next_run = opp_race
                        else:
                            break
                    if next_run is None:
                        for opp_race in reversed(opp_races):
                            if opp_race[1] > r_date_str:
                                next_run = opp_race
                                break
                    if next_run is None:
                        continue

                    next_pos = next_run[2]
                    next_field = next_run[8]
                    if next_field < 3:
                        next_field = 3
                    total_checked += 1
                    bottom_third = next_field * 2 / 3.0
                    if next_pos > bottom_third:
                        poor_results += 1

            if total_checked == 0:
                anti_franked = False
                anti_ratio = 0.5
            else:
                anti_ratio = round(poor_results / total_checked, 4)
                anti_franked = anti_ratio > 0.6

            confidence = compute_franking_confidence(data_pts, individual_scores)

            # --- 2nd-layer recursive franking (class-weighted) ---
            # For opponents that won/placed, check what THEIR opponents did.
            # Weight by the class of the franking race (opponent's winning race).
            recursive_scores = []
            for race_tuple in last_5:
                r_id2, r_date_str2, h_pos2, h_margin2, r_class2, opp_json2, dist2, going2, fs2, wt2, rc_text2 = race_tuple
                opponents2 = []
                if opp_json2:
                    if isinstance(opp_json2, str):
                        try:
                            opponents2 = json.loads(opp_json2)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    elif isinstance(opp_json2, list):
                        opponents2 = opp_json2

                for opp2 in opponents2:
                    opp2_id = opp2.get("horse_id")
                    if not opp2_id:
                        continue
                    # Find opponent's subsequent wins/places (the franking race)
                    opp2_races = horse_races.get(opp2_id, [])
                    for opp2_race in opp2_races:
                        if opp2_race[1] <= r_date_str2:
                            break
                        if opp2_race[2] > 3:  # only follow top-3 finishers
                            continue
                        # This is the franking race — get its class weight
                        frank_class_w, _ = _get_class_franking_weight(opp2_race[10])
                        # Now check what THAT race's opponents did next (2nd layer)
                        frank_opp_json = opp2_race[5]
                        frank_opps = []
                        if frank_opp_json:
                            if isinstance(frank_opp_json, str):
                                try:
                                    frank_opps = json.loads(frank_opp_json)
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            elif isinstance(frank_opp_json, list):
                                frank_opps = frank_opp_json
                        for fopp in frank_opps[:5]:
                            fopp_id = fopp.get("horse_id")
                            if not fopp_id:
                                continue
                            fopp_races = horse_races.get(fopp_id, [])
                            for fopp_race in fopp_races:
                                if fopp_race[1] <= opp2_race[1]:
                                    break
                                if fopp_race[2] <= 3:
                                    # 2nd-layer frank: weighted by franking race class
                                    recursive_scores.append(frank_class_w)
                                    break

            if recursive_scores:
                recursive_avg = float(np.mean(recursive_scores))
            else:
                recursive_avg = 1.0

            # Composite: direct score adjusted by recursive class quality
            # recursive_avg > 1.0 means franking went through higher-class races
            composite = direct_score * (0.7 + 0.3 * recursive_avg)
            if anti_franked:
                composite *= 0.75
            composite = round(max(0.0, min(100.0, composite)), 2)

            global_elo = elo_ratings.get(horse_id, 50.0)

            best_adj = 0.0
            for race_tuple in races:
                if race_tuple[2] == 1:
                    adj = adjust_margin(
                        race_tuple[3], race_tuple[6], race_tuple[4],
                        race_tuple[7], race_tuple[8], race_tuple[9]
                    )
                    if adj > best_adj:
                        best_adj = adj

            recent_race_ids = [r[0] for r in last_5]
            field_strengths = []
            for rid in recent_race_ids:
                fs_avg, _ = _get_field_strength_cached(rid)
                field_strengths.append(fs_avg)
            field_strength_avg = round(float(np.mean(field_strengths)), 2) if field_strengths else 50.0

            if len(recent_race_ids) >= 2:
                race_ids_chrono = list(reversed(recent_race_ids))
                strengths = [_get_field_strength_cached(rid)[0] for rid in race_ids_chrono]
                if len(strengths) >= 2:
                    x = np.arange(len(strengths), dtype=float)
                    y = np.array(strengths, dtype=float)
                    slope, _ = np.polyfit(x, y, 1)
                    trend = round(float(slope), 4)
                else:
                    trend = 0.0
            else:
                trend = 0.0

            last_race_date = races[0][1] if races else None

            # franking_class: transparency about what class levels the franking chain went through
            franking_class_info = {
                "reference_class": best_ref_class_name,
                "reference_class_weight": round(best_ref_class_weight, 2),
                "group_listed_in_chain": any_group_listed,
                "recursive_class_avg": round(recursive_avg, 3),
            }

            details = {
                "direct_score": direct_score,
                "recursive_score": round(direct_score * (0.7 + 0.3 * recursive_avg), 2),
                "recursive_class_avg": round(recursive_avg, 3),
                "anti_franking_ratio": anti_ratio,
                "data_points": data_pts,
                "field_strengths": field_strengths[:5],
                "franking_class": franking_class_info,
            }

            result = {
                "horse_id": horse_id,
                "horse_name": horse_name,
                "global_elo": _to_native(global_elo),
                "franking_score": _to_native(composite),
                "franking_confidence": _to_native(confidence),
                "anti_franked": _to_native(anti_franked),
                "field_strength_avg": _to_native(field_strength_avg),
                "form_quality_trend": _to_native(trend),
                "best_adjusted_margin": _to_native(round(best_adj, 4)),
                "collateral_advantage": 0.0,
                "data_points": _to_native(data_pts),
                "last_race_date": last_race_date,
                "details_json": _to_native(details),
                "franking_class": _to_native(franking_class_info),
            }
            results.append(result)

            batch_rows.append((
                horse_id, horse_name, _to_native(global_elo), _to_native(composite),
                _to_native(confidence), _to_native(anti_franked), _to_native(field_strength_avg),
                _to_native(trend), _to_native(round(best_adj, 4)), 0.0, _to_native(data_pts),
                last_race_date, Json(_to_native(details)),
            ))

            if (idx + 1) % 1000 == 0:
                logger.info("  Processed %d / %d horses...", idx + 1, len(horses_to_process))

        except Exception as e:
            logger.error("Error computing franking for horse %s: %s", horse_id, e)
            continue

    t_frank = time.time()
    logger.info("Phase 3 - Franking computed for %d horses in %.1fs",
                len(results), t_frank - t_index)

    logger.info("Phase 4 - Upserting %d franking scores to DB...", len(batch_rows))
    CHUNK_SIZE = 500
    for chunk_start in range(0, len(batch_rows), CHUNK_SIZE):
        chunk = batch_rows[chunk_start:chunk_start + CHUNK_SIZE]
        execute_values(cur, """
            INSERT INTO franking_scores (
                horse_id, horse_name, global_elo, franking_score,
                franking_confidence, anti_franked, field_strength_avg,
                form_quality_trend, best_adjusted_margin, collateral_advantage,
                data_points, last_race_date, details_json, computed_at
            ) VALUES %s
            ON CONFLICT (horse_id) DO UPDATE SET
                horse_name = EXCLUDED.horse_name,
                global_elo = EXCLUDED.global_elo,
                franking_score = EXCLUDED.franking_score,
                franking_confidence = EXCLUDED.franking_confidence,
                anti_franked = EXCLUDED.anti_franked,
                field_strength_avg = EXCLUDED.field_strength_avg,
                form_quality_trend = EXCLUDED.form_quality_trend,
                best_adjusted_margin = EXCLUDED.best_adjusted_margin,
                collateral_advantage = EXCLUDED.collateral_advantage,
                data_points = EXCLUDED.data_points,
                last_race_date = EXCLUDED.last_race_date,
                details_json = EXCLUDED.details_json,
                computed_at = NOW()
        """, chunk, template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())")
        conn.commit()
        logger.info("  Upserted chunk %d-%d", chunk_start + 1, chunk_start + len(chunk))

    t_write = time.time()
    logger.info("Phase 4 - DB write complete in %.1fs", t_write - t_frank)

    cur.close()
    conn.close()

    elapsed = time.time() - t_start
    logger.info("Franking computation complete: %d horses in %.1fs", len(results), elapsed)
    logger.info("  Timing breakdown: ELO=%.1fs, Load=%.1fs, Index=%.1fs, Frank=%.1fs, Write=%.1fs",
                t_elo - t_start, t_load - t_elo, t_index - t_load, t_frank - t_index, t_write - t_frank)
    return results



_DEFAULTS = {
    "global_elo": 50.0,
    "franking_score": 50.0,
    "franking_confidence": 0.1,
    "anti_franked": False,
    "field_strength_avg": 50.0,
    "form_quality_trend": 0.0,
    "best_adjusted_margin": 0.0,
    "collateral_advantage": 0.0,
    "details_json": {},
    "franking_class": {
        "reference_class": "Unknown",
        "reference_class_weight": 1.0,
        "group_listed_in_chain": False,
        "recursive_class_avg": 1.0,
    },
}


def get_franking_for_runners(horse_ids):
    """
    Look up cached franking scores for a list of horse_ids.

    Returns dict {horse_id: {global_elo, franking_score, classification, ...}}.
    """
    if not horse_ids:
        return {}

    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT horse_id, horse_name, global_elo, franking_score,
               franking_confidence, anti_franked, field_strength_avg,
               form_quality_trend, best_adjusted_margin, collateral_advantage,
               details_json
        FROM franking_scores
        WHERE horse_id = ANY(%s)
    """, (list(horse_ids),))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    result = {}
    found = set()
    for r in rows:
        found.add(r[0])
        dj = r[10] if r[10] else {}
        if isinstance(dj, str):
            try:
                dj = json.loads(dj)
            except (json.JSONDecodeError, TypeError):
                dj = {}
        fc = dj.get("franking_class", _DEFAULTS["franking_class"])
        result[r[0]] = {
            "horse_name": r[1],
            "global_elo": _safe_float(r[2], 50.0),
            "franking_score": _safe_float(r[3], 50.0),
            "franking_confidence": _safe_float(r[4], 0.1),
            "anti_franked": bool(r[5]),
            "field_strength_avg": _safe_float(r[6], 50.0),
            "form_quality_trend": _safe_float(r[7], 0.0),
            "best_adjusted_margin": _safe_float(r[8], 0.0),
            "collateral_advantage": _safe_float(r[9], 0.0),
            "details_json": dj,
            "franking_class": fc,
        }

    for hid in horse_ids:
        if hid not in found:
            result[hid] = dict(_DEFAULTS)

    return result


def get_franking_for_race(race_runners):
    """
    Get franking data for all runners in a race.

    Also computes collateral_advantage: how each runner's franking score compares to the field average.
    """
    horse_ids = [r.get("horse_id") for r in race_runners if r.get("horse_id")]
    if not horse_ids:
        return []

    franking_data = get_franking_for_runners(horse_ids)

    elos = [franking_data[hid]["global_elo"] for hid in horse_ids if hid in franking_data]
    avg_elo = float(np.mean(elos)) if elos else 50.0

    results = []
    for runner in race_runners:
        hid = runner.get("horse_id")
        if not hid:
            continue
        data = franking_data.get(hid, dict(_DEFAULTS))
        data["collateral_advantage"] = round(data["global_elo"] - avg_elo, 2)
        data["horse_id"] = hid
        results.append(data)

    return results



def _cli_stats():
    """Print database statistics."""
    conn = _get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM race_results_history")
    total_results = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT horse_id) FROM race_results_history")
    total_horses = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT race_id) FROM race_results_history")
    total_races = cur.fetchone()[0]

    cur.execute("SELECT MIN(race_date), MAX(race_date) FROM race_results_history")
    date_range = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM franking_scores")
    cached = cur.fetchone()[0]

    cur.close()
    conn.close()

    print("=" * 55)
    print("  FORM FRANKING DATABASE STATISTICS")
    print("=" * 55)
    print(f"  Total result rows:     {total_results:,}")
    print(f"  Distinct horses:       {total_horses:,}")
    print(f"  Distinct races:        {total_races:,}")
    print(f"  Date range:            {date_range[0]} to {date_range[1]}")
    print(f"  Cached franking scores:{cached:,}")
    print("=" * 55)


def _cli_horse(horse_id):
    """Show detailed franking for one horse."""
    print(f"\nComputing franking for horse_id={horse_id}...")
    t0 = time.time()

    global _global_elo_cache
    if not _global_elo_cache:
        print("  Computing global ELO (this may take a moment)...")
        _global_elo_cache = compute_global_elo(iterations=10)

    conn = _get_connection()

    cur = conn.cursor()
    cur.execute("""
        SELECT horse_name, COUNT(*) as runs,
               SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) as wins
        FROM race_results_history
        WHERE horse_id = %s
        GROUP BY horse_name
    """, (horse_id,))
    info = cur.fetchone()
    cur.close()

    if not info:
        print(f"  Horse {horse_id} not found in database.")
        conn.close()
        return

    horse_name, runs, wins = info
    print(f"\n  Horse: {horse_name} (ID: {horse_id})")
    print(f"  Career: {runs} runs, {wins} wins")

    direct, dp, scores = compute_direct_franking(horse_id, conn=conn)
    recursive = compute_recursive_franking(horse_id, conn=conn)
    anti, ratio = detect_anti_franking(horse_id, conn=conn)
    trend = compute_form_quality_trend(horse_id, elo_ratings=_global_elo_cache)
    conf = compute_franking_confidence(dp, scores)
    elo = _global_elo_cache.get(horse_id, 50.0)

    composite = direct * 0.7 + recursive * 0.3
    if anti:
        composite *= 0.75

    conn.close()

    print(f"\n  Global ELO:            {elo:.2f}")
    print(f"  Direct Franking:       {direct:.2f}")
    print(f"  Recursive Franking:    {recursive:.2f}")
    print(f"  Composite Score:       {composite:.2f}")
    print(f"  Confidence:            {conf:.4f}")
    print(f"  Anti-Franked:          {anti} (ratio: {ratio:.4f})")
    print(f"  Form Quality Trend:    {trend:.4f}")
    print(f"  Data Points:           {dp}")
    print(f"  Computed in:           {time.time() - t0:.1f}s")


def _cli_compute_all():
    """Run full computation for all horses."""
    print("Starting full franking computation for ALL horses...")
    t0 = time.time()
    results = compute_all_franking_scores()
    elapsed = time.time() - t0

    print(f"\nComputation complete in {elapsed:.1f}s")
    print(f"Horses processed: {len(results)}")

    if results:
        scores = [r["franking_score"] for r in results]
        elos = [r["global_elo"] for r in results]
        anti_count = sum(1 for r in results if r["anti_franked"])

        print(f"\nFranking Score Distribution:")
        print(f"  Mean:   {np.mean(scores):.2f}")
        print(f"  Median: {np.median(scores):.2f}")
        print(f"  Min:    {np.min(scores):.2f}")
        print(f"  Max:    {np.max(scores):.2f}")
        print(f"\nELO Distribution:")
        print(f"  Mean:   {np.mean(elos):.2f}")
        print(f"  Median: {np.median(elos):.2f}")
        print(f"\nAnti-Franked horses: {anti_count} ({anti_count/len(results)*100:.1f}%)")

        top = sorted(results, key=lambda x: x["franking_score"], reverse=True)[:10]
        print("\nTop 10 by Franking Score:")
        for i, r in enumerate(top, 1):
            print(f"  {i:2d}. {r['horse_name'][:25]:<25} "
                  f"Score={r['franking_score']:6.2f}  "
                  f"ELO={r['global_elo']:6.2f}  "
                  f"Conf={r['franking_confidence']:.2f}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Form Franking & Collateral Form Analysis Engine"
    )
    parser.add_argument("--compute-all", action="store_true",
                        help="Run full computation for all horses")
    parser.add_argument("--horse-id", type=str, default=None,
                        help="Show detailed franking for one horse")
    parser.add_argument("--race-id", type=str, default=None,
                        help="Show field strength for a race")
    parser.add_argument("--stats", action="store_true",
                        help="Show database statistics")
    parser.add_argument("--refresh-elo", action="store_true",
                        help="Force recomputation of ELO ratings (ignore cache)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    if args.refresh_elo:
        print("Force-refreshing ELO ratings...")
        elo = compute_global_elo(iterations=20, force_recompute=True)
        print(f"ELO computed and cached for {len(elo)} horses.")
        return

    if args.stats:
        _cli_stats()
    elif args.horse_id:
        _cli_horse(args.horse_id)
    elif args.race_id:
        global _global_elo_cache
        if not _global_elo_cache:
            print("Computing ELO ratings first...")
            _global_elo_cache = compute_global_elo(iterations=10)
        avg, std = compute_field_strength(args.race_id, _global_elo_cache)
        print(f"\nRace {args.race_id}:")
        print(f"  Field Strength (avg ELO): {avg:.2f}")
        print(f"  Field Spread (std):       {std:.2f}")
    elif args.compute_all:
        _cli_compute_all()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
