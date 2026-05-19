"""
Predicts how "predictable" a race is before it runs.
High predictability races get normal confidence, unpredictable races get reduced confidence.
"""
import numpy as np
from typing import Dict, List, Optional, Any
from datetime import datetime

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


METRO_TRACKS = {
    'flemington', 'caulfield', 'moonee valley', 'sandown',
    'randwick', 'royal randwick', 'rosehill', 'warwick farm', 'canterbury',
    'eagle farm', 'doomben',
    'morphettville', 'cheltenham',
    'ascot', 'belmont',
}

GROUP_KEYWORDS = {'group 1', 'group 2', 'group 3', 'g1', 'g2', 'g3', 'grp1', 'grp2', 'grp3'}
LISTED_KEYWORDS = {'listed', 'open', 'stakes'}
BM_HCP_KEYWORDS = {'bm', 'benchmark', 'hcp', 'handicap', 'quality', 'conditions'}
MAIDEN_CL_KEYWORDS = {'maiden', 'cl', 'class', 'mdn', 'restricted'}

WET_GOING = {'soft', 'heavy', 'wet', 'slow', 'yielding'}


class PredictabilityMetaModel:
    def __init__(self):
        self.model = None
        self.is_fitted = False
        self.scaler = None
        self.training_stats = {}
        self.feature_names = [
            'field_size', 'class_tier', 'is_wet', 'fav_strength_val',
            'form_coverage', 'odds_concentration', 'distance_bucket_val',
            'has_first_starters', 'market_confidence_val',
        ]

    def _parse_class_tier(self, race_class: str) -> int:
        if not race_class:
            return 4
        rc = race_class.lower().strip()
        if any(k in rc for k in GROUP_KEYWORDS):
            return 1
        if any(k in rc for k in LISTED_KEYWORDS):
            return 2
        if any(k in rc for k in BM_HCP_KEYWORDS):
            return 3
        return 4

    def _compute_hhi(self, odds_list: list) -> float:
        if not odds_list or len(odds_list) == 0:
            return 0.0
        implied = []
        for o in odds_list:
            try:
                o_val = float(o)
                if o_val > 0:
                    implied.append(1.0 / o_val)
            except (ValueError, TypeError):
                continue
        if not implied:
            return 0.0
        total = sum(implied)
        if total <= 0:
            return 0.0
        normalised = [p / total for p in implied]
        return sum(p ** 2 for p in normalised)

    def _distance_bucket_val(self, distance_m: int) -> int:
        if distance_m < 1200:
            return 0
        elif distance_m <= 1600:
            return 1
        else:
            return 2

    def _distance_bucket_label(self, distance_m: int) -> str:
        if distance_m < 1200:
            return 'sprint'
        elif distance_m <= 1600:
            return 'mile'
        else:
            return 'staying'

    def _fav_strength_label(self, fav_odds: float) -> str:
        if fav_odds < 3.0:
            return 'strong'
        elif fav_odds <= 6.0:
            return 'moderate'
        else:
            return 'weak'

    def _fav_strength_val(self, fav_odds: float) -> float:
        if fav_odds <= 0:
            return 2.0
        return min(fav_odds, 20.0)

    def _field_size_bucket(self, n: int) -> str:
        if n < 8:
            return 'small'
        elif n <= 12:
            return 'medium'
        else:
            return 'large'

    def extract_meta_features(self, race_info: dict) -> dict:
        n_runners = int(race_info.get('n_runners', 0))
        race_class = str(race_info.get('race_class', ''))
        distance_m = int(race_info.get('distance_m', 1400))
        going = str(race_info.get('going', '')).lower()
        favourite_odds = float(race_info.get('favourite_odds', 5.0))
        avg_form_coverage = float(race_info.get('avg_form_coverage', 0.5))
        market_confidence = float(race_info.get('market_confidence', 0.0))
        has_first_starters = bool(race_info.get('has_first_starters', False))
        odds_spread = float(race_info.get('odds_spread', 0.0))
        top3_odds_sum = float(race_info.get('top3_odds_sum', 0.0))

        odds_list = race_info.get('odds_list', [])
        if not odds_list and top3_odds_sum > 0 and favourite_odds > 0:
            odds_list = [favourite_odds]

        is_wet = any(w in going for w in WET_GOING)
        class_tier = self._parse_class_tier(race_class)
        hhi = self._compute_hhi(odds_list) if odds_list else 0.0

        return {
            'field_size_bucket': self._field_size_bucket(n_runners),
            'field_size': n_runners,
            'class_tier': class_tier,
            'is_wet_track': is_wet,
            'is_wet': 1 if is_wet else 0,
            'fav_strength': self._fav_strength_label(favourite_odds),
            'fav_strength_val': self._fav_strength_val(favourite_odds),
            'form_coverage_pct': avg_form_coverage,
            'form_coverage': avg_form_coverage,
            'odds_concentration': hhi,
            'distance_bucket': self._distance_bucket_label(distance_m),
            'distance_bucket_val': self._distance_bucket_val(distance_m),
            'has_first_starters': 1 if has_first_starters else 0,
            'market_confidence_val': market_confidence,
        }

    def _features_to_vector(self, meta: dict) -> list:
        return [
            meta.get('field_size', 8),
            meta.get('class_tier', 4),
            meta.get('is_wet', 0),
            meta.get('fav_strength_val', 5.0),
            meta.get('form_coverage', 0.5),
            meta.get('odds_concentration', 0.0),
            meta.get('distance_bucket_val', 1),
            meta.get('has_first_starters', 0),
            meta.get('market_confidence_val', 0.0),
        ]

    def fit(self, race_histories: list):
        if not SKLEARN_AVAILABLE:
            self.training_stats = {'error': 'sklearn not available'}
            return

        if len(race_histories) < 10:
            self.training_stats = {'error': f'Need at least 10 records, got {len(race_histories)}'}
            return

        X = []
        y = []
        for record in race_histories:
            meta = record if 'field_size' in record else self.extract_meta_features(record)
            vec = self._features_to_vector(meta)
            target = 1 if record.get('fav_won', False) else 0
            X.append(vec)
            y.append(target)

        X = np.array(X, dtype=float)
        y = np.array(y, dtype=int)

        n_pos = int(y.sum())
        n_neg = int(len(y) - n_pos)
        if n_pos < 3 or n_neg < 3:
            self.training_stats = {'error': f'Insufficient class balance: {n_pos} positives, {n_neg} negatives'}
            return

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = LogisticRegression(
            max_iter=500,
            class_weight='balanced',
            solver='lbfgs',
            random_state=42,
        )
        self.model.fit(X_scaled, y)
        self.is_fitted = True

        train_preds = self.model.predict_proba(X_scaled)[:, 1]
        from sklearn.metrics import roc_auc_score, accuracy_score
        y_pred_binary = (train_preds > 0.5).astype(int)

        try:
            auc = roc_auc_score(y, train_preds)
        except ValueError:
            auc = 0.0

        self.training_stats = {
            'fitted_at': datetime.now().isoformat(),
            'n_samples': len(y),
            'n_predictable': n_pos,
            'n_chaotic': n_neg,
            'train_auc': round(auc, 4),
            'train_accuracy': round(accuracy_score(y, y_pred_binary), 4),
            'feature_names': self.feature_names,
            'coefficients': {
                name: round(float(coef), 4)
                for name, coef in zip(self.feature_names, self.model.coef_[0])
            },
        }

    def predict_predictability(self, race_info: dict) -> dict:
        meta = self.extract_meta_features(race_info)

        if self.is_fitted and self.model is not None and self.scaler is not None:
            vec = np.array([self._features_to_vector(meta)], dtype=float)
            vec_scaled = self.scaler.transform(vec)
            score = float(self.model.predict_proba(vec_scaled)[0, 1])

            coefs = self.model.coef_[0]
            feature_vals = self._features_to_vector(meta)
            contributions = []
            for name, coef, val in zip(self.feature_names, coefs, feature_vals):
                contributions.append((name, abs(coef * val)))
            contributions.sort(key=lambda x: x[1], reverse=True)
            key_factors = [f"{name} (impact={round(impact, 3)})" for name, impact in contributions[:4]]
        else:
            score = self._heuristic_score(race_info, meta)
            key_factors = self._heuristic_factors(meta)

        if score > 0.7:
            category = 'highly_predictable'
        elif score >= 0.3:
            category = 'normal'
        else:
            category = 'chaotic'

        confidence_modifier = 0.5 + (score * 0.7)
        confidence_modifier = round(max(0.5, min(1.2, confidence_modifier)), 3)

        return {
            'predictability_score': round(score, 4),
            'confidence_modifier': confidence_modifier,
            'category': category,
            'key_factors': key_factors,
            'meta_features': meta,
        }

    def _heuristic_score(self, race_info: dict, meta: dict) -> float:
        score = 0.5

        fav_odds = float(race_info.get('favourite_odds', 5.0))
        if fav_odds < 2.5:
            score += 0.2
        elif fav_odds < 4.0:
            score += 0.1
        elif fav_odds > 8.0:
            score -= 0.15

        n_runners = int(race_info.get('n_runners', 8))
        if n_runners <= 6:
            score += 0.1
        elif n_runners >= 14:
            score -= 0.1

        class_tier = meta.get('class_tier', 4)
        if class_tier <= 2:
            score += 0.1
        elif class_tier == 4:
            score -= 0.1

        if meta.get('is_wet', 0):
            score -= 0.05

        form_cov = meta.get('form_coverage', 0.5)
        if form_cov > 0.8:
            score += 0.05
        elif form_cov < 0.3:
            score -= 0.1

        if meta.get('has_first_starters', 0):
            score -= 0.05

        return max(0.0, min(1.0, score))

    def _heuristic_factors(self, meta: dict) -> list:
        factors = []
        fav_val = meta.get('fav_strength_val', 5.0)
        if fav_val < 3.0:
            factors.append('strong favourite (odds < $3)')
        elif fav_val > 6.0:
            factors.append('weak favourite (odds > $6)')

        fs = meta.get('field_size', 8)
        if fs < 8:
            factors.append(f'small field ({fs} runners)')
        elif fs > 12:
            factors.append(f'large field ({fs} runners)')

        ct = meta.get('class_tier', 4)
        tier_labels = {1: 'Group race', 2: 'Listed/Open', 3: 'BM/Handicap', 4: 'Maiden/CL'}
        factors.append(tier_labels.get(ct, 'Unknown class'))

        if meta.get('is_wet', 0):
            factors.append('wet track conditions')

        return factors[:4]

    def get_report(self) -> dict:
        return {
            'is_fitted': self.is_fitted,
            'model_type': 'LogisticRegression' if self.is_fitted else 'heuristic_fallback',
            'training_stats': self.training_stats,
            'feature_names': self.feature_names,
        }


_instance = None

def get_predictability_model() -> PredictabilityMetaModel:
    global _instance
    if _instance is None:
        _instance = PredictabilityMetaModel()
    return _instance


if __name__ == '__main__':
    model = PredictabilityMetaModel()

    test_race = {
        'n_runners': 8,
        'race_class': 'Group 1',
        'distance_m': 1200,
        'going': 'Good',
        'track': 'Flemington',
        'odds_spread': 40.0,
        'favourite_odds': 2.5,
        'top3_odds_sum': 9.0,
        'has_first_starters': False,
        'avg_form_coverage': 0.9,
        'market_confidence': 5.0,
        'odds_list': [2.5, 3.5, 4.0, 8.0, 12.0, 21.0, 31.0, 51.0],
    }

    result = model.predict_predictability(test_race)
    print(f"Predictability (heuristic): {result}")

    histories = []
    np.random.seed(42)
    for i in range(100):
        fav = np.random.uniform(1.5, 10.0)
        n = np.random.randint(5, 16)
        tier = np.random.choice([1, 2, 3, 4])
        wet = np.random.choice([0, 1], p=[0.7, 0.3])
        cov = np.random.uniform(0.2, 1.0)
        prob_win = 0.6 if fav < 3 else (0.35 if fav < 5 else 0.2)
        if tier <= 2:
            prob_win += 0.1
        if n > 12:
            prob_win -= 0.1
        fav_won = np.random.random() < max(0.05, min(0.9, prob_win))
        histories.append({
            'field_size': n,
            'class_tier': tier,
            'is_wet': wet,
            'fav_strength_val': fav,
            'form_coverage': cov,
            'odds_concentration': np.random.uniform(0.05, 0.4),
            'distance_bucket_val': np.random.choice([0, 1, 2]),
            'has_first_starters': np.random.choice([0, 1]),
            'market_confidence_val': np.random.uniform(2, 25),
            'fav_won': bool(fav_won),
        })

    model.fit(histories)
    print(f"\nReport: {model.get_report()}")

    result2 = model.predict_predictability(test_race)
    print(f"\nPredictability (fitted): {result2}")

    chaotic_race = {
        'n_runners': 16,
        'race_class': 'Maiden',
        'distance_m': 2400,
        'going': 'Heavy',
        'track': 'Ballarat',
        'odds_spread': 80.0,
        'favourite_odds': 7.5,
        'top3_odds_sum': 18.0,
        'has_first_starters': True,
        'avg_form_coverage': 0.3,
        'market_confidence': 22.0,
        'odds_list': [7.5, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 15.0, 18.0, 21.0, 26.0, 31.0, 41.0, 51.0, 71.0, 101.0],
    }
    result3 = model.predict_predictability(chaotic_race)
    print(f"\nChaotic race: {result3}")
