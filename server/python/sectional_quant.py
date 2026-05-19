#!/usr/bin/env python3
"""Five quantitative engines for horse racing Monte Carlo simulation enhancement. Uses sectional_times database table with ~25K records across 42 tracks."""

import os
import sys
import time
import json
import math
import statistics

try:
    import numpy as np
    NP_AVAILABLE = True
except ImportError:
    NP_AVAILABLE = False

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


_par_fsp_cache = {'data': None, 'ts': 0}
_horse_sasr_cache = {}
_race_shape_cache = {'data': {}, 'ts': 0}
_closing_field_cache = {'data': {}, 'ts': 0}
_horse_closing_cache = {}
_horse_shape_cache = {}
_horse_trip_cache = {}
_track_collapse_cache = {}
_all_quant_cache = {}

CACHE_TTL = 3600
SHORT_CACHE_TTL = 1800


def _dbg(msg):
    print(f"[SectionalQuant] {msg}", file=sys.stderr)


def _get_conn():
    if not PSYCOPG2_AVAILABLE:
        return None
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        return None
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        _dbg(f"DB connection error: {e}")
        return None


def _cache_valid(cache_dict, key=None, ttl=CACHE_TTL):
    if key is not None:
        entry = cache_dict.get(key)
        if entry and time.time() - entry.get('ts', 0) < ttl:
            return True
        return False
    return cache_dict.get('data') is not None and time.time() - cache_dict.get('ts', 0) < ttl


def _normalize_going(track_config):
    if not track_config:
        return 'good'
    tc = str(track_config).lower()
    if 'heavy' in tc:
        return 'heavy'
    if 'soft' in tc:
        return 'soft'
    if 'good' in tc or 'firm' in tc:
        return 'good'
    return 'good'



def _parse_mark(distance_str):
    """Extract 'mark' (metres remaining) from distance string like '800m-600m' or '200m-FINISH'."""
    import re as _re
    if not distance_str:
        return None
    parts = str(distance_str).split('-')
    if len(parts) != 2:
        return None
    end = parts[1].strip()
    if end.upper() == 'FINISH':
        return 0
    m = _re.match(r'(\d+)', end)
    return int(m.group(1)) if m else None


def _compute_lambda_decay(splits_json):
    """Ward-Smith velocity decay constant. See backfill_lambda_targeted for math."""
    from backfill_lambda_targeted import normalise_splits, compute_lambda
    return compute_lambda(normalise_splits(splits_json))


def _compute_svi(splits_json):
    """Sustained Velocity Index (mean of finishing 3 sections / mean of all)."""
    from backfill_lambda_targeted import normalise_splits, compute_svi
    return compute_svi(normalise_splits(splits_json))


def _compute_rsi(splits_json, distance_m):
    """Race Shape Index (first-half time / second-half time)."""
    from backfill_lambda_targeted import normalise_splits, compute_rsi
    return compute_rsi(normalise_splits(splits_json), distance_m)


def _compute_z_scores_for_race(conn, track: str, race_date: str, race_number: int) -> dict:
    """
    Z-score normalisation per section vs field.

    z_M_H = (speed_ms_H_M - mu_M) / sigma_M
    where mu_M and sigma_M are the field mean and std for section M in a given race.
    """
    if not conn:
        return {}
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT horse_name, last_200m_speed, last_400m_speed,
                   last_600m_speed, last_800m_speed
            FROM sectional_times
            WHERE track = %s AND race_date = %s AND race_number = %s
              AND last_200m_speed IS NOT NULL
        """, (track, race_date, race_number))
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        _dbg(f"Z-score query error: {e}")
        return {}

    if len(rows) < 3:
        return {}

    z_results = {}
    for mark, col in [(200, 'last_200m_speed'), (400, 'last_400m_speed'),
                      (600, 'last_600m_speed'), (800, 'last_800m_speed')]:
        field_speeds = [float(r[col]) for r in rows if r.get(col) is not None]
        if len(field_speeds) < 3:
            continue
        try:
            mu = statistics.mean(field_speeds)
            sigma = statistics.stdev(field_speeds)
        except Exception:
            continue
        if sigma == 0:
            continue
        for row in rows:
            h = row['horse_name']
            v = row.get(col)
            if v is not None:
                z_results.setdefault(h, {})[f'z_{mark}m'] = round((float(v) - mu) / sigma, 4)

    return z_results


def _compute_trip_cost_seconds(barrier: int, distance_m: int, avg_speed_ms: float) -> float:
    """
    Thorograph trip cost formula (Brohamer 2000).

    Converts extra ground run due to wide barrier draw into time-equivalent seconds lost. Wider barriers at longer distances cost more.
    """
    try:
        b = int(barrier) if barrier else 1
        d = int(distance_m) if distance_m else 1400
        v = float(avg_speed_ms) if avg_speed_ms and float(avg_speed_ms) > 0 else 14.5
        if b <= 1:
            return 0.0
        LANE_WIDTH = 1.8
        CURVATURE_FACTOR = 0.65
        turns = 1 if d < 1200 else 2
        extra_metres = (b - 1) * LANE_WIDTH * turns * CURVATURE_FACTOR
        return round(extra_metres / v, 3)
    except Exception:
        return 0.0



_SASR_RECENCY_WEIGHTS = [0.30, 0.22, 0.16, 0.12, 0.08, 0.05, 0.03, 0.02, 0.01, 0.01]

_SASR_DEFAULTS = {
    'sasr_mu_shift': 0.0,
    'sasr_upgrade_lbs': 0.0,
    'sasr_fsp': 100.0,
    'sasr_par': 100.0,
    'sasr_deviation': 0.0,
    'sasr_confidence': 0.0,
    'sasr_runs_analyzed': 0,
    'sasr_grade': 'C',
}


def _build_par_fsp_table():
    global _par_fsp_cache
    if _cache_valid(_par_fsp_cache):
        return _par_fsp_cache['data']

    conn = _get_conn()
    if not conn:
        _par_fsp_cache = {'data': {}, 'ts': time.time()}
        return {}

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT track, distance_m, track_config,
                   last_200m_speed, avg_speed
            FROM sectional_times
            WHERE last_200m_speed > 0 AND avg_speed > 0
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        _dbg(f"Par FSP query error: {e}")
        try:
            conn.close()
        except Exception:
            pass
        _par_fsp_cache = {'data': {}, 'ts': time.time()}
        return {}

    groups = {}
    for r in rows:
        fsp = (r['last_200m_speed'] / r['avg_speed']) * 100 if r['avg_speed'] > 0 else 100.0
        going = _normalize_going(r.get('track_config'))
        track = str(r.get('track', '')).lower().strip()
        dist = int(r.get('distance_m') or 0)

        key_full = (track, dist, going)
        key_td = (track, dist)
        key_d = ('__all__', dist)

        for k in [key_full, key_td, key_d]:
            groups.setdefault(k, []).append(fsp)

    par_table = {}
    for k, vals in groups.items():
        vals.sort()
        mid = len(vals) // 2
        median = vals[mid] if len(vals) % 2 == 1 else (vals[mid - 1] + vals[mid]) / 2
        par_table[k] = median

    _par_fsp_cache = {'data': par_table, 'ts': time.time()}
    _dbg(f"Built par FSP table with {len(par_table)} groups")
    return par_table


def _get_par_fsp(track, distance_m, going):
    par_table = _build_par_fsp_table()
    if not par_table:
        return 100.0

    t = str(track).lower().strip()
    d = int(distance_m)
    g = _normalize_going(going)

    for key in [(t, d, g), (t, d), ('__all__', d)]:
        if key in par_table:
            return par_table[key]
    return 100.0


def compute_sasr_upgrade(horse_name, track, distance_m, track_config):
    global _horse_sasr_cache

    cache_key = f"{horse_name.lower()}|{track}|{distance_m}|{track_config}"
    if _cache_valid(_horse_sasr_cache, cache_key):
        return _horse_sasr_cache[cache_key]['data']

    result = dict(_SASR_DEFAULTS)

    conn = _get_conn()
    if not conn:
        return result

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT last_200m_speed, avg_speed, distance_m, track_config, race_date
            FROM sectional_times
            WHERE LOWER(horse_name) = LOWER(%s)
              AND last_200m_speed > 0 AND avg_speed > 0
            ORDER BY race_date DESC
            LIMIT 10
        """, (horse_name,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        _dbg(f"SASR query error for {horse_name}: {e}")
        try:
            conn.close()
        except Exception:
            pass
        return result

    n = len(rows)
    if n == 0:
        return result

    fsp_vals = []
    for r in rows:
        fsp = (r['last_200m_speed'] / r['avg_speed']) * 100 if r['avg_speed'] > 0 else 100.0
        fsp_vals.append(fsp)

    weights = _SASR_RECENCY_WEIGHTS[:n]
    w_sum = sum(weights)
    horse_fsp = sum(f * w for f, w in zip(fsp_vals, weights)) / w_sum if w_sum > 0 else 100.0

    par = _get_par_fsp(track, distance_m, track_config)
    deviation = par - horse_fsp

    upgrade_lbs = 1.5 * (deviation ** 2) * (200 / max(int(distance_m), 200)) ** 1.4
    if deviation > 0:
        upgrade_lbs = abs(upgrade_lbs)
    else:
        upgrade_lbs = -abs(upgrade_lbs)

    mu_shift = upgrade_lbs * 0.012
    mu_shift = max(-0.15, min(0.15, mu_shift))

    confidence = min(1.0, n / 5.0)
    if n < 3:
        mu_shift *= (n / 3.0)

    abs_dev = abs(deviation)
    if abs_dev < 1.5:
        grade = 'A'
    elif abs_dev < 3.0:
        grade = 'B'
    elif abs_dev < 5.0:
        grade = 'C'
    elif abs_dev < 8.0:
        grade = 'D'
    else:
        grade = 'F'

    result = {
        'sasr_mu_shift': round(mu_shift, 4),
        'sasr_upgrade_lbs': round(upgrade_lbs, 2),
        'sasr_fsp': round(horse_fsp, 2),
        'sasr_par': round(par, 2),
        'sasr_deviation': round(deviation, 2),
        'sasr_confidence': round(confidence, 2),
        'sasr_runs_analyzed': n,
        'sasr_grade': grade,
    }

    _horse_sasr_cache[cache_key] = {'data': result, 'ts': time.time()}
    return result



_COLLAPSE_DEFAULTS = {
    'collapse_probability': 0.0,
    'collapse_score': 0.0,
    'speed_horse_count': 0,
    'speed_density': 0.0,
    'early_clustering_std': 0.0,
    'barrier_loading_score': 0.0,
    'distance_factor': 1.0,
    'track_historical_collapse_rate': 0.35,
    'field_size_factor': 1.0,
    'signals': [],
    'per_horse_collapse_adj': {},
}


def _get_distance_factor(dist_m):
    d = int(dist_m)
    if d <= 1000:
        return 0.6
    elif d <= 1200:
        return 0.8
    elif d <= 1600:
        return 1.0
    elif d <= 2000:
        return 1.15
    else:
        return 1.3


def _get_field_size_factor(n):
    if n <= 6:
        return 0.7
    elif n <= 9:
        return 0.9
    elif n <= 12:
        return 1.0
    else:
        return 1.15


def _get_track_collapse_rate(track, distance_m):
    global _track_collapse_cache

    cache_key = f"{str(track).lower()}|{distance_m}"
    if _cache_valid(_track_collapse_cache, cache_key):
        return _track_collapse_cache[cache_key]['data']

    default_rate = 0.35
    conn = _get_conn()
    if not conn:
        return default_rate

    try:
        d = int(distance_m)
        d_lo = d - 200
        d_hi = d + 200
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            WITH race_leaders AS (
                SELECT track, race_date, race_number, horse_name,
                       splits_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY track, race_date, race_number
                           ORDER BY (splits_json->'600m'->>'position')::int ASC NULLS LAST
                       ) as pos_rank
                FROM sectional_times
                WHERE LOWER(track) = LOWER(%s)
                  AND distance_m BETWEEN %s AND %s
                  AND splits_json IS NOT NULL
                  AND splits_json->'600m' IS NOT NULL
            ),
            leader_finishes AS (
                SELECT rl.track, rl.race_date, rl.race_number,
                       rl.horse_name as leader_name,
                       (rl.splits_json->
                           (SELECT key FROM jsonb_object_keys(rl.splits_json) AS key
                            ORDER BY key DESC LIMIT 1)
                        ->>'position')::int as final_pos,
                       (SELECT COUNT(*) FROM sectional_times st2
                        WHERE st2.track = rl.track
                          AND st2.race_date = rl.race_date
                          AND st2.race_number = rl.race_number) as field_size
                FROM race_leaders rl
                WHERE rl.pos_rank = 1
            )
            SELECT COUNT(*) as total_races,
                   SUM(CASE WHEN final_pos > 3 OR final_pos IS NULL THEN 1 ELSE 0 END) as collapsed
            FROM leader_finishes
            WHERE field_size >= 4
        """, (track, d_lo, d_hi))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row and row['total_races'] and row['total_races'] >= 10:
            rate = float(row['collapsed']) / float(row['total_races'])
        else:
            rate = default_rate
    except Exception as e:
        _dbg(f"Track collapse rate query error: {e}")
        try:
            conn.close()
        except Exception:
            pass
        rate = default_rate

    _track_collapse_cache[cache_key] = {'data': rate, 'ts': time.time()}
    return rate


def compute_pace_collapse(runners, race_distance_m, track):
    result = dict(_COLLAPSE_DEFAULTS)
    result['signals'] = []
    result['per_horse_collapse_adj'] = {}

    try:
        field_size = len(runners)
        if field_size == 0:
            return result

        speed_horses = [r for r in runners if r.get('running_style') in ('leader', 'on_pace')]
        speed_count = len(speed_horses)
        speed_density = speed_count / max(field_size, 1)

        speed_early_pcts = [r.get('early_speed_pct', 100.0) for r in speed_horses if r.get('early_speed_pct')]
        if len(speed_early_pcts) >= 2:
            early_std = statistics.stdev(speed_early_pcts)
        elif len(speed_early_pcts) == 1:
            early_std = 5.0
        else:
            early_std = 10.0
        early_clustering_score = max(0.0, 30.0 - early_std * 10.0)

        inside_speed = sum(1 for r in speed_horses if r.get('barrier', 99) <= 4)
        barrier_loading_score = (inside_speed / max(speed_count, 1)) * 100.0

        distance_factor = _get_distance_factor(race_distance_m)
        field_size_factor = _get_field_size_factor(field_size)
        track_collapse_rate = _get_track_collapse_rate(track, race_distance_m)

        signals = []
        if speed_count >= 4:
            signals.append('many_speed_horses')
        if speed_density > 0.35:
            signals.append('high_speed_density')
        if early_clustering_score > 20:
            signals.append('tight_early_clustering')
        if inside_speed >= 3:
            signals.append('barrier_pressure')
        if track_collapse_rate > 0.45:
            signals.append('high_collapse_track')

        score = (
            speed_density * 100 * 0.30 +
            early_clustering_score * 0.20 +
            barrier_loading_score * 0.15 * distance_factor +
            track_collapse_rate * 100 * 0.10 +
            field_size_factor * 30 * 0.10 +
            (1 if speed_count >= 4 else 0) * 30 * 0.15
        )
        score = max(0.0, min(100.0, score))

        # Phase 2: mechanistic λ contribution — high field avg decay → higher collapse risk
        # (λ=0.0003 across field adds ~4pts; λ=0.0005 adds ~6.7pts)
        field_lambdas = [r.get('lambda_decay') for r in runners if r.get('lambda_decay') is not None]
        if field_lambdas:
            avg_lambda = statistics.mean(field_lambdas)
            # Scale: avg_lambda * 20000 → 0-10 pts contribution (capped at 15)
            lambda_score_contrib = min(15.0, avg_lambda * 20000)
            score = min(100.0, score + lambda_score_contrib * 0.20)
            if avg_lambda > 0.0003:
                signals.append('high_field_velocity_decay')

        collapse_prob = 1.0 / (1.0 + math.exp(-(score - 45.0) / 8.0))

        per_horse_adj = {}
        style_adj_map = {
            'leader': -0.08,
            'on_pace': -0.04,
            'handy': 0.01,
            'midfield': 0.02,
            'backmarker': 0.04,
        }
        for r in runners:
            style = r.get('running_style', 'midfield')
            factor = style_adj_map.get(style, 0.0)
            per_horse_adj[r.get('horse_name', '')] = round(collapse_prob * factor, 4)

        result = {
            'collapse_probability': round(collapse_prob, 4),
            'collapse_score': round(score, 2),
            'speed_horse_count': speed_count,
            'speed_density': round(speed_density, 3),
            'early_clustering_std': round(early_std, 2),
            'barrier_loading_score': round(barrier_loading_score, 1),
            'distance_factor': distance_factor,
            'track_historical_collapse_rate': round(track_collapse_rate, 3),
            'field_size_factor': field_size_factor,
            'signals': signals,
            'per_horse_collapse_adj': per_horse_adj,
        }
    except Exception as e:
        _dbg(f"Pace collapse error: {e}")

    return result



_SHAPE_DEFAULTS = {
    'shape_fit_score': 50.0,
    'shape_mu_shift': 0.0,
    'shape_matrix': {},
    'best_scenario': 'genuine',
    'worst_scenario': 'genuine',
    'runs_classified': 0,
    'confidence': 0.0,
}


def _build_race_shape_cache(track, distance_m):
    global _race_shape_cache

    cache_key = f"{str(track).lower()}|{distance_m}"
    if cache_key in _race_shape_cache.get('data', {}) and _cache_valid(_race_shape_cache):
        return _race_shape_cache['data'][cache_key]

    conn = _get_conn()
    if not conn:
        return {}

    try:
        d = int(distance_m)
        d_lo = d - 200
        d_hi = d + 200
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT track, race_date, race_number,
                   AVG(last_200m_speed) as avg_closing,
                   AVG(avg_speed) as avg_overall,
                   COUNT(*) as field_cnt
            FROM sectional_times
            WHERE LOWER(track) = LOWER(%s)
              AND distance_m BETWEEN %s AND %s
              AND last_200m_speed > 0 AND avg_speed > 0
            GROUP BY track, race_date, race_number
            HAVING COUNT(*) >= 4
        """, (track, d_lo, d_hi))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        _dbg(f"Race shape cache query error: {e}")
        try:
            conn.close()
        except Exception:
            pass
        return {}

    shapes = {}
    for r in rows:
        avg_closing = float(r['avg_closing'] or 0)
        avg_overall = float(r['avg_overall'] or 1)
        if avg_overall <= 0:
            continue
        ratio = avg_closing / avg_overall
        if ratio > 1.03:
            scenario = 'hot'
        elif ratio >= 0.99:
            scenario = 'genuine'
        elif ratio >= 0.95:
            scenario = 'soft'
        else:
            scenario = 'slow'
        race_key = (str(r['track']).lower(), str(r['race_date']), int(r['race_number']))
        shapes[race_key] = {'scenario': scenario, 'ratio': ratio, 'field_cnt': int(r['field_cnt'])}

    if not _race_shape_cache.get('data'):
        _race_shape_cache['data'] = {}
    _race_shape_cache['data'][cache_key] = shapes
    _race_shape_cache['ts'] = time.time()
    _dbg(f"Built race shape cache for {track}/{distance_m}: {len(shapes)} races")
    return shapes


def compute_race_shape_fit(horse_name, predicted_pace_scenario, track, distance_m):
    global _horse_shape_cache

    cache_key = f"{horse_name.lower()}|{predicted_pace_scenario}|{track}|{distance_m}"
    if _cache_valid(_horse_shape_cache, cache_key):
        return _horse_shape_cache[cache_key]['data']

    result = dict(_SHAPE_DEFAULTS)

    conn = _get_conn()
    if not conn:
        return result

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT track, race_date, race_number, splits_json,
                   last_200m_speed, avg_speed
            FROM sectional_times
            WHERE LOWER(horse_name) = LOWER(%s)
              AND splits_json IS NOT NULL
            ORDER BY race_date DESC
            LIMIT 15
        """, (horse_name,))
        horse_rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        _dbg(f"Shape fit query error for {horse_name}: {e}")
        try:
            conn.close()
        except Exception:
            pass
        return result

    if not horse_rows:
        return result

    race_shapes = _build_race_shape_cache(track, distance_m)

    all_shapes_lookup = {}
    for r in horse_rows:
        rk = (str(r['track']).lower(), str(r['race_date']), int(r['race_number']))
        if rk in race_shapes:
            all_shapes_lookup[rk] = race_shapes[rk]
        else:
            avg_closing = float(r.get('last_200m_speed') or 0)
            avg_overall = float(r.get('avg_speed') or 1)
            if avg_overall > 0 and avg_closing > 0:
                ratio = avg_closing / avg_overall
                if ratio > 1.03:
                    sc = 'hot'
                elif ratio >= 0.99:
                    sc = 'genuine'
                elif ratio >= 0.95:
                    sc = 'soft'
                else:
                    sc = 'slow'
                all_shapes_lookup[rk] = {'scenario': sc, 'ratio': ratio, 'field_cnt': 1}

    shape_matrix = {}
    for r in horse_rows:
        rk = (str(r['track']).lower(), str(r['race_date']), int(r['race_number']))
        shape_info = all_shapes_lookup.get(rk)
        if not shape_info:
            continue

        scenario = shape_info['scenario']
        field_cnt = shape_info.get('field_cnt', 8)

        splits = r.get('splits_json')
        if isinstance(splits, str):
            try:
                splits = json.loads(splits)
            except Exception:
                splits = {}

        position_pct = 0.5
        if splits and isinstance(splits, dict):
            sorted_marks = sorted(splits.keys(), key=lambda k: int(k.replace('m', '')))
            if sorted_marks:
                last_mark = sorted_marks[-1]
                final_pos = splits[last_mark].get('position', field_cnt // 2)
                position_pct = final_pos / max(field_cnt, 1)

        if scenario not in shape_matrix:
            shape_matrix[scenario] = {'positions': [], 'n_runs': 0}
        shape_matrix[scenario]['positions'].append(position_pct)
        shape_matrix[scenario]['n_runs'] += 1

    runs_classified = sum(v['n_runs'] for v in shape_matrix.values())
    if runs_classified == 0:
        _horse_shape_cache[cache_key] = {'data': result, 'ts': time.time()}
        return result

    for sc in shape_matrix:
        positions = shape_matrix[sc]['positions']
        shape_matrix[sc]['avg_position_pct'] = round(sum(positions) / len(positions), 3) if positions else 0.5
        shape_matrix[sc]['avg_margin'] = round(shape_matrix[sc]['avg_position_pct'], 3)

    all_positions = []
    for sc in shape_matrix:
        all_positions.extend(shape_matrix[sc]['positions'])
    overall_perf = sum(all_positions) / len(all_positions) if all_positions else 0.5

    target = predicted_pace_scenario.lower()
    if target in shape_matrix and shape_matrix[target]['n_runs'] > 0:
        target_perf = shape_matrix[target]['avg_position_pct']
        denom = max(1.0 - overall_perf, 0.01)
        raw_fit = (1.0 - target_perf) / denom * 50.0
    else:
        raw_fit = 50.0

    n_target = shape_matrix.get(target, {}).get('n_runs', 0)
    alpha = n_target / (n_target + 3.0)
    smoothed_fit = alpha * raw_fit + (1.0 - alpha) * 50.0
    smoothed_fit = max(0.0, min(100.0, smoothed_fit))

    mu_shift = (smoothed_fit - 50.0) / 50.0 * 0.08
    mu_shift = max(-0.08, min(0.08, mu_shift))

    scenario_perfs = {sc: shape_matrix[sc]['avg_position_pct'] for sc in shape_matrix}
    best_scenario = min(scenario_perfs, key=scenario_perfs.get) if scenario_perfs else 'genuine'
    worst_scenario = max(scenario_perfs, key=scenario_perfs.get) if scenario_perfs else 'genuine'

    confidence = min(1.0, runs_classified / 8.0)

    output_matrix = {}
    for sc in shape_matrix:
        output_matrix[sc] = {
            'avg_margin': shape_matrix[sc]['avg_margin'],
            'avg_position_pct': shape_matrix[sc]['avg_position_pct'],
            'n_runs': shape_matrix[sc]['n_runs'],
        }

    result = {
        'shape_fit_score': round(smoothed_fit, 2),
        'shape_mu_shift': round(mu_shift, 4),
        'shape_matrix': output_matrix,
        'best_scenario': best_scenario,
        'worst_scenario': worst_scenario,
        'runs_classified': runs_classified,
        'confidence': round(confidence, 2),
    }

    _horse_shape_cache[cache_key] = {'data': result, 'ts': time.time()}
    return result



_CLOSING_DEFAULTS = {
    'closing_rank_percentile': 50.0,
    'closing_rank_multiplier': 1.0,
    'elite_closer': False,
    'one_pace': False,
    'rank_history': [],
    'runs_ranked': 0,
    'avg_last_400m_speed': 0.0,
}

_CLOSING_RECENCY_WEIGHTS = [0.28, 0.22, 0.16, 0.12, 0.08, 0.06, 0.04, 0.04]


def compute_closing_rank(horse_name, track, distance_m):
    global _horse_closing_cache

    cache_key = f"{horse_name.lower()}|{track}|{distance_m}"
    if _cache_valid(_horse_closing_cache, cache_key):
        return _horse_closing_cache[cache_key]['data']

    result = dict(_CLOSING_DEFAULTS)

    conn = _get_conn()
    if not conn:
        return result

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT st.track, st.race_date, st.race_number,
                   st.horse_name, st.last_400m_speed
            FROM sectional_times st
            WHERE (st.track, st.race_date, st.race_number) IN (
                SELECT track, race_date, race_number
                FROM sectional_times
                WHERE LOWER(horse_name) = LOWER(%s) AND last_400m_speed > 0
                ORDER BY race_date DESC
                LIMIT 10
            )
            AND st.last_400m_speed > 0
        """, (horse_name,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        _dbg(f"Closing rank query error for {horse_name}: {e}")
        try:
            conn.close()
        except Exception:
            pass
        return result

    if not rows:
        return result

    races = {}
    for r in rows:
        rk = (r['track'], r['race_date'], r['race_number'])
        if rk not in races:
            races[rk] = []
        races[rk].append({
            'horse': r['horse_name'],
            'speed': float(r['last_400m_speed']),
        })

    horse_lower = horse_name.lower()
    rank_entries = []
    horse_speeds = []

    for rk, field in races.items():
        horse_speed = None
        for h in field:
            if h['horse'].lower() == horse_lower:
                horse_speed = h['speed']
                break
        if horse_speed is None:
            continue

        field_speeds = [h['speed'] for h in field]
        n_field = len(field_speeds)
        if n_field <= 1:
            continue

        rank_pct = sum(1 for s in field_speeds if horse_speed > s) / n_field * 100
        rank_entries.append(rank_pct)
        horse_speeds.append(horse_speed)

    if not rank_entries:
        return result

    rank_entries = rank_entries[:8]

    weights = _CLOSING_RECENCY_WEIGHTS[:len(rank_entries)]
    w_sum = sum(weights)
    weighted_pct = sum(r * w for r, w in zip(rank_entries, weights)) / w_sum if w_sum > 0 else 50.0

    last5 = rank_entries[:5]
    elite_closer = sum(1 for r in last5 if r > 80) >= 3 if len(last5) >= 3 else False
    one_pace = sum(1 for r in last5 if r < 30) >= 3 if len(last5) >= 3 else False

    multiplier = 0.7 + (weighted_pct / 100.0) * 0.6

    avg_speed = sum(horse_speeds) / len(horse_speeds) if horse_speeds else 0.0

    result = {
        'closing_rank_percentile': round(weighted_pct, 1),
        'closing_rank_multiplier': round(multiplier, 3),
        'elite_closer': elite_closer,
        'one_pace': one_pace,
        'rank_history': [round(r, 1) for r in rank_entries],
        'runs_ranked': len(rank_entries),
        'avg_last_400m_speed': round(avg_speed, 2),
    }

    _horse_closing_cache[cache_key] = {'data': result, 'ts': time.time()}
    return result



_TRIP_DEFAULTS = {
    'unlucky_score': 0.0,
    'unlucky_mu_boost': 0.0,
    'wide_running_cost': 0.0,
    'traffic_disruption_score': 0.0,
    'position_trajectory': [],
    'avg_position_pct': 0.5,
    'max_disruption': 0,
    'strong_finish_despite_trouble': False,
    'runs_analyzed': 0,
    'details': '',
}


def compute_trip_efficiency(horse_name):
    global _horse_trip_cache

    cache_key = horse_name.lower()
    if _cache_valid(_horse_trip_cache, cache_key):
        return _horse_trip_cache[cache_key]['data']

    result = dict(_TRIP_DEFAULTS)

    conn = _get_conn()
    if not conn:
        return result

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT track, race_date, race_number, splits_json,
                   last_400m_speed, avg_speed, last_200m_speed, distance_m
            FROM sectional_times
            WHERE LOWER(horse_name) = LOWER(%s)
              AND splits_json IS NOT NULL
            ORDER BY race_date DESC
            LIMIT 5
        """, (horse_name,))
        horse_rows = cur.fetchall()

        if not horse_rows:
            cur.close()
            conn.close()
            return result

        race_keys = [(r['track'], r['race_date'], r['race_number']) for r in horse_rows]
        field_data = {}
        for rk in race_keys:
            cur.execute("""
                SELECT COUNT(*) as field_size,
                       AVG(last_400m_speed) as avg_field_400
                FROM sectional_times
                WHERE track = %s AND race_date = %s AND race_number = %s
                  AND last_400m_speed > 0
            """, rk)
            fd = cur.fetchone()
            field_data[rk] = {
                'field_size': int(fd['field_size'] or 8),
                'avg_field_400': float(fd['avg_field_400'] or 0),
            }

        cur.close()
        conn.close()
    except Exception as e:
        _dbg(f"Trip efficiency query error for {horse_name}: {e}")
        try:
            conn.close()
        except Exception:
            pass
        return result

    details_parts = []
    best_unlucky = 0.0
    best_mu_boost = 0.0
    best_wide_cost = 0.0
    best_traffic = 0.0
    best_trajectory = []
    best_avg_pct = 0.5
    best_max_disrupt = 0
    strong_finish = False

    for idx, r in enumerate(horse_rows[:2]):
        splits = r.get('splits_json')
        if isinstance(splits, str):
            try:
                splits = json.loads(splits)
            except Exception:
                continue
        if not splits or not isinstance(splits, dict):
            continue

        rk = (r['track'], r['race_date'], r['race_number'])
        fd = field_data.get(rk, {'field_size': 8, 'avg_field_400': 0})
        field_size = max(fd['field_size'], 1)

        sorted_marks = sorted(splits.keys(), key=lambda k: int(k.replace('m', '')))
        positions = []
        for mk in sorted_marks:
            pos = splits[mk].get('position')
            if pos is not None:
                positions.append(int(pos))

        if not positions:
            continue

        avg_pos_pct = sum(p / field_size for p in positions) / len(positions)

        wide_running_cost = max(0.0, (avg_pos_pct - 0.5)) * 0.04

        position_changes = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
        max_backward = max(0, max(position_changes)) if position_changes else 0
        max_forward = max(0, -min(position_changes)) if position_changes else 0

        if max_backward >= 3:
            traffic_score = min(100.0, max_backward * 20.0)
        else:
            traffic_score = 0.0

        horse_400 = float(r.get('last_400m_speed') or 0)
        avg_field_400 = fd['avg_field_400']
        fsp_bonus = 0.0
        has_trouble = wide_running_cost > 0.005 or traffic_score > 0

        if horse_400 > 0 and avg_field_400 > 0 and horse_400 > avg_field_400 and has_trouble:
            strong_finish = True
            fsp_bonus = min(1.0, (horse_400 - avg_field_400) / avg_field_400 * 10)

        unlucky_score = (
            wide_running_cost * 40.0 * 100.0 +
            traffic_score * 0.35 +
            fsp_bonus * 25.0
        )
        unlucky_score = min(100.0, max(0.0, unlucky_score))

        if idx == 0:
            mu_boost = min(0.06, unlucky_score / 1000.0)
        else:
            mu_boost = min(0.03, unlucky_score / 2000.0)

        if idx == 0:
            best_unlucky = unlucky_score
            best_mu_boost = mu_boost
            best_wide_cost = wide_running_cost
            best_traffic = traffic_score
            best_trajectory = positions
            best_avg_pct = avg_pos_pct
            best_max_disrupt = max_backward
        else:
            best_mu_boost = max(best_mu_boost, best_mu_boost + mu_boost)

        run_label = "most recent" if idx == 0 else "2nd most recent"
        if unlucky_score > 10:
            parts = []
            if wide_running_cost > 0.005:
                parts.append(f"wide running (avg pos {avg_pos_pct:.0%})")
            if traffic_score > 0:
                parts.append(f"traffic disruption (dropped {max_backward} spots)")
            if strong_finish:
                parts.append("strong closing despite trouble")
            if parts:
                details_parts.append(f"{run_label}: {', '.join(parts)} [score={unlucky_score:.0f}]")

    result = {
        'unlucky_score': round(best_unlucky, 1),
        'unlucky_mu_boost': round(best_mu_boost, 4),
        'wide_running_cost': round(best_wide_cost, 4),
        'traffic_disruption_score': round(best_traffic, 1),
        'position_trajectory': best_trajectory,
        'avg_position_pct': round(best_avg_pct, 3),
        'max_disruption': best_max_disrupt,
        'strong_finish_despite_trouble': strong_finish,
        'runs_analyzed': len(horse_rows),
        'details': '; '.join(details_parts) if details_parts else 'No significant trip issues detected',
    }

    _horse_trip_cache[cache_key] = {'data': result, 'ts': time.time()}
    return result



def compute_all_quant_features(runners, race_data):
    global _all_quant_cache

    track = race_data.get('track', '')
    distance_m = int(race_data.get('distance_m', 1400))
    track_config = race_data.get('track_config', race_data.get('going', ''))
    predicted_pace = race_data.get('predicted_pace_scenario', 'genuine')
    race_date = race_data.get('race_date', '')
    race_number = race_data.get('race_number', 0)

    cache_key = f"{track}|{race_date}|{race_number}"
    if _cache_valid(_all_quant_cache, cache_key, ttl=SHORT_CACHE_TTL):
        return _all_quant_cache[cache_key]['data']

    # Single shared connection for Phase 2 primitives (λ, SVI, RSI, Z-scores)
    _shared_conn = _get_conn()

    per_horse = {}
    for r in runners:
        horse_name = r.get('horse_name', '')
        if not horse_name:
            continue

        horse_result = {}

        try:
            horse_result['sasr'] = compute_sasr_upgrade(horse_name, track, distance_m, track_config)
        except Exception as e:
            _dbg(f"SASR error for {horse_name}: {e}")
            horse_result['sasr'] = dict(_SASR_DEFAULTS)

        try:
            horse_result['shape_fit'] = compute_race_shape_fit(horse_name, predicted_pace, track, distance_m)
        except Exception as e:
            _dbg(f"Shape fit error for {horse_name}: {e}")
            horse_result['shape_fit'] = dict(_SHAPE_DEFAULTS)

        try:
            horse_result['closing_rank'] = compute_closing_rank(horse_name, track, distance_m)
        except Exception as e:
            _dbg(f"Closing rank error for {horse_name}: {e}")
            horse_result['closing_rank'] = dict(_CLOSING_DEFAULTS)

        try:
            horse_result['trip_efficiency'] = compute_trip_efficiency(horse_name)
        except Exception as e:
            _dbg(f"Trip efficiency error for {horse_name}: {e}")
            horse_result['trip_efficiency'] = dict(_TRIP_DEFAULTS)

        # --- Phase 2: λ, SVI, RSI, trip_cost from most recent sectional run ---
        horse_result['lambda_decay'] = None
        horse_result['svi'] = None
        horse_result['rsi'] = None
        horse_result['trip_cost_seconds'] = 0.0
        try:
            if _shared_conn:
                cur = _shared_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT splits_json, distance_m AS dist, avg_speed
                    FROM sectional_times
                    WHERE LOWER(horse_name) = LOWER(%s) AND splits_json IS NOT NULL
                    ORDER BY race_date DESC LIMIT 1
                """, (horse_name,))
                row = cur.fetchone()
                cur.close()
                if row and row.get('splits_json'):
                    splits = row['splits_json']
                    if isinstance(splits, str):
                        splits = json.loads(splits)
                    horse_result['lambda_decay'] = _compute_lambda_decay(splits)
                    horse_result['svi'] = _compute_svi(splits)
                    last_dist = row.get('dist') or distance_m
                    horse_result['rsi'] = _compute_rsi(splits, last_dist)
        except Exception as e:
            _dbg(f"Phase2 splits fetch error for {horse_name}: {e}")

        # Trip cost from barrier + current race distance
        try:
            barrier = r.get('barrier') or r.get('draw')
            avg_spd = r.get('avg_speed') or 14.5
            horse_result['trip_cost_seconds'] = _compute_trip_cost_seconds(
                barrier=int(barrier) if barrier else 1,
                distance_m=distance_m,
                avg_speed_ms=float(avg_spd)
            )
        except Exception as e:
            _dbg(f"Trip cost error for {horse_name}: {e}")

        # Phase 2: SVI modulation of Engine 4 closing_rank multiplier
        # SVI > 1.05 amplifies the closing signal; SVI < 0.95 dampens it
        try:
            svi = horse_result.get('svi')
            if svi is not None and 'closing_rank' in horse_result:
                cr = horse_result['closing_rank']
                base_mult = cr.get('closing_rank_multiplier', 1.0)
                # SVI=1.05 → +0.030 adj; SVI=0.95 → -0.030 adj (range ±0.15)
                svi_adj = max(-0.15, min(0.15, (float(svi) - 1.0) * 0.60))
                new_mult = max(0.50, min(1.50, base_mult + svi_adj))
                horse_result['closing_rank'] = {**cr, 'closing_rank_multiplier': round(new_mult, 3)}
        except Exception as e:
            _dbg(f"SVI closing_rank modulation error for {horse_name}: {e}")

        # Phase 2: Enhance Engine 5 unlucky_mu_boost with time-equivalent trip cost (Thorograph)
        # trip_cost_seconds already computed from barrier + distance above
        try:
            trip_cost_s = horse_result.get('trip_cost_seconds', 0.0)
            if trip_cost_s and trip_cost_s > 0 and 'trip_efficiency' in horse_result:
                te = horse_result['trip_efficiency']
                base_boost = te.get('unlucky_mu_boost', 0.0)
                # 0.5s → +0.023, 1.5s → +0.056, 2.0s → +0.07 (hard cap 0.10)
                trip_mu = min(0.07, float(trip_cost_s) * 0.045)
                new_boost = min(0.10, base_boost + trip_mu)
                horse_result['trip_efficiency'] = {**te, 'unlucky_mu_boost': round(new_boost, 4)}
        except Exception as e:
            _dbg(f"Trip cost mu_boost enhancement error for {horse_name}: {e}")

        per_horse[horse_name] = horse_result

    # --- Phase 2: Z-scores for this race's field (uses already-imported sectional data) ---
    field_z_scores = {}
    try:
        if _shared_conn and race_date and race_number:
            field_z_scores = _compute_z_scores_for_race(_shared_conn, track, race_date, race_number)
            # Merge z-scores into each horse's result
            for h_name, z_dict in field_z_scores.items():
                if h_name in per_horse:
                    per_horse[h_name].update(z_dict)
    except Exception as e:
        _dbg(f"Z-score computation error: {e}")

    if _shared_conn:
        try:
            _shared_conn.close()
        except Exception:
            pass

    field_level = {}
    try:
        field_level['pace_collapse'] = compute_pace_collapse(runners, distance_m, track)
    except Exception as e:
        _dbg(f"Pace collapse error: {e}")
        field_level['pace_collapse'] = dict(_COLLAPSE_DEFAULTS)

    output = {
        'per_horse': per_horse,
        'field_level': field_level,
    }

    _all_quant_cache[cache_key] = {'data': output, 'ts': time.time()}
    _dbg(f"Computed all quant features for {track} R{race_number}: {len(per_horse)} horses")
    return output



if __name__ == '__main__':
    _dbg("Running standalone test...")

    test_horse = 'Winx'
    if len(sys.argv) > 1:
        test_horse = sys.argv[1]

    _dbg(f"Testing with horse: {test_horse}")

    print("\n=== ENGINE 1: SASR Upgrade/Downgrade ===")
    sasr = compute_sasr_upgrade(test_horse, 'Randwick', 1200, 'Good 4')
    print(json.dumps(sasr, indent=2))

    print("\n=== ENGINE 2: Pace Collapse ===")
    test_runners = [
        {'horse_name': 'Horse A', 'running_style': 'leader', 'barrier': 1, 'early_speed_pct': 102.0},
        {'horse_name': 'Horse B', 'running_style': 'on_pace', 'barrier': 3, 'early_speed_pct': 101.5},
        {'horse_name': 'Horse C', 'running_style': 'leader', 'barrier': 2, 'early_speed_pct': 103.0},
        {'horse_name': 'Horse D', 'running_style': 'midfield', 'barrier': 6, 'early_speed_pct': 98.0},
        {'horse_name': 'Horse E', 'running_style': 'backmarker', 'barrier': 8, 'early_speed_pct': 95.0},
    ]
    collapse = compute_pace_collapse(test_runners, 1400, 'Randwick')
    print(json.dumps(collapse, indent=2))

    print("\n=== ENGINE 3: Race Shape Matching ===")
    shape = compute_race_shape_fit(test_horse, 'hot', 'Randwick', 1200)
    print(json.dumps(shape, indent=2))

    print("\n=== ENGINE 4: Closing Speed Percentile ===")
    closing = compute_closing_rank(test_horse, 'Randwick', 1200)
    print(json.dumps(closing, indent=2))

    print("\n=== ENGINE 5: Trip Efficiency ===")
    trip = compute_trip_efficiency(test_horse)
    print(json.dumps(trip, indent=2))

    print("\n=== BATCH: All Quant Features ===")
    batch_runners = [
        {'horse_name': test_horse, 'running_style': 'midfield', 'barrier': 5, 'early_speed_pct': 100.0},
    ]
    batch_race = {
        'track': 'Randwick', 'distance_m': 1200, 'track_config': 'Good 4',
        'predicted_pace_scenario': 'genuine', 'race_date': '2026-01-01', 'race_number': 5,
    }
    all_feats = compute_all_quant_features(batch_runners, batch_race)
    print(json.dumps(all_feats, indent=2, default=str))

    _dbg("Standalone test complete.")
