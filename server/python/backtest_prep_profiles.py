#!/usr/bin/env python3
"""
Compares the OLD universal prep bonus system vs the NEW individual profile-based system across historical data, reporting which system better predicts winners.

The backtest uses walk-forward methodology: for each horse on each race date, only data BEFORE that date is used to compute the prep score. This prevents look-ahead bias.
"""

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from intelligence_common import get_connection
from fitness_peak import (
    _detect_preparations,
    _parse_date,
    calculate_fitness_readiness_score,
    calculate_campaign_tempo,
    calculate_weight_penalty,
    analyze_form_trajectory,
)

SPELL_THRESHOLD_DAYS = 60


def classify_distance_band(distance_m):
    if distance_m is None:
        return "middle"
    d = int(distance_m)
    if d < 1200:
        return "sprint"
    if d <= 1600:
        return "middle"
    return "staying"


def build_walk_forward_profile(runs_before, distance_band, run_number):
    """
    Build a horse prep profile using only runs BEFORE the target date.
    Returns {starts, wins, places} for the given (distance_band, run_number) or None.
    """
    preps = _detect_preparations(runs_before)
    if not preps:
        return None

    stats = {'starts': 0, 'wins': 0, 'places': 0}
    for prep in preps:
        for run in prep:
            rn = min(run.get('run_number', 1), 5)
            dist_band = classify_distance_band(run.get('distance_m'))
            if dist_band == distance_band and rn == run_number:
                stats['starts'] += 1
                pos = run.get('position')
                if pos is not None:
                    try:
                        p = int(pos)
                        if p == 1:
                            stats['wins'] += 1
                            stats['places'] += 1
                        elif p <= 3:
                            stats['places'] += 1
                    except (ValueError, TypeError):
                        pass

    return stats if stats['starts'] >= 4 else None


def compute_old_score(prep_data, form_analysis, profile_total_preps, peak_run):
    """Compute fitness score using the OLD universal system (no individual profiles)."""
    runs_this_prep = prep_data['runs_this_prep']
    has_individual_data = profile_total_preps >= 2

    if has_individual_data:
        if runs_this_prep == peak_run:
            peak_score = 1.0
        elif abs(runs_this_prep - peak_run) == 1:
            peak_score = 0.8
        else:
            peak_score = 0.5
    else:
        if runs_this_prep in (2, 3):
            peak_score = 1.0
        elif runs_this_prep in (1, 4):
            peak_score = 0.8
        else:
            peak_score = 0.5

    traj = form_analysis.get('trajectory', 'steady')
    traj_map = {'improving': 1.0, 'steady': 0.7, 'declining': 0.3, 'peaked': 0.5}
    traj_score = traj_map.get(traj, 0.7)
    if form_analysis.get('improving_and_placed'):
        traj_score = min(1.0, traj_score + 0.3)
    elif form_analysis.get('placed_last_start'):
        traj_score = min(1.0, traj_score + 0.2)

    from fitness_peak import calculate_run_spacing_quality, calculate_spell_length_score
    spacing_score = calculate_run_spacing_quality(prep_data.get('days_since_last_run', 999))
    spell_score = calculate_spell_length_score(prep_data.get('spell_length_days', 0))

    if form_analysis.get('placed_last_start'):
        placed_score = 1.0
    elif form_analysis.get('close_up_last_start'):
        placed_score = 0.6
    else:
        placed_score = 0.3

    composite = (
        peak_score * 0.30 +
        traj_score * 0.25 +
        spacing_score * 0.15 +
        spell_score * 0.10 +
        placed_score * 0.20
    )
    return round(min(1.0, max(0.0, composite)), 3)


def main():
    parser = argparse.ArgumentParser(description="Backtest old vs new prep profile system.")
    parser.add_argument('--months', type=int, default=15, help='Months of history to backtest (default 15)')
    parser.add_argument('--min-runs', type=int, default=5, help='Min runs per horse to include (default 5)')
    parser.add_argument('--sample-size', type=int, default=0, help='Limit horses sampled (0=all)')
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()

    cutoff_date = (datetime.now() - timedelta(days=args.months * 30)).strftime('%Y-%m-%d')
    print(f"[BACKTEST] Testing prep scoring from {cutoff_date} to today", file=sys.stderr)
    print(f"[BACKTEST] Min runs per horse: {args.min_runs}", file=sys.stderr)

    limit_clause = f"LIMIT {args.sample_size}" if args.sample_size > 0 else ""
    cur.execute(f"""
        SELECT horse_id, horse_name
        FROM race_results_history
        WHERE horse_id IS NOT NULL AND position IS NOT NULL
        GROUP BY horse_id, horse_name
        HAVING COUNT(*) >= %s
        ORDER BY horse_id
        {limit_clause}
    """, (args.min_runs,))
    horses = cur.fetchall()
    print(f"[BACKTEST] Found {len(horses)} horses with {args.min_runs}+ runs", file=sys.stderr)

    if not horses:
        print("[BACKTEST] No horses found. Exiting.", file=sys.stderr)
        conn.close()
        return

    old_system = {'total': 0, 'winners_in_top_quartile': 0, 'score_sum_winners': 0.0,
                  'score_sum_losers': 0.0, 'winner_count': 0, 'loser_count': 0}
    new_system = {'total': 0, 'winners_in_top_quartile': 0, 'score_sum_winners': 0.0,
                  'score_sum_losers': 0.0, 'winner_count': 0, 'loser_count': 0}

    old_by_run = defaultdict(lambda: {'wins': 0, 'total': 0, 'score_sum': 0.0})
    new_by_run = defaultdict(lambda: {'wins': 0, 'total': 0, 'score_sum': 0.0})

    start_time = time.time()
    processed = 0
    batch_size = 200

    for batch_start in range(0, len(horses), batch_size):
        batch = horses[batch_start:batch_start + batch_size]
        horse_ids = [h[0] for h in batch]

        cur.execute("""
            SELECT horse_id, horse_name, race_date, position, margin_lengths,
                   distance_m, sp_odds, field_size, weight_kg, track
            FROM race_results_history
            WHERE horse_id = ANY(%s) AND position IS NOT NULL
            ORDER BY horse_id, race_date::date ASC, race_number ASC, id ASC
        """, (horse_ids,))

        rows = cur.fetchall()
        runs_by_horse = defaultdict(list)
        for row in rows:
            runs_by_horse[row[0]].append({
                'horse_id': row[0],
                'horse_name': row[1],
                'race_date': row[2],
                'position': row[3],
                'margin': row[4],
                'distance_m': row[5],
                'sp_odds': row[6],
                'field_size': row[7],
                'weight_kg': row[8],
                'track': row[9],
            })

        for hid in horse_ids:
            all_runs = runs_by_horse.get(hid, [])
            if len(all_runs) < args.min_runs:
                continue

            for i, target_run in enumerate(all_runs):
                target_date = str(target_run['race_date'])[:10]
                if target_date < cutoff_date:
                    continue

                runs_before = all_runs[:i]
                if len(runs_before) < 3:
                    continue

                formatted = [{
                    'race_date': r['race_date'],
                    'position': r['position'],
                    'margin': r.get('margin'),
                    'distance': r.get('distance_m'),
                    'race_class': None,
                } for r in runs_before]

                preps = _detect_preparations(formatted)
                if not preps:
                    continue

                current_prep = preps[-1]
                runs_this_prep = len(current_prep) + 1
                if runs_this_prep > 8:
                    continue

                last_dt = _parse_date(runs_before[-1]['race_date'])
                target_dt = _parse_date(target_date)
                if not last_dt or not target_dt:
                    continue
                dslr = (target_dt - last_dt).days
                if dslr >= SPELL_THRESHOLD_DAYS:
                    runs_this_prep = 1

                spell_days = 0
                if runs_this_prep == 1:
                    spell_days = dslr
                elif len(preps) >= 2:
                    prev_last = preps[-2][-1].get('_dt')
                    curr_first = current_prep[0].get('_dt')
                    if prev_last and curr_first:
                        spell_days = (curr_first - prev_last).days

                positions = [r.get('position') for r in current_prep if r.get('position') is not None]
                try:
                    positions_int = [int(p) for p in positions]
                except (ValueError, TypeError):
                    positions_int = []
                margins = [r.get('margin') for r in current_prep if r.get('margin') is not None]
                try:
                    margins_float = [float(m) for m in margins]
                except (ValueError, TypeError):
                    margins_float = []

                form_analysis = analyze_form_trajectory(positions_int, margins_float)

                days_between = []
                for run in current_prep:
                    gap = run.get('gap_days')
                    if gap is not None and run.get('run_number', 1) > 1:
                        days_between.append(gap)

                peak_counts = defaultdict(int)
                for prep in preps:
                    for run in prep:
                        rn = run.get('run_number', 1)
                        pos = run.get('position')
                        if pos is not None and int(pos) == 1:
                            peak_counts[rn] += 1
                peak_run = 2
                if peak_counts:
                    peak_run = max(peak_counts, key=peak_counts.get)

                prep_data = {
                    'runs_this_prep': runs_this_prep,
                    'spell_length_days': spell_days,
                    'days_since_last_run': dslr,
                    'days_between_runs': days_between,
                    'positions_this_prep': positions_int,
                    'margins_this_prep': margins_float,
                }

                profile_stub = {
                    'peak_run_number': peak_run,
                    'total_preps': len(preps),
                }

                old_score = compute_old_score(prep_data, form_analysis, len(preps), peak_run)

                distance_band = classify_distance_band(target_run.get('distance_m'))
                run_key = min(runs_this_prep, 5)
                wf_profile = build_walk_forward_profile(formatted, distance_band, run_key)

                hp_profile = {}
                has_individual = False
                if wf_profile:
                    hp_profile = {(distance_band, run_key): wf_profile}
                    has_individual = True

                campaign_tempo = calculate_campaign_tempo(days_between)

                weights = [r.get('weight_kg') for r in runs_before[-runs_this_prep:] if r.get('weight_kg')]
                weight_penalty = calculate_weight_penalty(weights)

                new_score = calculate_fitness_readiness_score(
                    prep_data,
                    profile_stub,
                    form_analysis,
                    horse_prep_profile=hp_profile if has_individual else None,
                    campaign_tempo=campaign_tempo,
                    weight_penalty=weight_penalty,
                    distance_band=distance_band,
                )

                actual_pos = target_run.get('position')
                if actual_pos is None:
                    continue
                try:
                    actual_pos = int(actual_pos)
                except (ValueError, TypeError):
                    continue

                is_winner = actual_pos == 1
                run_label = f"{min(runs_this_prep, 5)}th-up" if runs_this_prep > 3 else \
                    {1: '1st-up', 2: '2nd-up', 3: '3rd-up'}[runs_this_prep]

                old_system['total'] += 1
                new_system['total'] += 1
                old_by_run[run_label]['total'] += 1
                new_by_run[run_label]['total'] += 1
                old_by_run[run_label]['score_sum'] += old_score
                new_by_run[run_label]['score_sum'] += new_score

                if is_winner:
                    old_system['winner_count'] += 1
                    old_system['score_sum_winners'] += old_score
                    new_system['winner_count'] += 1
                    new_system['score_sum_winners'] += new_score
                    old_by_run[run_label]['wins'] += 1
                    new_by_run[run_label]['wins'] += 1
                else:
                    old_system['loser_count'] += 1
                    old_system['score_sum_losers'] += old_score
                    new_system['loser_count'] += 1
                    new_system['score_sum_losers'] += new_score

            processed += 1

        elapsed = time.time() - start_time
        print(
            f"  [BACKTEST] Processed {processed}/{len(horses)} horses "
            f"({old_system['total']} runs scored, {elapsed:.0f}s)",
            file=sys.stderr,
        )

    conn.close()

    print("\n" + "=" * 70)
    print("PREP PROFILE BACKTEST RESULTS")
    print(f"Period: {cutoff_date} to today | Horses: {processed} | Runs scored: {old_system['total']}")
    print("=" * 70)

    print("\n--- OVERALL ---")
    for label, system in [("OLD (universal)", old_system), ("NEW (profile-based)", new_system)]:
        avg_winner = system['score_sum_winners'] / system['winner_count'] if system['winner_count'] else 0
        avg_loser = system['score_sum_losers'] / system['loser_count'] if system['loser_count'] else 0
        separation = avg_winner - avg_loser
        print(f"  {label}:")
        print(f"    Avg score for WINNERS: {avg_winner:.4f}")
        print(f"    Avg score for LOSERS:  {avg_loser:.4f}")
        print(f"    Winner-loser separation: {separation:.4f}")
        print(f"    Winners: {system['winner_count']} / {system['total']} "
              f"({system['winner_count']/system['total']*100:.1f}%)" if system['total'] else "")

    improvement = 0
    if old_system['winner_count'] and new_system['winner_count']:
        old_sep = (old_system['score_sum_winners'] / old_system['winner_count']) - \
                  (old_system['score_sum_losers'] / old_system['loser_count'] if old_system['loser_count'] else 0)
        new_sep = (new_system['score_sum_winners'] / new_system['winner_count']) - \
                  (new_system['score_sum_losers'] / new_system['loser_count'] if new_system['loser_count'] else 0)
        improvement = new_sep - old_sep
        print(f"\n  >>> Separation improvement: {improvement:+.4f} "
              f"({'NEW wins' if improvement > 0 else 'OLD wins'})")

    print("\n--- BY RUN NUMBER ---")
    print(f"  {'Run':<8} {'OLD avg':<12} {'NEW avg':<12} {'Wins':<8} {'Total':<8} {'Delta':<8}")
    print(f"  {'-'*56}")
    for run_label in sorted(set(list(old_by_run.keys()) + list(new_by_run.keys()))):
        old_r = old_by_run[run_label]
        new_r = new_by_run[run_label]
        old_avg = old_r['score_sum'] / old_r['total'] if old_r['total'] else 0
        new_avg = new_r['score_sum'] / new_r['total'] if new_r['total'] else 0
        delta = new_avg - old_avg
        print(f"  {run_label:<8} {old_avg:<12.4f} {new_avg:<12.4f} {old_r['wins']:<8} {old_r['total']:<8} {delta:+.4f}")

    print(f"\n{'=' * 70}")
    if improvement > 0:
        print("RESULT: New profile-based system shows BETTER winner-loser separation.")
        print("Recommendation: Deploy the new system.")
    elif improvement == 0:
        print("RESULT: Both systems are equivalent.")
    else:
        print("RESULT: Old universal system shows better separation.")
        print("Recommendation: Review new system parameters before deploying.")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    main()
