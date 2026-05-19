#!/usr/bin/env python3
"""
Banker Detector Module - Enhanced v2

Identifies "banker" horses - dominant short-priced favourites that should be
selected regardless of edge. Uses MC win probability, ELO ratings, ML consensus,
danger horse profiling, form trajectory, pace security, and conditions suitability
to score and qualify banker candidates with adaptive race-context thresholds.

Designed to integrate with the MC API pipeline and race analysis system.
"""

import sys
import json
import math

WEIGHT_MC_DOMINANCE = 0.25
WEIGHT_ELO_GAP = 0.15
WEIGHT_ML_CONSENSUS = 0.12
WEIGHT_DANGER_PROFILE = 0.20
WEIGHT_FORM_TRAJECTORY = 0.10
WEIGHT_PACE_SECURITY = 0.10
WEIGHT_CONDITIONS_FIT = 0.08

BASE_MIN_BANKER_SCORE = 55
BASE_MIN_MC_WIN_PROB = 0.30
MAX_MARKET_ODDS = 3.50
MIN_FIELD_SIZE = 5

STRONG_BANKER_SCORE_OFFSET = 15
STRONG_MC_WIN_PROB_OFFSET = 0.10
STRONG_MAX_DANGER_LEVEL = 25.0

MC_DOM_FLOOR = 0.25
MC_DOM_CEILING = 0.65

ELO_GAP_CEILING = 80

DANGER_ELO_WEIGHT = 0.35
DANGER_MC_WEIGHT = 0.30
DANGER_FORM_WEIGHT = 0.20
DANGER_CLASS_WEIGHT = 0.15

STRONG_BANKER_WIN_STAKE = 2.0
STRONG_BANKER_EXACTA_STAKE = 1.0
BANKER_WIN_STAKE = 1.5
BANKER_EXACTA_STAKE = 0.5

MIN_STRIKE_RATE = 0.65

RACE_CLASS_TIERS = {
    'group 1': 5, 'group 2': 4, 'group 3': 4, 'listed': 3,
    'open': 3, 'benchmark': 2, 'class': 2, 'maiden': 1,
    'restricted': 2, 'handicap': 2, 'plate': 2, 'stakes': 4,
}


def _log(msg):
    print(f"[BankerDetector] {msg}", file=sys.stderr)


def _classify_race_tier(race_class_str):
    if not race_class_str:
        return 2
    if isinstance(race_class_str, (int, float)):
        if race_class_str >= 8:
            return 4
        elif race_class_str >= 5:
            return 3
        elif race_class_str >= 2:
            return 2
        else:
            return 1
    lower = str(race_class_str).lower()
    for key, tier in RACE_CLASS_TIERS.items():
        if key in lower:
            return tier
    return 2


def _get_going_category(going_str):
    if not going_str:
        return 'unknown'
    lower = going_str.lower().strip()
    if any(k in lower for k in ['heavy', 'soft 7', 'soft 8', 'soft 9', 'soft 10']):
        return 'heavy'
    if any(k in lower for k in ['soft', 'yielding']):
        return 'soft'
    if any(k in lower for k in ['good 3', 'good 2', 'good 1', 'firm']):
        return 'firm'
    if any(k in lower for k in ['good', 'dead']):
        return 'good'
    return 'unknown'


class BankerDetector:
    def __init__(self):
        self.selections_log = []
        self.results = []

    def detect_bankers(self, race_results: list, race_info: dict) -> list:
        try:
            if not race_results:
                return race_results

            race_context = self._build_race_context(race_results, race_info)

            adj_min_score, adj_min_mc = self._adaptive_thresholds(race_context)

            for runner in race_results:
                score, components = self._calculate_banker_score(runner, race_results, race_context)

                mc_win_prob = runner.get('winPercentage', 0) / 100.0

                qualifies, tier, qual_log = self._check_qualification(
                    runner, score, race_info, race_context, adj_min_score, adj_min_mc
                )

                if qualifies:
                    danger_level = components.get('max_danger_level', 100)
                    tier = self._determine_tier(score, mc_win_prob, danger_level, adj_min_score)
                    staking = self._get_staking(tier)
                    exotic_combos = self._generate_exotic_combos(runner, race_results)
                else:
                    tier = None
                    staking = {'win_stake': 0.0, 'exacta_stake': 0.0}
                    exotic_combos = []

                runner['banker_flag'] = qualifies
                runner['banker_score'] = round(score, 2)
                runner['banker_tier'] = tier
                runner['banker_staking'] = staking
                runner['banker_exotic_combos'] = exotic_combos
                runner['banker_score_components'] = components
                runner['banker_qualification_log'] = qual_log

                if qualifies:
                    horse_name = runner.get('horse', 'Unknown')
                    _log(f"BANKER DETECTED: {horse_name} | tier={tier} | score={score:.1f} | "
                         f"MC={mc_win_prob:.1%} | odds=${runner.get('marketOdds', 0):.2f} | "
                         f"context={race_context.get('context_label', '?')} | "
                         f"thresholds=score>{adj_min_score:.0f}/mc>{adj_min_mc:.0%}")
                    self.selections_log.append({
                        'horse': horse_name,
                        'number': runner.get('number', 0),
                        'score': round(score, 2),
                        'tier': tier,
                        'mc_win_prob': mc_win_prob,
                        'market_odds': runner.get('marketOdds', 0),
                        'components': components,
                        'race_info': race_info,
                        'race_context': race_context,
                    })

            return race_results

        except Exception as e:
            _log(f"Error in detect_bankers: {e}")
            return race_results

    def _build_race_context(self, all_runners: list, race_info: dict) -> dict:
        field_size = len(all_runners)
        race_class_raw = race_info.get('raceClass', race_info.get('race_class', '')) or ''
        race_class_str = str(race_class_raw) if race_class_raw else ''
        race_tier = _classify_race_tier(race_class_raw)

        mc_probs = sorted(
            [r.get('winPercentage', 0) / 100.0 for r in all_runners],
            reverse=True
        )

        top_prob = mc_probs[0] if mc_probs else 0
        second_prob = mc_probs[1] if len(mc_probs) > 1 else 0
        dominance_gap = top_prob - second_prob

        hhi = sum(p**2 for p in mc_probs) if mc_probs else 0
        competitiveness = 1.0 - hhi

        going_str = race_info.get('going', race_info.get('track_condition', ''))
        going_cat = _get_going_category(going_str)

        distance_raw = race_info.get('distance', '') or ''
        try:
            if isinstance(distance_raw, (int, float)):
                distance_m = int(distance_raw)
            else:
                distance_str = str(distance_raw)
                distance_m = int(''.join(c for c in distance_str if c.isdigit()) or '0')
        except (ValueError, TypeError):
            distance_m = 0

        is_sprint = distance_m > 0 and distance_m <= 1200
        is_staying = distance_m >= 2000
        is_middle = not is_sprint and not is_staying and distance_m > 0

        is_maiden = 'maiden' in race_class_str.lower()

        if competitiveness < 0.6 and field_size <= 8:
            context_label = 'weak'
        elif race_tier >= 4 and field_size >= 12:
            context_label = 'elite'
        elif competitiveness > 0.85:
            context_label = 'open'
        else:
            context_label = 'standard'

        return {
            'field_size': field_size,
            'race_tier': race_tier,
            'race_class': race_class_str,
            'competitiveness': round(competitiveness, 4),
            'hhi': round(hhi, 4),
            'dominance_gap': round(dominance_gap, 4),
            'top_prob': round(top_prob, 4),
            'second_prob': round(second_prob, 4),
            'going_category': going_cat,
            'going_raw': going_str or '',
            'distance_m': distance_m,
            'is_sprint': is_sprint,
            'is_staying': is_staying,
            'is_middle': is_middle,
            'is_maiden': is_maiden,
            'context_label': context_label,
        }

    def _adaptive_thresholds(self, ctx: dict) -> tuple:
        score_threshold = float(BASE_MIN_BANKER_SCORE)
        mc_threshold = float(BASE_MIN_MC_WIN_PROB)

        tier = ctx.get('race_tier', 2)
        if tier >= 4:
            score_threshold += 8
            mc_threshold += 0.05
        elif tier <= 1:
            score_threshold -= 5
            mc_threshold -= 0.03

        field = ctx.get('field_size', 10)
        if field <= 6:
            score_threshold -= 5
            mc_threshold -= 0.05
        elif field >= 14:
            score_threshold += 5
            mc_threshold += 0.03

        comp = ctx.get('competitiveness', 0.75)
        if comp < 0.6:
            score_threshold -= 8
            mc_threshold -= 0.05
        elif comp > 0.85:
            score_threshold += 8
            mc_threshold += 0.05

        score_threshold = max(40.0, min(80.0, score_threshold))
        mc_threshold = max(0.20, min(0.55, mc_threshold))

        return score_threshold, mc_threshold

    def _calculate_banker_score(self, runner: dict, all_runners: list, ctx: dict) -> tuple:
        components = {}

        mc_win_prob = runner.get('winPercentage', 0) / 100.0
        if mc_win_prob <= MC_DOM_FLOOR:
            mc_dom_score = 0.0
        elif mc_win_prob >= MC_DOM_CEILING:
            mc_dom_score = 100.0
        else:
            mc_dom_score = ((mc_win_prob - MC_DOM_FLOOR) / (MC_DOM_CEILING - MC_DOM_FLOOR)) * 100.0
        components['mc_dominance'] = round(mc_dom_score, 2)

        runner_elo = runner.get('franking_elo', None)
        if runner_elo is not None:
            other_elos = []
            for r in all_runners:
                elo = r.get('franking_elo', None)
                if elo is not None and r.get('horse', '') != runner.get('horse', ''):
                    other_elos.append(elo)
            if other_elos:
                second_highest = max(other_elos)
                elo_gap = max(0, runner_elo - second_highest)
                elo_gap_score = min(100.0, (elo_gap / ELO_GAP_CEILING) * 100.0)
            else:
                elo_gap_score = 50.0
                elo_gap = 0
        else:
            elo_gap_score = 50.0
            elo_gap = 0
        components['elo_gap'] = round(elo_gap_score, 2)
        components['elo_gap_raw'] = round(elo_gap, 2)

        ml_ranks = runner.get('ml_ranks', None)
        if ml_ranks and isinstance(ml_ranks, dict):
            rank_values = []
            for key in ('xgb_rank', 'lgb_rank', 'cat_rank'):
                val = ml_ranks.get(key)
                if val is not None:
                    rank_values.append(val)
            if rank_values:
                first_count = sum(1 for v in rank_values if v == 1)
                top2_count = sum(1 for v in rank_values if v <= 2)
                if first_count == len(rank_values) and len(rank_values) >= 3:
                    ml_consensus_score = 100.0
                elif first_count >= 2:
                    ml_consensus_score = 75.0
                elif first_count >= 1 and top2_count >= 2:
                    ml_consensus_score = 55.0
                elif first_count >= 1:
                    ml_consensus_score = 40.0
                elif top2_count >= 2:
                    ml_consensus_score = 30.0
                else:
                    ml_consensus_score = 0.0
            else:
                ml_consensus_score = 50.0
        else:
            ml_consensus_score = 50.0
        components['ml_consensus'] = round(ml_consensus_score, 2)

        danger_score, danger_details = self._profile_danger_horses(runner, all_runners, ctx)
        components['danger_profile'] = round(danger_score, 2)
        components['danger_details'] = danger_details
        components['max_danger_level'] = danger_details.get('max_danger_level', 100)
        components['threat_count_raw'] = danger_details.get('num_threats', 0)

        form_trajectory_score = self._score_form_trajectory(runner)
        components['form_trajectory'] = round(form_trajectory_score, 2)

        pace_security_score = self._score_pace_security(runner, all_runners, ctx)
        components['pace_security'] = round(pace_security_score, 2)

        conditions_fit_score = self._score_conditions_fit(runner, ctx)
        components['conditions_fit'] = round(conditions_fit_score, 2)

        total_score = (
            mc_dom_score * WEIGHT_MC_DOMINANCE +
            elo_gap_score * WEIGHT_ELO_GAP +
            ml_consensus_score * WEIGHT_ML_CONSENSUS +
            danger_score * WEIGHT_DANGER_PROFILE +
            form_trajectory_score * WEIGHT_FORM_TRAJECTORY +
            pace_security_score * WEIGHT_PACE_SECURITY +
            conditions_fit_score * WEIGHT_CONDITIONS_FIT
        )

        pr_auth = runner.get('pagerank_authority', 0.0) or 0.0
        comm_str = runner.get('community_strength', 0.5) or 0.5
        f_stab = runner.get('form_stability', 0.5) or 0.5
        graph_af = runner.get('graph_anti_franked', 0)
        graph_af_ratio = runner.get('graph_anti_frank_ratio', 0.5) or 0.5

        graph_bonus = 0.0
        if pr_auth > 0.3:
            graph_bonus += min(8.0, (pr_auth - 0.3) * 20.0)
        if comm_str < 0.3:
            graph_bonus -= min(10.0, (0.3 - comm_str) * 30.0)
        elif comm_str > 0.6:
            graph_bonus += min(5.0, (comm_str - 0.6) * 12.0)
        if f_stab > 0.7:
            graph_bonus += min(4.0, (f_stab - 0.7) * 12.0)
        if graph_af and graph_af_ratio > 0.7:
            graph_bonus -= 15.0

        components['graph_bonus'] = round(graph_bonus, 2)
        components['pagerank_authority'] = round(pr_auth, 4)
        components['community_strength'] = round(comm_str, 4)
        components['form_stability'] = round(f_stab, 4)

        total_score += graph_bonus
        total_score = min(100.0, max(0.0, total_score))
        components['total'] = round(total_score, 2)

        return total_score, components

    def _profile_danger_horses(self, runner: dict, all_runners: list, ctx: dict) -> tuple:
        runner_name = runner.get('horse', '')
        runner_elo = runner.get('franking_elo', 0) or 0
        runner_mc = runner.get('winPercentage', 0) / 100.0

        danger_levels = []
        threats = []

        for r in all_runners:
            if r.get('horse', '') == runner_name:
                continue

            other_mc = r.get('winPercentage', 0) / 100.0
            other_elo = r.get('franking_elo', 0) or 0

            elo_closeness = 0
            if runner_elo > 0 and other_elo > 0:
                gap = runner_elo - other_elo
                if gap <= 0:
                    elo_closeness = 100
                elif gap < 50:
                    elo_closeness = max(0, 100 - (gap * 2))
                else:
                    elo_closeness = 0

            mc_threat = 0
            if runner_mc > 0:
                ratio = other_mc / runner_mc
                if ratio >= 0.8:
                    mc_threat = min(100, ratio * 100)
                elif ratio >= 0.5:
                    mc_threat = ratio * 60
                else:
                    mc_threat = 0

            other_improving = r.get('isImproving', 0)
            other_form_traj = r.get('form_trajectory', 0)
            other_fitness = r.get('fitnessData', {}) or {}
            other_prep_traj = other_fitness.get('prepFormTrajectory', 'steady') if isinstance(other_fitness, dict) else 'steady'

            form_danger = 50
            if other_improving == 1 or other_form_traj > 0.2 or other_prep_traj == 'improving':
                form_danger = 85
            elif other_form_traj > 0:
                form_danger = 65
            elif other_prep_traj == 'declining' or other_form_traj < -0.1:
                form_danger = 20
            elif other_form_traj < 0:
                form_danger = 35

            other_class_avg = 0
            try:
                other_class_avg = float(r.get('classAvg', 0) or 0)
            except (ValueError, TypeError):
                pass
            race_class_tier = ctx.get('race_tier', 2)

            class_danger = 50
            if other_class_avg > 0:
                if other_class_avg >= race_class_tier + 1:
                    class_danger = 90
                elif other_class_avg >= race_class_tier:
                    class_danger = 70
                elif other_class_avg >= race_class_tier - 1:
                    class_danger = 50
                else:
                    class_danger = 25

            composite_danger = (
                elo_closeness * DANGER_ELO_WEIGHT +
                mc_threat * DANGER_MC_WEIGHT +
                form_danger * DANGER_FORM_WEIGHT +
                class_danger * DANGER_CLASS_WEIGHT
            )

            danger_levels.append(composite_danger)
            if composite_danger >= 40:
                threats.append({
                    'horse': r.get('horse', '?'),
                    'danger_level': round(composite_danger, 1),
                    'elo_closeness': round(elo_closeness, 1),
                    'mc_threat': round(mc_threat, 1),
                    'form_danger': round(form_danger, 1),
                    'class_danger': round(class_danger, 1),
                })

        threats.sort(key=lambda t: t['danger_level'], reverse=True)
        top_threats = threats[:3]
        num_serious = sum(1 for t in threats if t['danger_level'] >= 60)
        num_moderate = sum(1 for t in threats if 40 <= t['danger_level'] < 60)
        max_danger = max(danger_levels) if danger_levels else 0

        field_size = ctx.get('field_size', 10)
        moderate_ratio = num_moderate / max(field_size - 1, 1)
        serious_ratio = num_serious / max(field_size - 1, 1)

        if num_serious == 0 and num_moderate <= 1:
            safety_score = 100.0
        elif num_serious == 0 and moderate_ratio <= 0.35:
            safety_score = 80.0
        elif num_serious == 0 and moderate_ratio <= 0.60:
            safety_score = 65.0
        elif num_serious == 0:
            safety_score = 50.0
        elif num_serious == 1 and serious_ratio <= 0.15:
            safety_score = 55.0
        elif num_serious == 1:
            safety_score = 40.0
        elif num_serious == 2 and serious_ratio <= 0.20:
            safety_score = 30.0
        elif num_serious == 2:
            safety_score = 20.0
        else:
            safety_score = max(0.0, 10.0 - num_serious * 3)

        if max_danger >= 85:
            safety_score = min(safety_score, 20.0)
        elif max_danger >= 75:
            safety_score = min(safety_score, 40.0)
        elif max_danger >= 65:
            safety_score = min(safety_score, 55.0)

        details = {
            'num_threats': len(threats),
            'num_serious': num_serious,
            'num_moderate': num_moderate,
            'max_danger_level': round(max_danger, 1),
            'top_threats': top_threats,
        }

        return safety_score, details

    def _score_form_trajectory(self, runner: dict) -> float:
        is_improving = runner.get('isImproving', 0)
        form_traj = runner.get('form_trajectory', 0)
        fitness_data = runner.get('fitnessData', {}) or {}
        prep_traj = fitness_data.get('prepFormTrajectory', 'steady') if isinstance(fitness_data, dict) else 'steady'
        readiness = fitness_data.get('readinessScore', 0.5) if isinstance(fitness_data, dict) else 0.5
        runs_this_prep = fitness_data.get('runsThisPrep', 0) if isinstance(fitness_data, dict) else 0
        is_peak = fitness_data.get('isAtPeakRun', False) if isinstance(fitness_data, dict) else False

        score = 50.0

        if is_improving == 1 or form_traj > 0.3:
            score += 30
        elif form_traj > 0:
            score += 15
        elif form_traj < -0.2:
            score -= 25
        elif form_traj < 0:
            score -= 10

        if prep_traj == 'improving':
            score += 15
        elif prep_traj == 'declining':
            score -= 20

        if is_peak:
            score += 10
        if readiness >= 0.85:
            score += 10
        elif readiness >= 0.7:
            score += 5
        elif readiness < 0.4:
            score -= 15

        if runs_this_prep == 0:
            score -= 5
        elif runs_this_prep >= 5:
            score -= 5

        form_quality = runner.get('formQualityTrend', 0) or 0
        if form_quality > 0.2:
            score += 10
        elif form_quality < -0.2:
            score -= 10

        return max(0.0, min(100.0, score))

    def _score_pace_security(self, runner: dict, all_runners: list, ctx: dict) -> float:
        pace_scenario = runner.get('paceScenarioJson', None) or runner.get('pace_scenario', None)

        if not pace_scenario or not isinstance(pace_scenario, dict):
            return 60.0

        score = 60.0

        hot_prob = float(pace_scenario.get('hot', 0))
        genuine_prob = float(pace_scenario.get('genuine', 0))
        slow_prob = float(pace_scenario.get('slow', 0))
        soft_prob = float(pace_scenario.get('soft', 0))

        running_style = runner.get('running_style', '') or ''
        running_style_score = runner.get('running_style_score', 1)

        is_leader = running_style_score >= 2 or 'lead' in running_style.lower() or 'on pace' in running_style.lower()
        is_backmarker = running_style_score == 0 or 'back' in running_style.lower()

        if is_leader:
            if hot_prob > 0.6:
                score -= 15
            elif slow_prob > 0.3 or soft_prob > 0.3:
                score += 20
            else:
                score += 10

        elif is_backmarker:
            if hot_prob > 0.5:
                score += 10
            elif slow_prob > 0.3:
                score -= 15

        else:
            score += 5

        barrier = 0
        try:
            barrier = int(runner.get('barrier', 0) or 0)
        except (ValueError, TypeError):
            pass

        field_size = ctx.get('field_size', 10)
        if barrier > 0 and field_size > 0:
            barrier_ratio = barrier / field_size
            if barrier_ratio > 0.75:
                score -= 10
                if ctx.get('is_sprint', False):
                    score -= 5
            elif barrier_ratio <= 0.25:
                score += 5

        if ctx.get('is_sprint', False) and is_leader:
            score += 5
        elif ctx.get('is_staying', False) and is_backmarker:
            score += 5

        return max(0.0, min(100.0, score))

    def _score_conditions_fit(self, runner: dict, ctx: dict) -> float:
        going_cat = ctx.get('going_category', 'unknown')

        if going_cat == 'unknown':
            return 65.0

        score = 60.0

        stats = runner.get('stats', {})
        if not stats or not isinstance(stats, dict):
            track_cond_stats = runner.get('track_condition_stats', {}) or {}
        else:
            track_cond_stats = stats.get('track_condition_stats', {}) or {}

        going_key_map = {
            'firm': ['firm', 'good 1', 'good 2'],
            'good': ['good', 'good 3', 'good 4', 'dead'],
            'soft': ['soft', 'soft 5', 'soft 6', 'yielding'],
            'heavy': ['heavy', 'soft 7', 'soft 8', 'heavy 8', 'heavy 9', 'heavy 10'],
        }

        relevant_keys = going_key_map.get(going_cat, [])
        total_starts = 0
        total_wins = 0

        for key in relevant_keys:
            cond_data = track_cond_stats.get(key, {})
            if isinstance(cond_data, dict):
                total_starts += cond_data.get('starts', 0)
                total_wins += cond_data.get('wins', 0)

        if total_starts == 0:
            going_suit = runner.get('goingSuitability', 0) or 0
            try:
                going_suit = float(going_suit)
            except (ValueError, TypeError):
                going_suit = 0

            if going_suit >= 1.1:
                score += 15
            elif going_suit >= 0.9:
                score += 5
            elif going_suit > 0:
                score -= 10
            else:
                score -= 5
                if going_cat == 'heavy':
                    score -= 10
        else:
            win_rate = total_wins / total_starts if total_starts > 0 else 0
            if total_starts >= 3 and win_rate >= 0.25:
                score += 30
            elif total_starts >= 2 and win_rate >= 0.2:
                score += 20
            elif total_starts >= 1 and win_rate > 0:
                score += 10
            elif total_starts >= 3 and win_rate == 0:
                score -= 20
            elif total_starts >= 1 and win_rate == 0:
                score -= 10

        dist_m = ctx.get('distance_m', 0)
        if dist_m > 0:
            dist_sr = 0
            try:
                dist_sr = float(runner.get('distanceSR', 0) or 0)
            except (ValueError, TypeError):
                pass
            if dist_sr >= 25:
                score += 10
            elif dist_sr >= 15:
                score += 5
            elif dist_sr > 0:
                pass
            else:
                score -= 5

        course_sr = 0
        try:
            course_sr = float(runner.get('courseSR', 0) or 0)
        except (ValueError, TypeError):
            pass
        if course_sr >= 20:
            score += 10
        elif course_sr >= 10:
            score += 5

        return max(0.0, min(100.0, score))

    def _check_qualification(self, runner: dict, score: float, race_info: dict,
                             ctx: dict, adj_min_score: float, adj_min_mc: float) -> tuple:
        qual_log = []
        qualifies = True

        qual_log.append(f"CONTEXT: {ctx.get('context_label', '?')} | field={ctx.get('field_size',0)} | "
                        f"tier={ctx.get('race_tier',0)} | comp={ctx.get('competitiveness',0):.2f} | "
                        f"going={ctx.get('going_category','?')} | dist={ctx.get('distance_m',0)}m")
        qual_log.append(f"ADAPTIVE THRESHOLDS: score>={adj_min_score:.1f} | mc>={adj_min_mc:.1%}")

        if score >= adj_min_score:
            qual_log.append(f"PASS: banker_score {score:.1f} >= {adj_min_score:.1f}")
        else:
            qual_log.append(f"FAIL: banker_score {score:.1f} < {adj_min_score:.1f}")
            qualifies = False

        mc_win_prob = runner.get('winPercentage', 0) / 100.0
        if mc_win_prob >= adj_min_mc:
            qual_log.append(f"PASS: MC win prob {mc_win_prob:.1%} >= {adj_min_mc:.0%}")
        else:
            qual_log.append(f"FAIL: MC win prob {mc_win_prob:.1%} < {adj_min_mc:.0%}")
            qualifies = False

        market_odds = runner.get('marketOdds', 999)
        if market_odds <= MAX_MARKET_ODDS:
            qual_log.append(f"PASS: market odds ${market_odds:.2f} <= ${MAX_MARKET_ODDS:.2f}")
        else:
            qual_log.append(f"FAIL: market odds ${market_odds:.2f} > ${MAX_MARKET_ODDS:.2f}")
            qualifies = False

        if ctx.get('is_maiden', False):
            qual_log.append(f"FAIL: maiden race ({ctx.get('race_class', '')})")
            qualifies = False
        else:
            qual_log.append(f"PASS: not a maiden race")

        field_size = ctx.get('field_size', 0)
        if field_size >= MIN_FIELD_SIZE:
            qual_log.append(f"PASS: field size {field_size} >= {MIN_FIELD_SIZE}")
        else:
            qual_log.append(f"FAIL: field size {field_size} < {MIN_FIELD_SIZE}")
            qualifies = False

        tier = None
        if qualifies:
            tier = 'banker'

        return qualifies, tier, qual_log

    def _determine_tier(self, score: float, mc_win_prob: float,
                        max_danger_level: float, adj_min_score: float) -> str:
        strong_score_threshold = adj_min_score + STRONG_BANKER_SCORE_OFFSET
        strong_mc_threshold = BASE_MIN_MC_WIN_PROB + STRONG_MC_WIN_PROB_OFFSET

        if (score >= strong_score_threshold and
                mc_win_prob >= strong_mc_threshold and
                max_danger_level <= STRONG_MAX_DANGER_LEVEL):
            return 'strong_banker'
        return 'banker'

    def _generate_exotic_combos(self, banker_runner: dict, all_runners: list) -> list:
        banker_name = banker_runner.get('horse', 'Unknown')

        others = [r for r in all_runners if r.get('horse', '') != banker_name]
        others_sorted = sorted(others, key=lambda r: r.get('winPercentage', 0), reverse=True)

        top_others = others_sorted[:4]

        combos = []

        for other in top_others:
            combos.append({
                'type': 'exacta',
                'legs': [
                    {'position': 1, 'horse': banker_name},
                    {'position': 2, 'horse': other.get('horse', 'Unknown')},
                ],
                'combinations': 1,
            })

        if len(top_others) >= 2:
            trifecta_others = top_others[:3] if len(top_others) >= 3 else top_others[:2]
            trifecta_horses = [r.get('horse', 'Unknown') for r in trifecta_others]
            num_combos = len(trifecta_horses) * (len(trifecta_horses) - 1) if len(trifecta_horses) >= 2 else 0
            combos.append({
                'type': 'trifecta',
                'legs': [
                    {'position': 1, 'horse': banker_name},
                    {'position': 2, 'horses': trifecta_horses},
                    {'position': 3, 'horses': trifecta_horses},
                ],
                'combinations': num_combos,
            })

        return combos

    def _get_staking(self, tier: str) -> dict:
        if tier == 'strong_banker':
            return {
                'win_stake': STRONG_BANKER_WIN_STAKE,
                'exacta_stake': STRONG_BANKER_EXACTA_STAKE,
            }
        else:
            return {
                'win_stake': BANKER_WIN_STAKE,
                'exacta_stake': BANKER_EXACTA_STAKE,
            }

    def backtest(self, historical_races: list) -> dict:
        total_races = len(historical_races)
        bankers_identified = 0
        strong_bankers_identified = 0
        banker_winners = 0
        strong_banker_winners = 0
        strong_banker_count = 0
        total_banker_odds = 0.0
        total_pnl = 0.0
        detailed_log = []

        sub2_count = 0
        sub2_winners = 0
        sub2_pnl = 0.0

        for race_data in historical_races:
            race_info = race_data.get('race_info', {})
            runners = race_data.get('runners', [])
            actual_winner = race_data.get('actual_winner', '')

            if not runners:
                continue

            fav = min(runners, key=lambda r: r.get('marketOdds', 999))
            fav_odds = fav.get('marketOdds', 999)
            if fav_odds < 2.0:
                sub2_count += 1
                if fav.get('horse', '') == actual_winner:
                    sub2_winners += 1
                    sub2_pnl += (fav_odds - 1.0)
                else:
                    sub2_pnl -= 1.0

            results = self.detect_bankers(list(runners), race_info)

            for runner in results:
                if not runner.get('banker_flag', False):
                    continue

                bankers_identified += 1
                tier = runner.get('banker_tier', 'banker')
                odds = runner.get('marketOdds', 0)
                total_banker_odds += odds
                horse = runner.get('horse', '')

                if tier == 'strong_banker':
                    strong_bankers_identified += 1
                    strong_banker_count += 1

                won = (horse == actual_winner)
                if won:
                    banker_winners += 1
                    total_pnl += (odds - 1.0) * runner.get('banker_staking', {}).get('win_stake', 1.5)
                    if tier == 'strong_banker':
                        strong_banker_winners += 1
                else:
                    total_pnl -= runner.get('banker_staking', {}).get('win_stake', 1.5)

                detailed_log.append({
                    'horse': horse,
                    'tier': tier,
                    'score': runner.get('banker_score', 0),
                    'odds': odds,
                    'won': won,
                    'components': runner.get('banker_score_components', {}),
                    'race_class': race_info.get('raceClass', ''),
                    'track': race_info.get('track', ''),
                })

        banker_strike_rate = banker_winners / bankers_identified if bankers_identified > 0 else 0.0
        strong_sr = strong_banker_winners / strong_banker_count if strong_banker_count > 0 else 0.0
        avg_odds = total_banker_odds / bankers_identified if bankers_identified > 0 else 0.0
        flat_roi = (total_pnl / (bankers_identified * BANKER_WIN_STAKE)) * 100 if bankers_identified > 0 else 0.0

        sub2_sr = sub2_winners / sub2_count if sub2_count > 0 else 0.0
        sub2_roi = (sub2_pnl / sub2_count) * 100 if sub2_count > 0 else 0.0

        return {
            'total_races': total_races,
            'bankers_identified': bankers_identified,
            'strong_bankers_identified': strong_bankers_identified,
            'banker_winners': banker_winners,
            'banker_strike_rate': round(banker_strike_rate, 4),
            'strong_banker_strike_rate': round(strong_sr, 4),
            'avg_banker_odds': round(avg_odds, 2),
            'flat_stake_roi': round(flat_roi, 2),
            'sub2_fav_comparison': {
                'count': sub2_count,
                'winners': sub2_winners,
                'strike_rate': round(sub2_sr, 4),
                'roi': round(sub2_roi, 2),
            },
            'needs_recalibration': banker_strike_rate < MIN_STRIKE_RATE,
            'detailed_log': detailed_log,
        }

    def get_performance_stats(self) -> dict:
        total = len(self.results)
        winners = sum(1 for r in self.results if r.get('won', False))
        strike_rate = winners / total if total > 0 else 0.0

        total_pnl = 0.0
        total_odds = 0.0
        total_scores = 0.0
        by_tier = {
            'strong_banker': {'count': 0, 'winners': 0, 'pnl': 0.0},
            'banker': {'count': 0, 'winners': 0, 'pnl': 0.0},
        }

        for r in self.results:
            odds = r.get('sp_odds', 0)
            total_odds += odds
            tier = r.get('tier', 'banker')
            score = r.get('banker_score', 0)
            total_scores += score
            stake = STRONG_BANKER_WIN_STAKE if tier == 'strong_banker' else BANKER_WIN_STAKE

            if tier in by_tier:
                by_tier[tier]['count'] += 1

            if r.get('won', False):
                pnl = (odds - 1.0) * stake
                total_pnl += pnl
                if tier in by_tier:
                    by_tier[tier]['winners'] += 1
                    by_tier[tier]['pnl'] += pnl
            else:
                total_pnl -= stake
                if tier in by_tier:
                    by_tier[tier]['pnl'] -= stake

        total_staked = sum(
            (STRONG_BANKER_WIN_STAKE if r.get('tier') == 'strong_banker' else BANKER_WIN_STAKE)
            for r in self.results
        )
        roi_pct = (total_pnl / total_staked) * 100 if total_staked > 0 else 0.0
        avg_odds = total_odds / total if total > 0 else 0.0
        avg_score = total_scores / total if total > 0 else 0.0

        tier_stats = {}
        for tier_name, data in by_tier.items():
            cnt = data['count']
            w = data['winners']
            staked = cnt * (STRONG_BANKER_WIN_STAKE if tier_name == 'strong_banker' else BANKER_WIN_STAKE)
            tier_stats[tier_name] = {
                'count': cnt,
                'winners': w,
                'strike_rate': round(w / cnt, 4) if cnt > 0 else 0.0,
                'roi': round((data['pnl'] / staked) * 100, 2) if staked > 0 else 0.0,
            }

        recent = self.results[-20:] if len(self.results) > 20 else list(self.results)

        return {
            'total_selections': total,
            'winners': winners,
            'strike_rate': round(strike_rate, 4),
            'roi_pct': round(roi_pct, 2),
            'avg_odds': round(avg_odds, 2),
            'avg_banker_score': round(avg_score, 2),
            'by_tier': tier_stats,
            'recent_form': recent,
        }

    def record_result(self, horse_name: str, race_id: str, won: bool, position: int, sp_odds: float):
        matching = [s for s in self.selections_log if s.get('horse', '') == horse_name]
        tier = matching[-1].get('tier', 'banker') if matching else 'banker'
        score = matching[-1].get('score', 0) if matching else 0

        self.results.append({
            'horse': horse_name,
            'race_id': race_id,
            'won': won,
            'position': position,
            'sp_odds': sp_odds,
            'tier': tier,
            'banker_score': score,
        })

        result_str = "WON" if won else f"P{position}"
        _log(f"Result recorded: {horse_name} ({race_id}) - {result_str} @ ${sp_odds:.2f}")


_banker_detector = None


def get_banker_detector():
    global _banker_detector
    if _banker_detector is None:
        _banker_detector = BankerDetector()
    return _banker_detector
