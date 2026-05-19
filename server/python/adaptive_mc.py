"""Adaptive Monte Carlo that dynamically determines optimal simulation count based on race characteristics, running until convergence or resource limits."""

import time
import math
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Callable, Any


@dataclass
class AdaptiveMCConfig:
    min_sims: int = 1000
    max_sims: int = 20000
    convergence_threshold: float = 0.005
    batch_size: int = 500
    confidence_level: float = 0.95
    time_limit_seconds: float = 30.0


class AdaptiveMCSampler:
    def __init__(self, config: AdaptiveMCConfig = None):
        self.config = config or AdaptiveMCConfig()
        self.stats: Dict[str, Any] = {
            'races_processed': 0,
            'total_sims_used': 0,
            'total_sims_saved': 0,
            'convergence_count': 0,
            'timeout_count': 0,
            'max_sims_count': 0,
            'total_time': 0.0,
        }

    def get_race_complexity_score(self, race_info: dict) -> float:
        score = 50.0

        n_runners = race_info.get('n_runners', 8)
        if n_runners >= 16:
            score += 25
        elif n_runners >= 12:
            score += 20
        elif n_runners >= 9:
            score += 10
        elif n_runners <= 5:
            score -= 15
        elif n_runners <= 3:
            score -= 25

        prob_spread = race_info.get('probability_spread', 0.3)
        if prob_spread < 0.10:
            score += 30
        elif prob_spread < 0.20:
            score += 15
        elif prob_spread > 0.50:
            score -= 20
        elif prob_spread > 0.35:
            score -= 10

        race_class = str(race_info.get('race_class', '')).lower()
        if 'group 1' in race_class or 'g1' in race_class:
            score += 15
        elif 'group 2' in race_class or 'g2' in race_class:
            score += 10
        elif 'group 3' in race_class or 'g3' in race_class:
            score += 5
        elif 'listed' in race_class:
            score += 3
        elif 'maiden' in race_class or 'mdn' in race_class:
            score -= 10

        if not race_info.get('has_dominant_favourite', False):
            score += 15

        field_quality = race_info.get('field_quality', 50.0)
        if field_quality >= 80:
            score += 10
        elif field_quality >= 60:
            score += 5
        elif field_quality < 30:
            score -= 10

        return max(0.0, min(100.0, score))

    def determine_sim_count(self, race_info: dict) -> int:
        complexity = self.get_race_complexity_score(race_info)

        base = 3000
        scale = (complexity - 50.0) / 50.0
        sim_count = base + int(scale * 7000)

        sim_count = max(self.config.min_sims, min(self.config.max_sims, sim_count))

        batch = self.config.batch_size
        sim_count = ((sim_count + batch - 1) // batch) * batch

        return sim_count

    def compute_confidence_intervals(
        self, win_counts: np.ndarray, total_sims: int
    ) -> Dict[int, Tuple[float, float]]:
        z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(
            self.config.confidence_level, 1.96
        )
        z2 = z * z
        n = total_sims
        intervals: Dict[int, Tuple[float, float]] = {}

        for i in range(len(win_counts)):
            p = win_counts[i] / n if n > 0 else 0.0
            denom = 1.0 + z2 / n
            centre = (p + z2 / (2.0 * n)) / denom
            margin = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
            lo = max(0.0, centre - margin)
            hi = min(1.0, centre + margin)
            intervals[i] = (round(lo, 6), round(hi, 6))

        return intervals

    def run_adaptive(
        self, simulate_fn: Callable, n_runners: int
    ) -> Dict[str, Any]:
        """
        Run adaptive Monte Carlo simulation until convergence or resource limits.

        Contract:
        - simulate_fn(n_sims) returns either:
          a) 1D array of shape (n_sims,) with winner indices (0-based integers)
             -> computes win_probs only, place_probs = None
          b) 2D array of shape (n_sims, n_runners) with finishing positions/ranks
             -> computes both win and place probs (top 3)

        Returns:
            Dict with win_probs, place_probs (or None), total_sims, converged, etc.
        """
        cfg = self.config
        start_time = time.time()

        win_counts = np.zeros(n_runners, dtype=np.int64)
        place_counts = np.zeros(n_runners, dtype=np.int64)
        total_sims = 0
        converged = False
        convergence_history: List[Tuple[int, float]] = []

        prev_probs: Optional[np.ndarray] = None
        use_place_probs = None

        initial_batch = max(cfg.min_sims, cfg.batch_size)
        initial_batch = ((initial_batch + cfg.batch_size - 1) // cfg.batch_size) * cfg.batch_size

        results = simulate_fn(initial_batch)
        results_array = np.asarray(results)
        
        if results_array.ndim == 1:
            use_place_probs = False
            winners = results_array.astype(np.int64)
        elif results_array.ndim == 2:
            use_place_probs = True
            winners = results_array[:, 0].astype(np.int64)
        else:
            raise ValueError(
                f"simulate_fn must return 1D or 2D array, got shape {results_array.shape}"
            )

        for w in winners:
            if 0 <= w < n_runners:
                win_counts[w] += 1

        if use_place_probs:
            for sim_row in results_array:
                for pos in range(min(3, len(sim_row))):
                    idx = int(sim_row[pos])
                    if 0 <= idx < n_runners:
                        place_counts[idx] += 1

        total_sims += initial_batch
        prev_probs = win_counts / total_sims
        convergence_history.append((total_sims, float('inf')))

        while total_sims < cfg.max_sims:
            elapsed = time.time() - start_time
            if elapsed >= cfg.time_limit_seconds:
                self.stats['timeout_count'] += 1
                break

            batch_results = simulate_fn(cfg.batch_size)
            batch_array = np.asarray(batch_results)

            if batch_array.ndim == 1:
                if use_place_probs:
                    raise ValueError(
                        "simulate_fn must consistently return 1D or 2D arrays"
                    )
                batch_winners = batch_array.astype(np.int64)
            elif batch_array.ndim == 2:
                if not use_place_probs:
                    raise ValueError(
                        "simulate_fn must consistently return 1D or 2D arrays"
                    )
                batch_winners = batch_array[:, 0].astype(np.int64)
            else:
                raise ValueError(
                    f"simulate_fn must return 1D or 2D array, got shape {batch_array.shape}"
                )

            for w in batch_winners:
                if 0 <= w < n_runners:
                    win_counts[w] += 1

            if use_place_probs:
                for sim_row in batch_array:
                    for pos in range(min(3, len(sim_row))):
                        idx = int(sim_row[pos])
                        if 0 <= idx < n_runners:
                            place_counts[idx] += 1

            total_sims += cfg.batch_size
            current_probs = win_counts / total_sims

            max_change = float(np.max(np.abs(current_probs - prev_probs)))
            convergence_history.append((total_sims, max_change))
            prev_probs = current_probs

            if total_sims >= cfg.min_sims and max_change < cfg.convergence_threshold:
                converged = True
                self.stats['convergence_count'] += 1
                break

        if total_sims >= cfg.max_sims and not converged:
            self.stats['max_sims_count'] += 1

        elapsed_total = time.time() - start_time
        win_probs = win_counts / total_sims if total_sims > 0 else np.zeros(n_runners)
        place_probs = (
            (place_counts / total_sims if total_sims > 0 else np.zeros(n_runners))
            if use_place_probs
            else None
        )
        confidence_intervals = self.compute_confidence_intervals(win_counts, total_sims)

        sims_saved = cfg.max_sims - total_sims
        self.stats['races_processed'] += 1
        self.stats['total_sims_used'] += total_sims
        self.stats['total_sims_saved'] += max(0, sims_saved)
        self.stats['total_time'] += elapsed_total

        return {
            'win_probs': win_probs,
            'place_probs': place_probs,
            'total_sims': total_sims,
            'converged': converged,
            'convergence_history': convergence_history,
            'confidence_intervals': confidence_intervals,
            'elapsed_seconds': round(elapsed_total, 3),
        }

    def get_stats(self) -> dict:
        n = self.stats['races_processed']
        if n == 0:
            return {
                'races_processed': 0,
                'avg_sims_per_race': 0,
                'convergence_rate': 0.0,
                'avg_time_per_race': 0.0,
                'total_sims_saved': 0,
                'timeout_rate': 0.0,
            }

        return {
            'races_processed': n,
            'avg_sims_per_race': round(self.stats['total_sims_used'] / n),
            'convergence_rate': round(self.stats['convergence_count'] / n, 3),
            'avg_time_per_race': round(self.stats['total_time'] / n, 3),
            'total_sims_saved': self.stats['total_sims_saved'],
            'timeout_rate': round(self.stats['timeout_count'] / n, 3),
            'max_sims_hit_rate': round(self.stats['max_sims_count'] / n, 3),
        }
