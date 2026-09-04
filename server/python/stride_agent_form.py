#!/usr/bin/env python3
"""
STRIDE Codex Agent 2 — Form & Horse Intelligence
Produces: form_franking.json, prep_cycles.json,
          sectional_trends.json, trainer_patterns.json
"""

import sys
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from intelligence_common import (
    load_todays_runners, json_write,
    INTELLIGENCE_DIR, CONN_ERRORS, ReconnectingRunner,
)


def _compute_franking_thresholds(franking_data):
    """Compute dynamic score thresholds from the current runner batch."""
    scores = [
        float(data.get("franking_score"))
        for data in franking_data.values()
        if data.get("franking_score") is not None
    ]
    if not scores:
        return {
            "deep_franked_score": 70.0,
            "franked_score": 55.0,
            "partial_score": 35.0,
            "confidence_deep": 0.45,
            "confidence_franked": 0.25,
            "pending_confidence": 0.08,
            "computed_from_n_horses": 0,
        }

    return {
        "deep_franked_score": float(np.percentile(scores, 95)),
        "franked_score": float(np.percentile(scores, 75)),
        "partial_score": float(np.percentile(scores, 25)),
        "confidence_deep": 0.45,
        "confidence_franked": 0.25,
        "pending_confidence": 0.08,
        "computed_from_n_horses": len(scores),
    }


def _classify_franking(data, thresholds):
    """Classify franking score into STRIDE tiers and assign weight."""
    score = data.get("franking_score", 50.0)
    confidence = data.get("franking_confidence", 0.1)
    anti = data.get("anti_franked", False)

    if anti:
        return "UNFRANKED", 0.6
    if confidence < thresholds["pending_confidence"]:
        return "PENDING", 0.8
    if score >= thresholds["deep_franked_score"] and confidence >= thresholds["confidence_deep"]:
        return "DEEP_FRANKED", 1.2
    if score >= thresholds["franked_score"] and confidence >= thresholds["confidence_franked"]:
        return "FRANKED", 1.1
    if score >= thresholds["partial_score"]:
        return "PARTIAL", 1.0
    return "UNFRANKED", 0.7


def _build_graph_profiles(horse_ids):
    """
    Compute graph-engine franking profiles for a batch of horse IDs.

    Returns dict {horse_id: profile} or empty dict on failure.
    The graph is expensive to build so this is wrapped in a try/except
    to ensure the pipeline continues if NetworkX or the DB is unavailable.
    """
    try:
        from franking_graph import get_franking_for_field
        t0 = time.time()
        profiles = get_franking_for_field(horse_ids)
        elapsed = time.time() - t0
        print(f"  Graph franking: {len(profiles)} profiles in {elapsed:.1f}s", file=sys.stderr)
        return profiles
    except Exception as e:
        print(f"  Graph franking unavailable: {e}", file=sys.stderr)
        return {}


def build_form_franking(today, conn):
    """Build form_franking.json — franking classification for each today's runner."""
    from form_franking import get_franking_for_runners

    horse_ids = today["horse_ids"]
    if not horse_ids:
        return {}

    franking_data = get_franking_for_runners(horse_ids)
    thresholds = _compute_franking_thresholds(franking_data)

    graph_profiles = _build_graph_profiles(horse_ids)

    horses = {}
    for hid, data in franking_data.items():
        classification, weight = _classify_franking(data, thresholds)
        fc = data.get("franking_class", {})
        gp = graph_profiles.get(hid, {})
        horses[hid] = {
            "horse_name": data.get("horse_name", ""),
            "classification": classification,
            "franking_weight": weight,
            "global_elo": round(data.get("global_elo", 50.0), 2),
            "franking_score": round(data.get("franking_score", 50.0), 2),
            "franking_confidence": round(data.get("franking_confidence", 0.1), 3),
            "form_quality_trend": round(data.get("form_quality_trend", 0.0), 3),
            "anti_franked": data.get("anti_franked", False),
            "field_strength_avg": round(data.get("field_strength_avg", 50.0), 2),
            "collateral_advantage": round(data.get("collateral_advantage", 0.0), 3),
            "franking_class": {
                "reference_class": fc.get("reference_class", "Unknown"),
                "reference_class_weight": fc.get("reference_class_weight", 1.0),
                "group_listed_in_chain": fc.get("group_listed_in_chain", False),
                "recursive_class_avg": fc.get("recursive_class_avg", 1.0),
            },
            "graph_franking_score": round(gp.get("franking_score", 0.0), 4),
            "graph_franking_depth": gp.get("franking_depth", 0),
            "graph_franking_independence": gp.get("franking_independence", 0),
            "pagerank_authority": round(gp.get("pagerank_authority", 0.5), 4),
            "community_strength": round(gp.get("community_strength", 0.5), 4),
            "form_stability": round(gp.get("form_stability", 0.5), 4),
            "bridge_score": round(gp.get("bridge_score", 0.0), 4),
            "market_validated_franking": gp.get("market_validated", False),
            "excuse_adjusted_anti_franks": gp.get("excuse_adjusted_anti_franks", 0),
        }

    return {
        "franking_thresholds": {
            "deep_franked_score": round(thresholds["deep_franked_score"], 3),
            "franked_score": round(thresholds["franked_score"], 3),
            "partial_score": round(thresholds["partial_score"], 3),
            "confidence_deep": thresholds["confidence_deep"],
            "confidence_franked": thresholds["confidence_franked"],
            "pending_confidence": thresholds["pending_confidence"],
            "computed_from_n_horses": thresholds["computed_from_n_horses"],
        },
        "horses": horses,
    }


def _map_prep_position(runs_this_prep):
    """Map run count to STRIDE prep position label."""
    labels = {1: "first_up", 2: "second_up", 3: "third_up",
              4: "fourth_up", 5: "fifth_up"}
    return labels.get(runs_this_prep, f"{runs_this_prep}th_up")


def _map_trajectory(trajectory_str):
    """Map fitness_peak trajectory to STRIDE enum."""
    t = (trajectory_str or "steady").lower()
    if t == "improving":
        return "IMPROVING"
    if t in ("declining", "peaked"):
        return "DECLINING"
    return "FLAT"


def _is_due_to_win(positions):
    """Check if horse is 'due' — 3+ runs this prep, ≥2 top-4 finishes, no wins."""
    if len(positions) < 3:
        return False
    if 1 in positions:
        return False
    close_up = sum(1 for p in positions[-3:] if p and p <= 4)
    return close_up >= 2


def _iter_batches(items, batch_size=50):
    """Yield fixed-size batches while preserving original order."""
    for start in range(0, len(items), batch_size):
        yield items[start:start + batch_size]


def _batch_prefetch_fitness_data(horse_names, race_date, conn):
    """
    Batch-fetch race history for a horse chunk, prime fitness caches, and
    monkey-patch fitness_peak._fetch_race_history to serve that batch from memory.

    Returns the original fetch function so the caller can restore it after the batch.
    """
    import fitness_peak
    from result_margins import beaten_margin

    original_fetch = fitness_peak._fetch_race_history
    if not horse_names:
        return original_fetch

    t0 = time.time()
    cur = conn.cursor()

    all_history = {}

    cur.execute("""
        SELECT horse_name, race_date, actual_position, NULL as margin, distance, race_class
        FROM training_data
        WHERE horse_name = ANY(%s) AND race_date < %s
          AND (race_class IS NULL OR (
              LOWER(race_class) NOT LIKE '%%jump%%'
              AND LOWER(race_class) NOT LIKE '%%trial%%'
              AND LOWER(race_class) NOT LIKE '%%jumpout%%'))
        ORDER BY horse_name, race_date ASC
    """, (horse_names, race_date))

    for hname, rd, pos_raw, margin, dist, rc in cur.fetchall():
        if not rd:
            continue
        if hname not in all_history:
            all_history[hname] = {}
        if rd not in all_history[hname]:
            pos = None
            if pos_raw is not None:
                try:
                    pos = int(pos_raw)
                except (ValueError, TypeError):
                    pass
            all_history[hname][rd] = {
                'race_date': rd, 'position': pos,
                'margin': None, 'distance': dist, 'race_class': rc,
            }

    # Query 2: race_results_history (same filters as fitness_peak lines 90-99)
    # Use LOWER matching like the original
    names_lower = [n.lower() for n in horse_names]
    name_map = {n.lower(): n for n in horse_names}

    cur.execute("""
        SELECT LOWER(horse_name) as hn, race_date, position, margin_lengths, distance_m, race_class
        FROM race_results_history
        WHERE LOWER(horse_name) = ANY(%s) AND race_date < %s
          AND (race_class IS NULL OR (
              LOWER(race_class) NOT LIKE '%%jump%%'
              AND LOWER(race_class) NOT LIKE '%%trial%%'
              AND LOWER(race_class) NOT LIKE '%%jumpout%%'))
        ORDER BY LOWER(horse_name), race_date ASC
    """, (names_lower, race_date))

    for hn_lower, rd, pos_raw, margin_raw, dist, rc in cur.fetchall():
        if not rd:
            continue
        hname = name_map.get(hn_lower, hn_lower)
        if hname not in all_history:
            all_history[hname] = {}

        if rd not in all_history[hname]:
            pos = None
            if pos_raw is not None:
                try:
                    pos = int(pos_raw)
                except (ValueError, TypeError):
                    pass
            # Lengths behind the winner, None for the winner, whichever
            # importer wrote the row (result_margins). This is the prep-cycle
            # path the pipeline runs; the intelligence/ copy #157 fixed is
            # not imported by anything.
            margin = beaten_margin(pos, margin_raw)
            all_history[hname][rd] = {
                'race_date': rd, 'position': pos,
                'margin': margin, 'distance': dist, 'race_class': rc,
            }
        elif all_history[hname][rd]['margin'] is None and margin_raw is not None:
            # A winner training_data left blank must stay blank: filling it
            # from the duplicate row would put the winning margin back.
            known = all_history[hname][rd]
            position = known['position'] if known['position'] is not None else pos_raw
            known['margin'] = beaten_margin(position, margin_raw)

    cur.close()

    prefetched = {}
    for hname, date_dict in all_history.items():
        prefetched[hname] = sorted(date_dict.values(), key=lambda x: x['race_date'])

    for hname, runs in prefetched.items():
        if hname in fitness_peak._fitness_profile_cache:
            continue
        preps = fitness_peak._detect_preparations(runs)
        run_stats = defaultdict(lambda: {'wins': 0, 'places': 0, 'starts': 0})
        for prep in preps:
            for r in prep:
                rn = r['run_number']
                key = rn if rn <= 5 else 6
                run_stats[key]['starts'] += 1
                pos = r.get('position')
                if pos is not None:
                    if pos == 1:
                        run_stats[key]['wins'] += 1
                        run_stats[key]['places'] += 1
                    elif pos <= 3:
                        run_stats[key]['places'] += 1

        win_rates = {}
        place_rates = {}
        for rn in range(1, 7):
            s = run_stats[rn]
            if s['starts'] > 0:
                win_rates[rn] = round(s['wins'] / s['starts'] * 100, 1)
                place_rates[rn] = round(s['places'] / s['starts'] * 100, 1)
            else:
                win_rates[rn] = 0.0
                place_rates[rn] = 0.0

        peak_run = 2
        peak_winrate = 0.0
        for rn in range(1, 7):
            s = run_stats[rn]
            if s['starts'] >= 2:
                wr = s['wins'] / s['starts'] * 100
                if wr > peak_winrate:
                    peak_winrate = wr
                    peak_run = rn

        prep_lengths = [len(p) for p in preps]
        career_avg_prep = sum(prep_lengths) / len(prep_lengths) if prep_lengths else 3.0

        first_up_wr = win_rates.get(1, 0)
        second_up_wr = win_rates.get(2, 0)
        third_up_wr = win_rates.get(3, 0)
        fourth_plus_wr = max(win_rates.get(4, 0), win_rates.get(5, 0), win_rates.get(6, 0))

        profile = {
            'win_rates_by_run': win_rates,
            'place_rates_by_run': place_rates,
            'peak_run_number': peak_run,
            'peak_run_winrate': round(peak_winrate, 1),
            'career_avg_prep_length': round(career_avg_prep, 1),
            'total_preps': len(preps),
            'pattern_flags': {
                'is_first_up_specialist': (first_up_wr > 15 and first_up_wr > second_up_wr and first_up_wr > third_up_wr),
                'is_third_up_peaker': (third_up_wr > 0 and first_up_wr > 0 and third_up_wr > 2 * first_up_wr),
                'is_campaign_horse': (fourth_plus_wr > first_up_wr and fourth_plus_wr > second_up_wr),
                'is_improver': (second_up_wr > first_up_wr * 1.5 and run_stats[2]['starts'] >= 2),
            },
        }
        fitness_peak._fitness_profile_cache[hname] = profile

    def _cached_fetch(horse_name, race_date_arg, conn_arg):
        if horse_name in prefetched:
            return prefetched[horse_name]
        return original_fetch(horse_name, race_date_arg, conn_arg)

    fitness_peak._fetch_race_history = _cached_fetch

    elapsed = time.time() - t0
    print(
        f"  [BATCH] Prefetched race history for {len(prefetched)} horses in {elapsed:.1f}s",
        file=sys.stderr,
    )
    return original_fetch


def _load_horse_prep_profiles(conn, horse_ids):
    """
    Load horse_prep_profiles from DB for given horse_ids.
    Returns dict keyed by (horse_id, distance_band, run_number) -> {starts, wins, places, avg_pve}.
    """
    if not horse_ids:
        return {}
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT horse_id, distance_band, run_number, starts, wins, places, avg_pve
            FROM horse_prep_profiles
            WHERE horse_id = ANY(%s)
        """, (list(horse_ids),))
        profiles = {}
        for row in cur.fetchall():
            key = (row[0], row[1], row[2])
            profiles[key] = {
                'starts': row[3] or 0,
                'wins': row[4] or 0,
                'places': row[5] or 0,
                'avg_pve': float(row[6]) if row[6] is not None else None,
            }
        cur.close()
        return profiles
    except CONN_ERRORS:
        # A dead socket must reach the step retry — swallowing it here left
        # the build to die at the next cursor() with no redial (2026-08-08).
        raise
    except Exception as e:
        print(f"  [PREP] horse_prep_profiles table not available: {e}", file=sys.stderr)
        return {}


def _fetch_weight_track_history(conn, horse_names, race_date):
    """
    Fetch weight_kg and track per run for horses.
    Returns dict: horse_name_lower -> list of {race_date, weight_kg, track, distance_m}.
    """
    if not horse_names:
        return {}
    names_lower = [n.lower() for n in horse_names]
    cur = conn.cursor()
    cur.execute("""
        SELECT LOWER(horse_name), race_date, weight_kg, track, distance_m
        FROM race_results_history
        WHERE LOWER(horse_name) = ANY(%s) AND race_date < %s
          AND (race_class IS NULL OR (
              LOWER(race_class) NOT LIKE '%%jump%%'
              AND LOWER(race_class) NOT LIKE '%%trial%%'
              AND LOWER(race_class) NOT LIKE '%%jumpout%%'))
        ORDER BY LOWER(horse_name), race_date::date ASC
    """, (names_lower, race_date))
    result = defaultdict(list)
    for hn_lower, rd, wt, track, dist in cur.fetchall():
        result[hn_lower].append({
            'race_date': rd,
            'weight_kg': float(wt) if wt is not None else None,
            'track': track or '',
            'distance_m': int(dist) if dist is not None else None,
        })
    cur.close()
    return dict(result)


def _classify_distance_band(distance_m):
    """Classify distance into sprint/middle/staying."""
    if distance_m is None:
        return "middle"
    d = int(distance_m)
    if d < 1200:
        return "sprint"
    if d <= 1600:
        return "middle"
    return "staying"


def _compute_track_specific_peak(track_runs, horse_name):
    """
    Compute peak run number at a specific track from run history.
    Returns peak run number or None if fewer than 3 runs.
    """
    if len(track_runs) < 3:
        return None

    import fitness_peak
    preps = fitness_peak._detect_preparations(track_runs)
    if not preps:
        return None

    win_count = defaultdict(int)
    place_count = defaultdict(int)
    for prep in preps:
        for run in prep:
            rn = run.get('run_number', 1)
            rn_capped = min(rn, 5)
            pos = run.get('position')
            if pos is not None:
                try:
                    p = int(pos)
                    if p == 1:
                        win_count[rn_capped] += 1
                    if p <= 3:
                        place_count[rn_capped] += 1
                except (ValueError, TypeError):
                    pass

    if win_count:
        return max(win_count, key=win_count.get)
    if place_count:
        return max(place_count, key=place_count.get)
    return None


def _load_trainer_patterns():
    """Load trainer_patterns.json if it exists. Returns dict or {}."""
    tp_path = INTELLIGENCE_DIR / "trainer_patterns.json"
    if tp_path.exists():
        try:
            import json
            with open(tp_path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def build_prep_cycles(today, conn, race_date):
    """
    Build prep_cycles.json — prep position, trajectory, due-to-win for each runner.

    Enhanced with: individual horse prep profiles, campaign tempo, weight penalty,
    return type classification, track-specific peaks, and age-based modifiers.
    """
    import fitness_peak
    from datetime import datetime

    # Clear cross-run caches because fitness_peak caches are keyed only by horse name.
    fitness_peak.clear_cache()

    all_horse_ids = set()
    runner_info = {}
    for race in today["races"]:
        for runner in race["runners"]:
            hid = runner.get("horse_id", "")
            if hid:
                all_horse_ids.add(hid)
                runner_info[hid] = {
                    "age_years": runner.get("age_years"),
                    "trainer": runner.get("trainer", ""),
                    "track": race.get("track", ""),
                    "distance_m": race.get("distance_m", 1400),
                }

    horse_prep_profiles_raw = _load_horse_prep_profiles(conn, all_horse_ids)
    horse_prep_profiles = defaultdict(dict)
    for (hid, db, rn), stats in horse_prep_profiles_raw.items():
        horse_prep_profiles[hid][(db, rn)] = stats

    profiles_count = len(set(k[0] for k in horse_prep_profiles_raw))
    print(f"  [PREP] Loaded prep profiles for {profiles_count} horses", file=sys.stderr)

    trainer_patterns = _load_trainer_patterns()

    try:
        race_month = datetime.strptime(str(race_date)[:10], '%Y-%m-%d').month
    except (ValueError, TypeError):
        race_month = None

    horse_names = list(today["horse_names"])
    wt_history = _fetch_weight_track_history(conn, horse_names, race_date)

    name_to_id = {}
    name_to_info = {}
    for race in today["races"]:
        for runner in race["runners"]:
            hname = runner.get("horse_name", "")
            hid = runner.get("horse_id", "")
            if hname and hid:
                name_to_id[hname] = hid
                name_to_info[hname] = runner_info.get(hid, {})

    fitness_by_name = {}
    total_batches = (len(horse_names) + 49) // 50 if horse_names else 0

    for batch_index, horse_batch in enumerate(_iter_batches(horse_names, 50), start=1):
        print(
            f"  [PREP] Processing fitness batch {batch_index}/{total_batches} ({len(horse_batch)} horses)",
            file=sys.stderr,
        )
        original_fetch = _batch_prefetch_fitness_data(horse_batch, race_date, conn)
        try:
            for hname in horse_batch:
                prep = fitness_peak.detect_current_prep(hname, race_date, conn)
                profile = fitness_peak.build_fitness_profile(hname, race_date, conn)
                form_analysis = fitness_peak.analyze_form_trajectory(
                    prep.get("positions_this_prep", []),
                    prep.get("margins_this_prep", []),
                )

                hid = name_to_id.get(hname, "")
                info = name_to_info.get(hname, {})
                today_track = info.get("track", "")
                today_distance_m = info.get("distance_m", 1400)
                age_years = info.get("age_years")
                trainer_name = info.get("trainer", "")

                campaign_tempo = fitness_peak.calculate_campaign_tempo(
                    prep.get("days_between_runs", [])
                )

                wt_runs = wt_history.get(hname.lower(), [])
                spell_days = prep.get("spell_length_days", 0)
                runs_this = prep.get("runs_this_prep", 1)
                weights_this_prep = []
                if wt_runs:
                    recent_weights = [r['weight_kg'] for r in wt_runs[-runs_this:] if r.get('weight_kg')]
                    weights_this_prep = recent_weights
                weight_penalty = fitness_peak.calculate_weight_penalty(weights_this_prep)

                return_type = fitness_peak.classify_return_type(
                    hname, race_date, spell_days, conn
                )

                track_runs = [r for r in wt_runs if r.get('track', '').lower() == today_track.lower()]
                track_runs_formatted = [
                    {'race_date': r['race_date'], 'position': None, 'margin': None,
                     'distance': r.get('distance_m'), 'race_class': None}
                    for r in track_runs
                ]
                prefetched_runs = fitness_peak._fetch_race_history(hname, race_date, conn)
                track_runs_with_pos = [
                    r for r in prefetched_runs
                    if any(tr['race_date'] == r['race_date'] for tr in track_runs)
                ]
                track_specific_peak = _compute_track_specific_peak(track_runs_with_pos, hname)

                distance_band = _classify_distance_band(today_distance_m)

                hp_profile = horse_prep_profiles.get(hid, {}) if hid else {}
                has_individual_profile = bool(hp_profile)

                trainer_fu_pct = None
                if trainer_name:
                    tp = trainer_patterns.get(trainer_name, {})
                    trainer_fu_pct = tp.get("first_up_win_pct")

                fitness_score = fitness_peak.calculate_fitness_readiness_score(
                    prep,
                    profile,
                    form_analysis,
                    horse_prep_profile=hp_profile if has_individual_profile else None,
                    age_years=age_years,
                    campaign_tempo=campaign_tempo,
                    weight_penalty=weight_penalty,
                    return_type=return_type,
                    trainer_first_up_pct=trainer_fu_pct,
                    distance_band=distance_band,
                    race_month=race_month,
                )

                runs = prep.get("runs_this_prep", 1)
                run_key = runs if runs <= 5 else 6
                peak_run = profile.get("peak_run_number", 2)
                effective_peak = track_specific_peak if track_specific_peak is not None else peak_run
                is_at_peak = runs == effective_peak
                this_run_wr = profile.get("win_rates_by_run", {}).get(run_key, 0.0)
                peak_wr = profile.get("peak_run_winrate", 0.0)
                label = fitness_peak._run_label(runs)
                description = fitness_peak._build_description(
                    label,
                    is_at_peak,
                    form_analysis.get("trajectory", "steady"),
                    form_analysis.get("improving_and_placed", False),
                    form_analysis.get("placed_last_start", False),
                )

                fitness_by_name[hname] = {
                    "prep": prep,
                    "fitness": {
                        "runs_this_prep": runs,
                        "run_label": label,
                        "spell_length_days": prep.get("spell_length_days", 0),
                        "is_at_peak_run": is_at_peak,
                        "peak_run_number": effective_peak,
                        "peak_run_winrate": peak_wr,
                        "this_run_winrate": this_run_wr,
                        "fitness_readiness_score": fitness_score,
                        "prep_form_trajectory": form_analysis.get("trajectory", "steady"),
                        "margin_improvement_trend": form_analysis.get("margin_trend", 0.0),
                        "placed_last_start": form_analysis.get("placed_last_start", False),
                        "improving_and_placed": form_analysis.get("improving_and_placed", False),
                        "run_spacing_quality": fitness_peak.calculate_run_spacing_quality(
                            prep.get("days_since_last_run", 999)
                        ),
                        "career_avg_prep_length": profile.get("career_avg_prep_length", 3.0),
                        "pattern_flags": profile.get("pattern_flags", {}),
                        "positions_this_prep": prep.get("positions_this_prep", []),
                        "description": description,
                    },
                    "campaign_tempo": campaign_tempo,
                    "weight_penalty": weight_penalty,
                    "return_type": return_type,
                    "track_specific_peak": track_specific_peak,
                    "has_individual_profile": has_individual_profile,
                    "weights_this_prep": weights_this_prep,
                    "age_years": age_years,
                }
        finally:
            fitness_peak._fetch_race_history = original_fetch

    result = {}
    for race in today["races"]:
        for runner in race["runners"]:
            hid = runner["horse_id"]
            hname = runner["horse_name"]
            if not hid or hid in result:
                continue

            horse_data = fitness_by_name.get(hname, {})
            fitness = horse_data.get("fitness", fitness_peak._default_result(hname))
            prep = horse_data.get(
                "prep",
                {
                    "days_since_last_run": 999,
                    "margins_this_prep": [],
                },
            )

            runs = fitness.get("runs_this_prep", 1)
            positions = fitness.get("positions_this_prep", [])
            trajectory = _map_trajectory(fitness.get("prep_form_trajectory", "steady"))
            dslr = prep.get("days_since_last_run", 999)

            back_up = dslr <= 7
            back_up_hard = False
            if back_up and positions:
                last_pos = positions[-1] if positions else 99
                margins = prep.get("margins_this_prep", [])
                last_margin = margins[-1] if margins else 99
                back_up_hard = (last_pos <= 3) or (last_margin is not None and last_margin < 1.0)

            result[hid] = {
                "horse_name": hname,
                "prep_position": _map_prep_position(runs),
                "runs_this_prep": runs,
                "trajectory": trajectory,
                "due_to_win": _is_due_to_win(positions),
                "back_up": back_up,
                "back_up_hard_run": back_up_hard,
                "days_since_last_run": dslr,
                "spell_length_days": fitness.get("spell_length_days", 0),
                "is_at_peak_run": fitness.get("is_at_peak_run", False),
                "peak_run_number": fitness.get("peak_run_number", 2),
                "fitness_readiness_score": round(fitness.get("fitness_readiness_score", 0.5), 3),
                "pattern_flags": fitness.get("pattern_flags", {}),
                "campaign_tempo": horse_data.get("campaign_tempo", "STANDARD"),
                "weight_penalty": round(horse_data.get("weight_penalty", 0.0), 2),
                "return_type": horse_data.get("return_type"),
                "track_specific_peak": horse_data.get("track_specific_peak"),
                "has_individual_profile": horse_data.get("has_individual_profile", False),
                "weights_this_prep": horse_data.get("weights_this_prep", []),
                "age_years": horse_data.get("age_years"),
            }

    return result


def _linear_slope(values):
    """Simple linear regression slope."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


def build_sectional_trends(today, conn):
    """Build sectional_trends.json — sectional improvement signals per horse."""
    horse_names = today["horse_names"]
    if not horse_names:
        return {}

    cur = conn.cursor()
    names_lower = [n.lower() for n in horse_names]

    cur.execute("""
        SELECT LOWER(st.horse_name) as hn, st.race_date, st.track, st.race_number,
               st.z_200m, st.z_400m, st.z_600m,
               st.finishing_burst, st.svi
        FROM sectional_times st
        WHERE LOWER(st.horse_name) = ANY(%s)
          AND st.race_date >= (CURRENT_DATE - INTERVAL '180 days')::text
          AND st.z_200m IS NOT NULL
        ORDER BY st.horse_name, st.race_date DESC
    """, (names_lower,))
    sect_rows = cur.fetchall()

    cur.execute("""
        SELECT LOWER(horse_name) as hn, race_date, track, race_number, position
        FROM race_results_history
        WHERE LOWER(horse_name) = ANY(%s)
          AND race_date >= (CURRENT_DATE - INTERVAL '180 days')::text
          AND position IS NOT NULL
        ORDER BY horse_name, race_date DESC
    """, (names_lower,))
    pos_rows = cur.fetchall()

    pos_lookup = {}
    for hn, rd, trk, rn, pos in pos_rows:
        pos_lookup[(hn, str(rd), trk)] = pos

    # For fastest_closer_while_unplaced: need field-level z_200m comparison
    # Build race-level z_200m rankings
    cur.execute("""
        SELECT LOWER(horse_name) as hn, race_date, track, race_number, z_200m
        FROM sectional_times
        WHERE race_date >= (CURRENT_DATE - INTERVAL '180 days')::text
          AND z_200m IS NOT NULL
          AND (track, race_date, race_number) IN (
              SELECT DISTINCT track, race_date, race_number
              FROM sectional_times
              WHERE LOWER(horse_name) = ANY(%s)
                AND race_date >= (CURRENT_DATE - INTERVAL '180 days')::text
          )
        ORDER BY track, race_date, race_number, z_200m DESC
    """, (names_lower,))
    field_rows = cur.fetchall()

    race_z200_ranks = {}
    race_groups = {}
    for hn, rd, trk, rn, z200 in field_rows:
        rk = (str(rd), trk, rn)
        if rk not in race_groups:
            race_groups[rk] = []
        race_groups[rk].append((hn, float(z200)))

    for rk, entries in race_groups.items():
        entries.sort(key=lambda x: x[1], reverse=True)
        race_z200_ranks[rk] = {hn: rank + 1 for rank, (hn, _) in enumerate(entries)}

    cur.close()

    horse_sects = {}
    for hn, rd, trk, rn, z200, z400, z600, fb, svi in sect_rows:
        if hn not in horse_sects:
            horse_sects[hn] = []
        if len(horse_sects[hn]) < 5:
            horse_sects[hn].append({
                "race_date": str(rd), "track": trk, "race_number": rn,
                "z_200m": float(z200) if z200 else None,
                "z_400m": float(z400) if z400 else None,
                "z_600m": float(z600) if z600 else None,
                "finishing_burst": float(fb) if fb else None,
                "svi": float(svi) if svi else None,
            })

    name_map = {n.lower(): n for n in horse_names}
    result = {}

    for hn_lower, sects in horse_sects.items():
        display_name = name_map.get(hn_lower, hn_lower)

        z600_vals = [s["z_600m"] for s in reversed(sects) if s["z_600m"] is not None]
        z600_slope = _linear_slope(z600_vals) if len(z600_vals) >= 2 else 0.0
        sectional_trend_improving = z600_slope > 0.05

        fastest_closer_unplaced = False
        pb_while_unplaced = False

        if sects:
            last = sects[0]
            rk = (last["race_date"], last["track"], last["race_number"])
            pos = pos_lookup.get((hn_lower, last["race_date"], last["track"]))

            if pos and pos > 3:
                ranks = race_z200_ranks.get(rk, {})
                rank = ranks.get(hn_lower)
                if rank == 1:
                    fastest_closer_unplaced = True

                all_bursts = [s["finishing_burst"] for s in sects if s["finishing_burst"] is not None]
                if all_bursts and last.get("finishing_burst") is not None:
                    if last["finishing_burst"] >= max(all_bursts) and len(all_bursts) >= 2:
                        pb_while_unplaced = True

        result[display_name] = {
            "sectional_trend_improving": sectional_trend_improving,
            "fastest_closer_while_unplaced": fastest_closer_unplaced,
            "pb_while_unplaced": pb_while_unplaced,
            "last_3_z600": z600_vals[:3] if z600_vals else [],
            "z600_slope": round(z600_slope, 4),
            "data_points": len(sects),
        }

    for name in horse_names:
        if name not in result:
            result[name] = {
                "sectional_trend_improving": False,
                "fastest_closer_while_unplaced": False,
                "pb_while_unplaced": False,
                "last_3_z600": [],
                "z600_slope": 0.0,
                "data_points": 0,
            }

    return result


def build_trainer_patterns(today, conn):
    """Build trainer_patterns.json — trainer stats + jockey synergy."""
    trainers = today["trainers"]
    if not trainers:
        return {}

    cur = conn.cursor()
    trainers_lower = [t.lower() for t in trainers]

    cur.execute("""
        SELECT trainer,
               COUNT(*) as total_starts,
               COUNT(CASE WHEN won = 1 THEN 1 END) as wins,
               COUNT(CASE WHEN placed = 1 THEN 1 END) as places
        FROM training_data
        WHERE LOWER(trainer) = ANY(%s)
          AND race_date >= (CURRENT_DATE - INTERVAL '365 days')::text
        GROUP BY trainer
    """, (trainers_lower,))
    trainer_rows = cur.fetchall()

    trainer_stats = {}
    for trainer, starts, wins, places in trainer_rows:
        trainer_stats[trainer.lower()] = {
            "trainer_name": trainer,
            "total_starts_12m": starts,
            "wins_12m": wins,
            "places_12m": places,
            "overall_win_pct": round(wins / starts * 100, 2) if starts > 0 else 0.0,
            "overall_place_pct": round(places / starts * 100, 2) if starts > 0 else 0.0,
        }

    try:
        cur.execute("""
            SELECT LOWER(trainer) as trainer_lower,
                   COUNT(*) as fu_starts,
                   COUNT(CASE WHEN result_position = 1 THEN 1 END) as fu_wins
            FROM selections
            WHERE LOWER(trainer) = ANY(%s)
              AND is_first_up = true
              AND result_position IS NOT NULL
              AND race_date >= (CURRENT_DATE - INTERVAL '365 days')::text
            GROUP BY 1
        """, (trainers_lower,))
        fu_rows = cur.fetchall()
        for tl, fu_starts, fu_wins in fu_rows:
            if tl in trainer_stats:
                trainer_stats[tl]["first_up_starts"] = fu_starts
                trainer_stats[tl]["first_up_win_pct"] = round(fu_wins / fu_starts * 100, 2) if fu_starts > 0 else 0.0
    except CONN_ERRORS:
        raise
    except Exception as e:
        print(f"  [WARN] first-up query failed: {e}", file=sys.stderr)
        # Without a rollback the aborted transaction fails every later query
        # on this connection, turning one optional query into a dead step.
        conn.rollback()

    cur.execute("""
        SELECT LOWER(trainer) as trainer_lower, jockey,
               COUNT(*) as combos,
               COUNT(CASE WHEN won = 1 THEN 1 END) as wins
        FROM training_data
        WHERE LOWER(trainer) = ANY(%s)
          AND race_date >= (CURRENT_DATE - INTERVAL '365 days')::text
        GROUP BY 1, 2
        HAVING COUNT(*) >= 3
        ORDER BY 1, COUNT(CASE WHEN won = 1 THEN 1 END) DESC
    """, (trainers_lower,))
    synergy_rows = cur.fetchall()

    synergy_map = {}
    for tl, jockey, combos, wins in synergy_rows:
        if tl not in synergy_map:
            synergy_map[tl] = {}
        combo_pct = round(wins / combos * 100, 2) if combos > 0 else 0.0
        overall_pct = trainer_stats.get(tl, {}).get("overall_win_pct", 10.0)
        synergy_edge = round(combo_pct - overall_pct, 2)
        synergy_map[tl][jockey] = {
            "combos": combos,
            "wins": wins,
            "win_pct": combo_pct,
            "synergy_edge": synergy_edge,
        }

    cur.close()

    result = {}
    for tl, stats in trainer_stats.items():
        name = stats["trainer_name"]
        result[name] = {
            "overall_win_pct": stats["overall_win_pct"],
            "overall_place_pct": stats["overall_place_pct"],
            "total_starts_12m": stats["total_starts_12m"],
            "first_up_win_pct": stats.get("first_up_win_pct", None),
            "first_up_starts": stats.get("first_up_starts", None),
            "jockey_synergy": synergy_map.get(tl, {}),
        }

    return result


def main(race_date):
    print(f"[Agent 2 — Form/Horse] Starting for {race_date}", file=sys.stderr)
    today = load_todays_runners(race_date)
    print(f"  Loaded {len(today['horse_names'])} horses across {len(today['tracks'])} tracks", file=sys.stderr)

    runner = ReconnectingRunner()
    files_written = []

    try:
        steps = [
            ("form_franking.json", lambda conn: build_form_franking(today, conn)),
            ("prep_cycles.json", lambda conn: build_prep_cycles(today, conn, race_date)),
            ("sectional_trends.json", lambda conn: build_sectional_trends(today, conn)),
            ("trainer_patterns.json", lambda conn: build_trainer_patterns(today, conn)),
        ]
        for filename, step in steps:
            print(f"  Building {filename} ...", file=sys.stderr)
            data = runner.run(filename, step)
            json_write(data, INTELLIGENCE_DIR / filename)
            files_written.append(filename)
    finally:
        runner.close()

    print(f"[Agent 2] Complete — wrote {len(files_written)} files", file=sys.stderr)
    return files_written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stride_agent_form.py YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)
    try:
        main(sys.argv[1])
    except Exception as e:
        print(f"[Agent 2] FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
