#!/usr/bin/env python3
"""
Monte Carlo Probability Recalibration Engine.

Corrects the systematic favorite-longshot bias in MC simulation outputs. Backtesting revealed:
  - 50-100% predicted: actual win rate ~38% (overconfident on favorites)
  - 30-50% predicted: actual win rate ~26% (overconfident on mid-favorites)
  - 0-5% predicted: actual win rate ~4% (underestimates longshots)

Uses isotonic regression fitted on historical MC predictions vs actual outcomes to produce properly calibrated probabilities.
"""

import sys, os, time, json, pickle
import numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

CALIBRATION_MODEL_PATH = os.path.join(os.path.dirname(__file__), 'calibration_model.json')

class MCRecalibrator:
    """
    Probability recalibration using isotonic regression.

    Isotonic regression fits a non-decreasing step function that maps predicted probabilities to actual win rates. It's ideal for this use case because:
    1. It preserves probability ordering (if A > B predicted, A >= B calibrated)
    2. It handles non-linear biases (our model is more wrong at extremes)
    3. It's non-parametric (no assumptions about bias shape)
    """
    
    def __init__(self):
        self.fitted = False
        self.iso_x = None
        self.iso_y = None
        self.n_samples = 0
        self.n_races = 0
        self.fit_date = None
    
    def fit_from_database(self, min_field_size=6, min_runners_with_sectionals=3, max_races=300):
        """Fit calibration model from historical race results vs MC predictions."""
        import psycopg2
        import psycopg2.extras
        
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("""
            SELECT r.race_id, r.horse_name, r.position, r.sp_odds, 
                   r.distance_m, r.field_size, r.race_date, r.form_string,
                   r.jockey, r.barrier, r.weight_kg
            FROM race_results_history r
            WHERE r.position IS NOT NULL 
              AND r.sp_odds IS NOT NULL 
              AND r.sp_odds > 1.0
              AND r.field_size >= %s
            ORDER BY r.race_date DESC, r.race_id, r.position
        """, (min_field_size,))
        
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        races = defaultdict(list)
        for row in rows:
            races[row['race_id']].append(dict(row))
        
        race_list = list(races.items())
        if max_races and len(race_list) > max_races:
            race_list = race_list[:max_races]
        
        import realistic_simulate as sim_module
        
        all_preds = []
        all_actuals = []
        races_processed = 0
        
        print(f"Fitting recalibration on {len(race_list)} races...")
        t0 = time.time()
        
        for race_id, runners in race_list:
            if len(runners) < 4:
                continue
            
            odds = [r['sp_odds'] for r in runners]
            implied = [1.0 / o for o in odds]
            total = sum(implied)
            market_probs = np.array([p / total for p in implied])
            
            sim_runners = []
            for r in runners:
                sim_runners.append({
                    'horse': r['horse_name'],
                    'form': r.get('form_string', ''),
                    'market_odds': r['sp_odds'],
                    'jockey': r.get('jockey', ''),
                })
            
            result = sim_module.simulate_race_realistic(
                sim_runners, market_probs, n_sims=5000, seed=42
            )
            mc_probs = result['sim_win_probs']
            
            for i, r in enumerate(runners):
                won = 1 if r['position'] == 1 else 0
                all_preds.append(float(mc_probs[i]))
                all_actuals.append(won)
            
            races_processed += 1
            if races_processed % 50 == 0:
                print(f"  Processed {races_processed}/{len(race_list)} races...")
        
        dt = time.time() - t0
        print(f"  MC simulation complete: {races_processed} races, {len(all_preds)} runners in {dt:.1f}s")
        
        preds = np.array(all_preds)
        actuals = np.array(all_actuals)
        
        self._fit_isotonic(preds, actuals)
        self.n_samples = len(preds)
        self.n_races = races_processed
        self.fit_date = time.strftime('%Y-%m-%d %H:%M:%S')
        
        self.save()
        
        print(f"\n  Recalibration model fitted on {self.n_races} races, {self.n_samples} runners")
        self._print_calibration_comparison(preds, actuals)
        
        return self
    
    def _fit_isotonic(self, predictions, actuals):
        """Fit isotonic regression: predictions -> actual_win_rates."""
        sorted_idx = np.argsort(predictions)
        x_sorted = predictions[sorted_idx]
        y_sorted = actuals[sorted_idx].astype(float)
        
        n = len(x_sorted)
        bin_size = max(30, n // 50)
        
        bin_x = []
        bin_y = []
        for i in range(0, n, bin_size):
            chunk_x = x_sorted[i:i+bin_size]
            chunk_y = y_sorted[i:i+bin_size]
            if len(chunk_x) >= 10:
                bin_x.append(float(np.median(chunk_x)))
                bin_y.append(float(np.mean(chunk_y)))
        
        bin_x = np.array(bin_x)
        bin_y = np.array(bin_y)
        
        iso_y = self._pool_adjacent_violators(bin_y)
        
        self.iso_x = bin_x.tolist()
        self.iso_y = iso_y.tolist()
        self.fitted = True
    
    def _pool_adjacent_violators(self, y):
        """Pool Adjacent Violators algorithm for isotonic regression."""
        n = len(y)
        result = y.copy().astype(float)
        weights = np.ones(n)
        
        i = 0
        while i < n - 1:
            if result[i] > result[i + 1]:
                merged = (result[i] * weights[i] + result[i + 1] * weights[i + 1]) / (weights[i] + weights[i + 1])
                result[i] = merged
                result[i + 1] = merged
                weights[i] += weights[i + 1]
                weights[i + 1] = weights[i]
                
                j = i - 1
                while j >= 0 and result[j] > result[j + 1]:
                    merged = (result[j] * weights[j] + result[j + 1] * weights[j + 1]) / (weights[j] + weights[j + 1])
                    result[j] = merged
                    result[j + 1] = merged
                    weights[j] += weights[j + 1]
                    weights[j + 1] = weights[j]
                    j -= 1
            i += 1
        
        return result
    
    def transform(self, probs):
        """Apply calibration to an array of probabilities."""
        if not self.fitted:
            return probs
        
        probs = np.asarray(probs, dtype=float)
        original_shape = probs.shape
        flat = probs.ravel()
        
        iso_x = np.array(self.iso_x)
        iso_y = np.array(self.iso_y)
        
        calibrated = np.interp(flat, iso_x, iso_y, left=iso_y[0], right=iso_y[-1])
        
        calibrated = np.clip(calibrated, 0.005, 0.95)
        
        calibrated = calibrated.reshape(original_shape)
        
        return calibrated
    
    def transform_race(self, probs):
        """Apply calibration and re-normalize to sum to 1.0 for a single race."""
        calibrated = self.transform(probs)
        total = calibrated.sum()
        if total > 0:
            calibrated = calibrated / total
        return calibrated
    
    def save(self, path=None):
        """Save calibration model to JSON."""
        path = path or CALIBRATION_MODEL_PATH
        data = {
            'fitted': self.fitted,
            'iso_x': self.iso_x,
            'iso_y': self.iso_y,
            'n_samples': self.n_samples,
            'n_races': self.n_races,
            'fit_date': self.fit_date,
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  Model saved to: {path}")
    
    def load(self, path=None):
        """Load calibration model from JSON."""
        path = path or CALIBRATION_MODEL_PATH
        if not os.path.exists(path):
            print(f"  No calibration model found at {path}")
            return False
        
        with open(path) as f:
            data = json.load(f)
        
        self.fitted = data['fitted']
        self.iso_x = data['iso_x']
        self.iso_y = data['iso_y']
        self.n_samples = data.get('n_samples', 0)
        self.n_races = data.get('n_races', 0)
        self.fit_date = data.get('fit_date', 'unknown')
        return True
    
    def _print_calibration_comparison(self, preds, actuals):
        """Show before/after calibration curves."""
        calibrated = self.transform(preds)
        
        bands = [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), 
                 (0.20, 0.30), (0.30, 0.50), (0.50, 1.0)]
        
        print(f"\n  {'Band':<12s} {'Raw Pred':>10s} {'Calibrated':>10s} {'Actual':>10s} {'Raw Gap':>10s} {'Cal Gap':>10s} {'Count':>8s}")
        print("  " + "-" * 65)
        
        for low, high in bands:
            mask = (preds >= low) & (preds < high)
            if mask.sum() > 0:
                raw_avg = preds[mask].mean() * 100
                cal_avg = calibrated[mask].mean() * 100
                actual_avg = actuals[mask].mean() * 100
                raw_gap = raw_avg - actual_avg
                cal_gap = cal_avg - actual_avg
                count = int(mask.sum())
                print(f"  {f'{low:.0%}-{high:.0%}':<12s} {raw_avg:>9.2f}% {cal_avg:>9.2f}% {actual_avg:>9.2f}% {raw_gap:>+9.2f}% {cal_gap:>+9.2f}% {count:>8d}")


def get_calibrator():
    """Get a loaded calibrator instance, fitting if needed."""
    cal = MCRecalibrator()
    if cal.load():
        return cal
    
    print("No calibration model found. Fitting from database...")
    cal.fit_from_database(max_races=200)
    return cal


if __name__ == '__main__':
    max_races = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    cal = MCRecalibrator()
    cal.fit_from_database(max_races=max_races)
