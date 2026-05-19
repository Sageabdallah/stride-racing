#!/usr/bin/env python3
"""
Replaces static elite jockey/trainer lists with data-driven momentum scoring.

Tracks rolling 30-day form with recency weighting, course-specific performance,
hot/cold streak detection, and returns momentum scores (0-100 scale).

Used by mc_api.py to dynamically identify top-performing jockeys and trainers
instead of relying on hardcoded name lists.
"""

import os
import sys
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


def _log(message: str):
    print(f"[JockeyMomentum] {message}", file=sys.stderr)


def _neutral_jockey_result(jockey_name: str, reason: str = "insufficient data") -> Dict[str, Any]:
    return {
        'jockey': jockey_name,
        'momentum_score': 50,
        'is_hot_streak': False,
        'is_cold_streak': False,
        'recent_wins': 0,
        'recent_rides': 0,
        'strike_rate_30d': 0.0,
        'course_strike_rate': 0.0,
        'momentum_adjustment': 1.0,
        'momentum_insight': f"Neutral momentum ({reason})",
    }


def _neutral_trainer_result(trainer_name: str, reason: str = "insufficient data") -> Dict[str, Any]:
    return {
        'trainer': trainer_name,
        'momentum_score': 50,
        'is_hot_streak': False,
        'is_cold_streak': False,
        'recent_wins': 0,
        'recent_rides': 0,
        'strike_rate_30d': 0.0,
        'course_strike_rate': 0.0,
        'momentum_adjustment': 1.0,
        'momentum_insight': f"Neutral momentum ({reason})",
    }


def _get_connection():
    if not HAS_PSYCOPG2:
        return None
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return None
    try:
        return psycopg2.connect(database_url)
    except Exception as e:
        _log(f"DB connection error: {e}")
        return None


def _parse_race_date(race_date: str) -> Optional[datetime]:
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(race_date.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _calculate_momentum_score(
    wins_7d: int,
    rides_7d: int,
    wins_14d: int,
    rides_14d: int,
    wins_30d: int,
    rides_30d: int,
    course_wins: int,
    course_rides: int,
) -> Dict[str, Any]:
    strike_rate_30d = (wins_30d / rides_30d * 100) if rides_30d > 0 else 0.0
    course_strike_rate = (course_wins / course_rides * 100) if course_rides > 0 else 0.0

    weighted_wins = (wins_7d * 3) + ((wins_14d - wins_7d) * 2) + ((wins_30d - wins_14d) * 1)
    weighted_rides = (rides_7d * 3) + ((rides_14d - rides_7d) * 2) + ((rides_30d - rides_14d) * 1)
    weighted_strike_rate = (weighted_wins / weighted_rides * 100) if weighted_rides > 0 else 0.0

    base_score = weighted_strike_rate

    course_bonus = 10.0 if course_strike_rate > 20.0 else 0.0

    is_hot_streak = wins_14d >= 3
    is_cold_streak = rides_14d >= 10 and wins_14d == 0
    streak_bonus = 0.0
    if is_hot_streak:
        streak_bonus = 15.0
    elif is_cold_streak:
        streak_bonus = -15.0

    raw_score = base_score + course_bonus + streak_bonus

    momentum_score = max(0.0, min(100.0, raw_score))

    if momentum_score >= 65:
        adjustment = 1.05 + (momentum_score - 65) * (0.15 / 35)
        adjustment = min(adjustment, 1.20)
    elif momentum_score <= 35:
        adjustment = 1.0 - (35 - momentum_score) * (0.15 / 35)
        adjustment = max(adjustment, 0.85)
    else:
        adjustment = 1.0

    return {
        'momentum_score': round(momentum_score, 1),
        'is_hot_streak': is_hot_streak,
        'is_cold_streak': is_cold_streak,
        'recent_wins': wins_30d,
        'recent_rides': rides_30d,
        'strike_rate_30d': round(strike_rate_30d, 2),
        'course_strike_rate': round(course_strike_rate, 2),
        'momentum_adjustment': round(adjustment, 3),
    }


def _build_insight(name: str, data: Dict[str, Any], role: str = "Jockey") -> str:
    score = data['momentum_score']
    sr = data['strike_rate_30d']
    wins = data['recent_wins']
    rides = data['recent_rides']

    if data['is_hot_streak']:
        return (
            f"{role} {name} is on a HOT STREAK "
            f"({wins} wins from {rides} rides, {sr:.1f}% SR, momentum {score:.0f})"
        )
    elif data['is_cold_streak']:
        return (
            f"{role} {name} is on a COLD STREAK "
            f"(0 wins from {rides} rides in 14 days, momentum {score:.0f})"
        )
    elif score >= 65:
        return (
            f"{role} {name} in strong form "
            f"({wins}W from {rides}R, {sr:.1f}% SR, momentum {score:.0f})"
        )
    elif score <= 35:
        return (
            f"{role} {name} in poor form "
            f"({wins}W from {rides}R, {sr:.1f}% SR, momentum {score:.0f})"
        )
    else:
        return (
            f"{role} {name} in steady form "
            f"({wins}W from {rides}R, {sr:.1f}% SR, momentum {score:.0f})"
        )


class JockeyMomentumTracker:
    """Tracks jockey and trainer momentum from the training_data table."""

    def __init__(self):
        self._conn = None

    def _ensure_connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = _get_connection()
        return self._conn

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _query_person_stats(
        self,
        person_name: str,
        column: str,
        track: str,
        race_date_str: str,
    ) -> Optional[Dict[str, Any]]:
        conn = self._ensure_connection()
        if not conn:
            return None

        ref_date = _parse_race_date(race_date_str)
        if not ref_date:
            return None

        date_30 = (ref_date - timedelta(days=30)).strftime('%Y-%m-%d')
        date_14 = (ref_date - timedelta(days=14)).strftime('%Y-%m-%d')
        date_7 = (ref_date - timedelta(days=7)).strftime('%Y-%m-%d')
        ref_date_s = ref_date.strftime('%Y-%m-%d')

        try:
            cursor = conn.cursor()

            cursor.execute(f"""
                SELECT
                    COUNT(*) AS rides_30d,
                    COALESCE(SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END), 0) AS wins_30d
                FROM training_data
                WHERE LOWER({column}) = LOWER(%s)
                  AND race_date >= %s
                  AND race_date < %s
                  AND {column} IS NOT NULL
            """, (person_name, date_30, ref_date_s))
            row_30 = cursor.fetchone()

            cursor.execute(f"""
                SELECT
                    COUNT(*) AS rides_14d,
                    COALESCE(SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END), 0) AS wins_14d
                FROM training_data
                WHERE LOWER({column}) = LOWER(%s)
                  AND race_date >= %s
                  AND race_date < %s
                  AND {column} IS NOT NULL
            """, (person_name, date_14, ref_date_s))
            row_14 = cursor.fetchone()

            cursor.execute(f"""
                SELECT
                    COUNT(*) AS rides_7d,
                    COALESCE(SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END), 0) AS wins_7d
                FROM training_data
                WHERE LOWER({column}) = LOWER(%s)
                  AND race_date >= %s
                  AND race_date < %s
                  AND {column} IS NOT NULL
            """, (person_name, date_7, ref_date_s))
            row_7 = cursor.fetchone()

            cursor.execute(f"""
                SELECT
                    COUNT(*) AS course_rides,
                    COALESCE(SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END), 0) AS course_wins
                FROM training_data
                WHERE LOWER({column}) = LOWER(%s)
                  AND LOWER(track) = LOWER(%s)
                  AND race_date >= %s
                  AND race_date < %s
                  AND {column} IS NOT NULL
            """, (person_name, track, date_30, ref_date_s))
            row_course = cursor.fetchone()

            cursor.close()

            return {
                'rides_30d': row_30[0] if row_30 else 0,
                'wins_30d': row_30[1] if row_30 else 0,
                'rides_14d': row_14[0] if row_14 else 0,
                'wins_14d': row_14[1] if row_14 else 0,
                'rides_7d': row_7[0] if row_7 else 0,
                'wins_7d': row_7[1] if row_7 else 0,
                'course_rides': row_course[0] if row_course else 0,
                'course_wins': row_course[1] if row_course else 0,
            }

        except Exception as e:
            _log(f"Error querying {column} stats for {person_name}: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return None

    def score_jockey(self, jockey_name: str, track: str, race_date: str) -> Dict[str, Any]:
        if not jockey_name or not jockey_name.strip():
            return _neutral_jockey_result(jockey_name or '', 'no jockey name')

        stats = self._query_person_stats(jockey_name.strip(), 'jockey', track, race_date)
        if stats is None:
            return _neutral_jockey_result(jockey_name, 'database unavailable')

        if stats['rides_30d'] < 5:
            result = _neutral_jockey_result(jockey_name, 'fewer than 5 rides in 30 days')
            result['recent_rides'] = stats['rides_30d']
            result['recent_wins'] = stats['wins_30d']
            return result

        data = _calculate_momentum_score(
            wins_7d=stats['wins_7d'],
            rides_7d=stats['rides_7d'],
            wins_14d=stats['wins_14d'],
            rides_14d=stats['rides_14d'],
            wins_30d=stats['wins_30d'],
            rides_30d=stats['rides_30d'],
            course_wins=stats['course_wins'],
            course_rides=stats['course_rides'],
        )
        data['jockey'] = jockey_name.strip()
        data['momentum_insight'] = _build_insight(jockey_name.strip(), data, "Jockey")
        return data

    def score_trainer(self, trainer_name: str, track: str, race_date: str) -> Dict[str, Any]:
        if not trainer_name or not trainer_name.strip():
            return _neutral_trainer_result(trainer_name or '', 'no trainer name')

        stats = self._query_person_stats(trainer_name.strip(), 'trainer', track, race_date)
        if stats is None:
            return _neutral_trainer_result(trainer_name, 'database unavailable')

        if stats['rides_30d'] < 5:
            result = _neutral_trainer_result(trainer_name, 'fewer than 5 rides in 30 days')
            result['recent_rides'] = stats['rides_30d']
            result['recent_wins'] = stats['wins_30d']
            return result

        data = _calculate_momentum_score(
            wins_7d=stats['wins_7d'],
            rides_7d=stats['rides_7d'],
            wins_14d=stats['wins_14d'],
            rides_14d=stats['rides_14d'],
            wins_30d=stats['wins_30d'],
            rides_30d=stats['rides_30d'],
            course_wins=stats['course_wins'],
            course_rides=stats['course_rides'],
        )
        data['trainer'] = trainer_name.strip()
        data['momentum_insight'] = _build_insight(trainer_name.strip(), data, "Trainer")
        return data

    def get_elite_jockeys_dynamic(
        self, track: str, race_date: str, top_n: int = 15
    ) -> List[str]:
        conn = self._ensure_connection()
        if not conn:
            _log("DB unavailable for dynamic elite jockeys, returning empty list")
            return []

        ref_date = _parse_race_date(race_date)
        if not ref_date:
            return []

        date_30 = (ref_date - timedelta(days=30)).strftime('%Y-%m-%d')
        date_14 = (ref_date - timedelta(days=14)).strftime('%Y-%m-%d')
        date_7 = (ref_date - timedelta(days=7)).strftime('%Y-%m-%d')
        ref_date_s = ref_date.strftime('%Y-%m-%d')

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    jockey,
                    COUNT(*) AS rides_30d,
                    COALESCE(SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END), 0) AS wins_30d,
                    COALESCE(SUM(CASE WHEN race_date >= %s AND won = 1 THEN 1 ELSE 0 END), 0) AS wins_14d,
                    COALESCE(SUM(CASE WHEN race_date >= %s THEN 1 ELSE 0 END), 0) AS rides_14d,
                    COALESCE(SUM(CASE WHEN race_date >= %s AND won = 1 THEN 1 ELSE 0 END), 0) AS wins_7d,
                    COALESCE(SUM(CASE WHEN race_date >= %s THEN 1 ELSE 0 END), 0) AS rides_7d
                FROM training_data
                WHERE jockey IS NOT NULL
                  AND jockey != ''
                  AND race_date >= %s
                  AND race_date < %s
                GROUP BY jockey
                HAVING COUNT(*) >= 5
                ORDER BY
                    (COALESCE(SUM(CASE WHEN race_date >= %s AND won = 1 THEN 1 ELSE 0 END), 0) * 3
                     + COALESCE(SUM(CASE WHEN race_date >= %s AND race_date < %s AND won = 1 THEN 1 ELSE 0 END), 0) * 2
                     + COALESCE(SUM(CASE WHEN race_date < %s AND won = 1 THEN 1 ELSE 0 END), 0) * 1
                    )::float
                    / GREATEST(
                        (COALESCE(SUM(CASE WHEN race_date >= %s THEN 1 ELSE 0 END), 0) * 3
                         + COALESCE(SUM(CASE WHEN race_date >= %s AND race_date < %s THEN 1 ELSE 0 END), 0) * 2
                         + COALESCE(SUM(CASE WHEN race_date < %s THEN 1 ELSE 0 END), 0) * 1
                        ), 1
                    ) DESC
                LIMIT %s
            """, (
                date_14, date_14, date_7, date_7,
                date_30, ref_date_s,
                date_7,
                date_7, date_14,
                date_14,
                date_7,
                date_7, date_14,
                date_14,
                top_n,
            ))

            rows = cursor.fetchall()
            cursor.close()

            return [row[0] for row in rows if row[0]]

        except Exception as e:
            _log(f"Error fetching dynamic elite jockeys: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return []


_tracker_instance: Optional[JockeyMomentumTracker] = None


def _get_tracker() -> JockeyMomentumTracker:
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = JockeyMomentumTracker()
    return _tracker_instance


def score_jockey_momentum(jockey_name: str, track: str, race_date: str) -> Dict[str, Any]:
    try:
        tracker = _get_tracker()
        return tracker.score_jockey(jockey_name, track, race_date)
    except Exception as e:
        _log(f"Error scoring jockey momentum for {jockey_name}: {e}")
        return _neutral_jockey_result(jockey_name, 'error')


def score_trainer_momentum(trainer_name: str, track: str, race_date: str) -> Dict[str, Any]:
    try:
        tracker = _get_tracker()
        return tracker.score_trainer(trainer_name, track, race_date)
    except Exception as e:
        _log(f"Error scoring trainer momentum for {trainer_name}: {e}")
        return _neutral_trainer_result(trainer_name, 'error')


def get_elite_jockeys_dynamic(track: str, race_date: str, top_n: int = 15) -> List[str]:
    try:
        tracker = _get_tracker()
        return tracker.get_elite_jockeys_dynamic(track, race_date, top_n)
    except Exception as e:
        _log(f"Error getting dynamic elite jockeys: {e}")
        return []
