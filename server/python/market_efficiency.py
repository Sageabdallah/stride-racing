"""Market efficiency analyzer that segments races by efficiency level."""
import math
from typing import Dict, List, Optional, Any
from datetime import datetime


METRO_TRACKS = {
    'flemington', 'caulfield', 'moonee valley', 'sandown',
    'randwick', 'royal randwick', 'rosehill', 'warwick farm', 'canterbury',
    'eagle farm', 'doomben',
    'morphettville', 'cheltenham',
    'ascot', 'belmont',
}

GROUP_KEYWORDS = {'group 1', 'group 2', 'group 3', 'g1', 'g2', 'g3', 'grp1', 'grp2', 'grp3'}

SEGMENT_EDGE_THRESHOLDS = {
    'ultra_efficient': 0.05,
    'efficient': 0.03,
    'moderate': 0.02,
    'inefficient': 0.01,
    'thin': 1.0,
}


class MarketEfficiencyAnalyzer:
    def __init__(self):
        self.history = []
        self.segment_stats = {}

    def _implied_probs(self, odds: list) -> list:
        probs = []
        for o in odds:
            try:
                o_val = float(o)
                if o_val > 0:
                    probs.append(1.0 / o_val)
                else:
                    probs.append(0.0)
            except (ValueError, TypeError):
                probs.append(0.0)
        return probs

    def _overround(self, odds: list) -> float:
        probs = self._implied_probs(odds)
        return (sum(probs) - 1.0) * 100.0

    def compute_herfindahl_index(self, odds: list) -> float:
        probs = self._implied_probs(odds)
        total = sum(probs)
        if total <= 0:
            return 0.0
        normalised = [p / total for p in probs]
        return sum(p ** 2 for p in normalised)

    def _is_group_race(self, race_class: str) -> bool:
        if not race_class:
            return False
        rc = race_class.lower().strip()
        return any(k in rc for k in GROUP_KEYWORDS)

    def _is_metro(self, track: str) -> bool:
        if not track:
            return False
        return track.lower().strip() in METRO_TRACKS

    def _compute_efficiency_score(self, overround_pct: float, n_runners: int,
                                   race_class: str, track: str) -> tuple:
        is_group = self._is_group_race(race_class)
        is_metro = self._is_metro(track)

        if n_runners < 6 or overround_pct <= 0.0 or overround_pct > 25.0:
            return max(0.0, min(0.19, 0.2 - overround_pct / 200)), 'thin'

        if overround_pct < 3.0 and n_runners >= 8 and is_group:
            score = 0.9 + min(0.1, (3.0 - overround_pct) / 30.0)
            return min(1.0, score), 'ultra_efficient'

        if overround_pct < 3.0 and (n_runners >= 8 or is_metro):
            return 0.9, 'ultra_efficient'

        if overround_pct < 8.0:
            base = 0.7 + (8.0 - overround_pct) / 25.0
            if is_metro:
                base += 0.02
            return min(0.89, max(0.7, base)), 'efficient'

        if overround_pct < 15.0:
            base = 0.4 + (15.0 - overround_pct) / 23.3
            return min(0.69, max(0.4, base)), 'moderate'

        base = 0.2 + (25.0 - min(overround_pct, 25.0)) / 50.0
        return min(0.39, max(0.2, base)), 'inefficient'

    def _compute_market_depth(self, odds: list) -> str:
        n = len(odds)
        if n >= 12:
            return 'deep'
        elif n >= 7:
            return 'medium'
        else:
            return 'shallow'

    def _compute_price_gaps(self, odds: list) -> list:
        if len(odds) < 2:
            return []
        sorted_odds = sorted(odds)
        gaps = []
        for i in range(1, len(sorted_odds)):
            gaps.append(round(sorted_odds[i] - sorted_odds[i - 1], 2))
        return gaps

    def detect_anomalies(self, odds: list, race_info: dict) -> list:
        anomalies = []
        if not odds or len(odds) == 0:
            anomalies.append('no_odds_available')
            return anomalies

        sorted_odds = sorted(odds)
        overround = self._overround(odds)

        for i in range(1, len(sorted_odds)):
            if sorted_odds[i - 1] > 0 and sorted_odds[i] / sorted_odds[i - 1] > 2.0:
                anomalies.append(
                    f'large_price_gap: ${sorted_odds[i-1]:.1f} to ${sorted_odds[i]:.1f} '
                    f'(ratio {sorted_odds[i]/sorted_odds[i-1]:.1f}x)'
                )

        if overround > 30.0:
            anomalies.append(f'very_high_overround: {overround:.1f}%')
        elif overround < 1.0 and len(odds) >= 4:
            anomalies.append(f'unusually_low_overround: {overround:.1f}%')

        probs = self._implied_probs(odds)
        total_prob = sum(probs)
        if total_prob > 0:
            max_prob = max(probs) / total_prob
            if max_prob > 0.6:
                fav_odds = min(odds)
                anomalies.append(
                    f'dominant_favourite: {max_prob*100:.1f}% implied prob '
                    f'(odds ${fav_odds:.1f})'
                )

        if len(odds) >= 4:
            top4 = sorted(probs, reverse=True)[:4]
            if total_prob > 0:
                top4_share = sum(top4) / total_prob
                if top4_share > 0.85:
                    anomalies.append(f'top_heavy_market: top 4 runners hold {top4_share*100:.1f}% of probability')

        time_to_race = race_info.get('time_to_race_mins', None)
        if time_to_race is not None and time_to_race > 120 and overround > 15:
            anomalies.append(f'early_market_wide_spread: {overround:.1f}% overround {time_to_race}min before race')

        return anomalies

    def analyze_market(self, race_odds: list, race_info: dict) -> dict:
        valid_odds = []
        for o in race_odds:
            try:
                o_val = float(o)
                if o_val > 1.0:
                    valid_odds.append(o_val)
            except (ValueError, TypeError):
                continue

        if not valid_odds:
            return {
                'overround_pct': 0.0,
                'efficiency_score': 0.0,
                'segment': 'thin',
                'exploitability': 1.0,
                'recommended_edge_threshold': 1.0,
                'market_depth': 'shallow',
                'price_gaps': [],
                'anomalies': ['no_valid_odds'],
                'hhi': 0.0,
            }

        overround_pct = round(self._overround(valid_odds), 2)
        
        if overround_pct <= 0.0:
            overround_pct = 0.0
            anomalies_list = ['incomplete_market_data']
            n_runners = len(valid_odds)
            efficiency_score = 0.05
            segment = 'thin'
        else:
            n_runners = len(valid_odds)
            race_class = str(race_info.get('class', race_info.get('race_class', '')))
            track = str(race_info.get('track', ''))

            efficiency_score, segment = self._compute_efficiency_score(
                overround_pct, n_runners, race_class, track
            )
            anomalies_list = self.detect_anomalies(valid_odds, race_info)

        efficiency_score = round(efficiency_score, 4)
        exploitability = round(max(0.0, min(1.0, 1.0 - efficiency_score)), 4)
        edge_threshold = SEGMENT_EDGE_THRESHOLDS.get(segment, 0.05)
        market_depth = self._compute_market_depth(valid_odds)
        price_gaps = self._compute_price_gaps(valid_odds)
        hhi = round(self.compute_herfindahl_index(valid_odds), 4)

        return {
            'overround_pct': overround_pct,
            'efficiency_score': efficiency_score,
            'segment': segment,
            'exploitability': exploitability,
            'recommended_edge_threshold': edge_threshold,
            'market_depth': market_depth,
            'price_gaps': price_gaps,
            'anomalies': anomalies_list,
            'hhi': hhi,
            'n_runners': n_runners,
        }

    def record_result(self, race_id: str, efficiency_data: dict,
                      fav_won: bool, model_correct: bool):
        entry = {
            'race_id': race_id,
            'timestamp': datetime.now().isoformat(),
            'segment': efficiency_data.get('segment', 'unknown'),
            'efficiency_score': efficiency_data.get('efficiency_score', 0),
            'overround_pct': efficiency_data.get('overround_pct', 0),
            'fav_won': fav_won,
            'model_correct': model_correct,
        }
        self.history.append(entry)

        seg = entry['segment']
        if seg not in self.segment_stats:
            self.segment_stats[seg] = {
                'total_races': 0,
                'fav_wins': 0,
                'model_wins': 0,
                'total_overround': 0.0,
            }
        stats = self.segment_stats[seg]
        stats['total_races'] += 1
        if fav_won:
            stats['fav_wins'] += 1
        if model_correct:
            stats['model_wins'] += 1
        stats['total_overround'] += entry['overround_pct']

    def get_segment_performance(self) -> dict:
        result = {}
        for seg, stats in self.segment_stats.items():
            total = stats['total_races']
            if total == 0:
                continue
            result[seg] = {
                'total_races': total,
                'fav_win_rate': round(stats['fav_wins'] / total, 4),
                'model_win_rate': round(stats['model_wins'] / total, 4),
                'avg_overround': round(stats['total_overround'] / total, 2),
                'edge_threshold': SEGMENT_EDGE_THRESHOLDS.get(seg, 0.05),
                'model_has_edge': stats['model_wins'] / total > stats['fav_wins'] / total,
            }
        return result

    def get_recommended_strategy(self, efficiency_data: dict) -> dict:
        segment = efficiency_data.get('segment', 'moderate')
        exploitability = efficiency_data.get('exploitability', 0.5)
        overround = efficiency_data.get('overround_pct', 10.0)

        strategies = {
            'ultra_efficient': {
                'bet_size_modifier': 0.5,
                'approach': 'minimal_betting',
                'description': 'Very efficient market. Only bet with strong edge (5%+). Reduce stake size.',
                'selection_method': 'value_only',
                'avoid': False,
                'max_bets_per_race': 1,
            },
            'efficient': {
                'bet_size_modifier': 0.75,
                'approach': 'selective_value',
                'description': 'Efficient market. Focus on value selections with 3%+ edge.',
                'selection_method': 'value_weighted',
                'avoid': False,
                'max_bets_per_race': 2,
            },
            'moderate': {
                'bet_size_modifier': 1.0,
                'approach': 'standard',
                'description': 'Moderate efficiency. Standard betting approach with 2%+ edge threshold.',
                'selection_method': 'balanced',
                'avoid': False,
                'max_bets_per_race': 3,
            },
            'inefficient': {
                'bet_size_modifier': 1.25,
                'approach': 'aggressive_value',
                'description': 'Inefficient market with exploitable pricing. Can bet more aggressively with 1%+ edge.',
                'selection_method': 'model_favoured',
                'avoid': False,
                'max_bets_per_race': 4,
            },
            'thin': {
                'bet_size_modifier': 0.0,
                'approach': 'avoid',
                'description': 'Thin/unreliable market. Avoid betting - prices are unreliable.',
                'selection_method': 'none',
                'avoid': True,
                'max_bets_per_race': 0,
            },
        }

        strategy = strategies.get(segment, strategies['moderate']).copy()
        strategy['segment'] = segment
        strategy['edge_threshold'] = SEGMENT_EDGE_THRESHOLDS.get(segment, 0.05)
        strategy['exploitability'] = exploitability
        strategy['overround_pct'] = overround

        return strategy


_instance = None

def get_market_efficiency_analyzer() -> MarketEfficiencyAnalyzer:
    global _instance
    if _instance is None:
        _instance = MarketEfficiencyAnalyzer()
    return _instance


if __name__ == '__main__':
    analyzer = MarketEfficiencyAnalyzer()

    efficient_odds = [2.5, 3.5, 5.0, 8.0, 11.0, 15.0, 21.0, 26.0, 34.0, 51.0]
    efficient_info = {'track': 'Flemington', 'class': 'Group 1', 'distance': 1200, 'going': 'Good'}
    result1 = analyzer.analyze_market(efficient_odds, efficient_info)
    print(f"Efficient market: {result1}")
    print(f"Strategy: {analyzer.get_recommended_strategy(result1)}\n")

    moderate_odds = [3.0, 4.5, 6.0, 8.0, 12.0, 18.0, 26.0, 41.0]
    moderate_info = {'track': 'Sandown', 'class': 'BM78', 'distance': 1600, 'going': 'Soft'}
    result2 = analyzer.analyze_market(moderate_odds, moderate_info)
    print(f"Moderate market: {result2}")
    print(f"Strategy: {analyzer.get_recommended_strategy(result2)}\n")

    thin_odds = [1.8, 5.0, 8.0, 51.0, 101.0]
    thin_info = {'track': 'Ballarat', 'class': 'Maiden', 'distance': 2000, 'going': 'Heavy'}
    result3 = analyzer.analyze_market(thin_odds, thin_info)
    print(f"Thin market: {result3}")
    print(f"Strategy: {analyzer.get_recommended_strategy(result3)}\n")

    analyzer.record_result('R1', result1, fav_won=True, model_correct=True)
    analyzer.record_result('R2', result2, fav_won=False, model_correct=True)
    analyzer.record_result('R3', result3, fav_won=False, model_correct=False)
    print(f"Segment performance: {analyzer.get_segment_performance()}")

    dominant_odds = [1.3, 8.0, 15.0, 21.0, 51.0, 101.0]
    dominant_info = {'track': 'Randwick', 'class': 'Group 2', 'distance': 1400}
    result4 = analyzer.analyze_market(dominant_odds, dominant_info)
    print(f"\nDominant fav market: {result4}")
    print(f"HHI: {analyzer.compute_herfindahl_index(dominant_odds)}")
