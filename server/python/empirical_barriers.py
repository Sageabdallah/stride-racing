#!/usr/bin/env python3
"""
Builds data-driven barrier advantage scores from historical race results.
Replaces theoretical barrier zone assumptions with actual track-specific win rates.
"""

import os
import sys
import time
import math

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

_barrier_cache = None
_barrier_cache_time = 0
CACHE_TTL = 1800


def get_db_connection():
    if not PSYCOPG2_AVAILABLE:
        return None
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return None
    try:
        return psycopg2.connect(database_url)
    except Exception as e:
        print(f"[EmpiricalBarriers] DB error: {e}", file=sys.stderr)
        return None


def _classify_distance(distance_m):
    if distance_m < 1200:
        return 'sprint'
    elif distance_m <= 1600:
        return 'mile'
    else:
        return 'staying'


def build_barrier_lookup():
    global _barrier_cache, _barrier_cache_time
    
    now = time.time()
    if _barrier_cache and (now - _barrier_cache_time) < CACHE_TTL:
        return _barrier_cache
    
    conn = get_db_connection()
    if not conn:
        return {}
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
              track,
              CASE WHEN distance_m < 1200 THEN 'sprint' 
                   WHEN distance_m <= 1600 THEN 'mile' 
                   ELSE 'staying' END as dist_cat,
              barrier,
              COUNT(*) as runners,
              COUNT(CASE WHEN position = 1 THEN 1 END) as wins,
              COUNT(CASE WHEN position <= 3 THEN 1 END) as places
            FROM race_results_history
            WHERE position IS NOT NULL AND barrier IS NOT NULL 
              AND barrier > 0 AND barrier <= 20
            GROUP BY 1, 2, 3
            HAVING COUNT(*) >= 10
            ORDER BY 1, 2, 3
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[EmpiricalBarriers] Query error: {e}", file=sys.stderr)
        try:
            conn.close()
        except:
            pass
        return {}
    
    lookup = {}
    for track, dist_cat, barrier, runners, wins, places in rows:
        if track not in lookup:
            lookup[track] = {}
        if dist_cat not in lookup[track]:
            lookup[track][dist_cat] = {'barriers': {}, 'total_runners': 0, 'total_wins': 0}
        
        lookup[track][dist_cat]['barriers'][barrier] = {
            'win_pct': round(wins / runners * 100, 2) if runners > 0 else 0,
            'place_pct': round(places / runners * 100, 2) if runners > 0 else 0,
            'sample_size': runners
        }
        lookup[track][dist_cat]['total_runners'] += runners
        lookup[track][dist_cat]['total_wins'] += wins
    
    for track in lookup:
        for dist_cat in lookup[track]:
            td = lookup[track][dist_cat]
            td['avg_win_pct'] = round(td['total_wins'] / td['total_runners'] * 100, 2) if td['total_runners'] > 0 else 10.0
    
    _barrier_cache = lookup
    _barrier_cache_time = now
    print(f"[EmpiricalBarriers] Built lookup: {len(lookup)} tracks", file=sys.stderr)
    return lookup


def get_empirical_barrier_advantage(track, barrier, distance_m):
    """
    Get data-driven barrier advantage score.

    Returns dict with:
    - advantage_score: -1.0 to +1.0 (0 = neutral, positive = better than avg)
    - win_pct: actual win percentage for this barrier
    - avg_win_pct: average win percentage for the track/distance
    - confidence: 'high' if sample >= 50, 'medium' if >= 25, 'low' if < 25
    - description: human-readable description
    """
    barrier = int(barrier) if barrier else 0
    distance_m = int(distance_m) if distance_m else 1400
    
    if barrier <= 0 or barrier > 20:
        return {
            'advantage_score': 0.0,
            'win_pct': 0,
            'avg_win_pct': 10.0,
            'confidence': 'low',
            'description': 'Invalid barrier'
        }
    
    lookup = build_barrier_lookup()
    dist_cat = _classify_distance(distance_m)
    
    track_data = lookup.get(track, {}).get(dist_cat)
    
    if not track_data or barrier not in track_data.get('barriers', {}):
        if dist_cat == 'sprint':
            ideal = [2, 3, 4, 5, 6]
            penalty_start = 10
        elif dist_cat == 'mile':
            ideal = [3, 4, 5, 6, 7]
            penalty_start = 12
        else:
            ideal = [1, 2, 3, 4, 5, 6, 7, 8]
            penalty_start = 14
        
        if barrier in ideal:
            score = 0.15
            desc = f"Favourable barrier {barrier} for {dist_cat} (generic)"
        elif barrier >= penalty_start:
            score = -0.15 - (barrier - penalty_start) * 0.05
            desc = f"Wide barrier {barrier} disadvantage for {dist_cat}"
        else:
            score = 0.0
            desc = f"Neutral barrier {barrier} for {dist_cat}"
        
        return {
            'advantage_score': round(max(-1.0, min(1.0, score)), 3),
            'win_pct': 0,
            'avg_win_pct': 10.0,
            'confidence': 'low',
            'description': desc
        }
    
    barrier_data = track_data['barriers'][barrier]
    win_pct = barrier_data['win_pct']
    avg_win_pct = track_data['avg_win_pct']
    sample = barrier_data['sample_size']
    
    if avg_win_pct > 0:
        ratio = win_pct / avg_win_pct
        advantage = (ratio - 1.0)
        advantage = max(-0.5, min(0.5, advantage))
    else:
        advantage = 0.0
    
    confidence = 'high' if sample >= 50 else ('medium' if sample >= 25 else 'low')
    
    if advantage > 0.1:
        desc = f"Strong barrier {barrier} at {track} ({dist_cat}): {win_pct}% win rate vs {avg_win_pct}% avg"
    elif advantage < -0.1:
        desc = f"Weak barrier {barrier} at {track} ({dist_cat}): {win_pct}% win rate vs {avg_win_pct}% avg"
    else:
        desc = f"Neutral barrier {barrier} at {track} ({dist_cat}): {win_pct}% win rate"
    
    return {
        'advantage_score': round(advantage, 3),
        'win_pct': win_pct,
        'avg_win_pct': avg_win_pct,
        'confidence': confidence,
        'description': desc
    }
