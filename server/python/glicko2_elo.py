"""
Surface-Conditional Glicko-2 Rating System for Horse Racing

Implements the Glicko-2 algorithm (Mark Glickman) with:
- Separate ratings per surface/going condition (Firm, Good, Soft, Heavy)
- Margin-based scoring instead of binary win/loss
- Rating deviation (uncertainty) and volatility tracking
- Bradley-Terry win probability predictions
"""

import os
import json
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

GLICKO2_SCALE = 173.7178
EPSILON = 1e-6
MAX_ITERATIONS = 100
CONVERGENCE_TOLERANCE = 1e-6

SURFACE_MAP = {
    'firm': 'firm',
    'good': 'good',
    'soft': 'soft',
    'heavy': 'heavy',
    'good to firm': 'firm',
    'good to soft': 'soft',
    'soft to heavy': 'heavy',
    'yielding': 'soft',
    'yielding to soft': 'soft',
    'hard': 'firm',
    'fast': 'firm',
    'dead': 'good',
    'slow': 'soft',
    'synthetic': 'good',
}


def normalize_surface(going: str) -> str:
    if not going:
        return 'good'
    going_lower = going.strip().lower()
    for key, val in SURFACE_MAP.items():
        if going_lower.startswith(key):
            return val
    for key in ['firm', 'good', 'soft', 'heavy']:
        if key in going_lower:
            return key
    return 'good'


def margin_score(position: int, margin: float) -> float:
    if position == 1:
        return 1.0
    elif position == 2:
        return max(0.3, 0.8 - margin * 0.1)
    elif position == 3:
        return max(0.2, 0.6 - margin * 0.1)
    else:
        return max(0.05, 0.4 - (position - 1) * 0.08 - margin * 0.05)


@dataclass
class Glicko2Rating:
    mu: float = 1500.0
    phi: float = 350.0
    sigma: float = 0.06
    n_races: int = 0
    last_race_date: str = None

    @property
    def confidence_interval(self) -> tuple:
        return (self.mu - 2 * self.phi, self.mu + 2 * self.phi)

    @property
    def display_rating(self) -> float:
        return self.mu

    def to_dict(self) -> dict:
        return {
            'mu': round(self.mu, 2),
            'phi': round(self.phi, 2),
            'sigma': round(self.sigma, 6),
            'n_races': self.n_races,
            'last_race_date': self.last_race_date,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'Glicko2Rating':
        return cls(
            mu=d.get('mu', 1500.0),
            phi=d.get('phi', 350.0),
            sigma=d.get('sigma', 0.06),
            n_races=d.get('n_races', 0),
            last_race_date=d.get('last_race_date'),
        )


class Glicko2Engine:

    def __init__(self, tau: float = 0.5, default_mu: float = 1500.0, default_phi: float = 350.0):
        self.tau = tau
        self.default_mu = default_mu
        self.default_phi = default_phi
        self.ratings: Dict[str, Dict[str, Glicko2Rating]] = {}
        self._default_path = os.path.join(
            os.path.dirname(__file__), 'models', 'glicko2_ratings.json'
        )

    def _g(self, phi: float) -> float:
        return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))

    def _E(self, mu: float, mu_j: float, phi_j: float) -> float:
        return 1.0 / (1.0 + math.exp(-self._g(phi_j) * (mu - mu_j)))

    def _new_sigma(self, sigma: float, phi: float, v: float, delta: float) -> float:
        a = math.log(sigma * sigma)
        tau2 = self.tau * self.tau
        phi2 = phi * phi

        def f(x):
            ex = math.exp(x)
            d2 = delta * delta
            num = ex * (d2 - phi2 - v - ex)
            denom = 2.0 * (phi2 + v + ex) ** 2
            return num / denom - (x - a) / tau2

        A = a
        if delta * delta > phi2 + v:
            B = math.log(delta * delta - phi2 - v)
        else:
            k = 1
            while f(a - k * self.tau) < 0:
                k += 1
                if k > MAX_ITERATIONS:
                    break
            B = a - k * self.tau

        fA = f(A)
        fB = f(B)

        for _ in range(MAX_ITERATIONS):
            if abs(B - A) < CONVERGENCE_TOLERANCE:
                break
            C = A + (A - B) * fA / (fB - fA)
            fC = f(C)
            if fC * fB <= 0:
                A = B
                fA = fB
            else:
                fA = fA / 2.0
            B = C
            fB = fC

        return math.exp(A / 2.0)

    def get_rating(self, horse_name: str, surface: str = 'all') -> Glicko2Rating:
        if horse_name not in self.ratings:
            return Glicko2Rating(mu=self.default_mu, phi=self.default_phi)
        return self.ratings[horse_name].get(
            surface,
            Glicko2Rating(mu=self.default_mu, phi=self.default_phi)
        )

    def _ensure_rating(self, horse_name: str, surface: str) -> Glicko2Rating:
        if horse_name not in self.ratings:
            self.ratings[horse_name] = {}
        if surface not in self.ratings[horse_name]:
            self.ratings[horse_name][surface] = Glicko2Rating(
                mu=self.default_mu, phi=self.default_phi
            )
        return self.ratings[horse_name][surface]

    def _update_single_rating(self, rating: Glicko2Rating, opponents: List[Tuple[Glicko2Rating, float]], race_date: str) -> Glicko2Rating:
        if not opponents:
            return rating

        mu = (rating.mu - 1500.0) / GLICKO2_SCALE
        phi = rating.phi / GLICKO2_SCALE

        opp_data = []
        for opp_rating, score in opponents:
            mu_j = (opp_rating.mu - 1500.0) / GLICKO2_SCALE
            phi_j = opp_rating.phi / GLICKO2_SCALE
            g_j = self._g(phi_j)
            e_j = self._E(mu, mu_j, phi_j)
            opp_data.append((mu_j, phi_j, g_j, e_j, score))

        v_inv = sum(g * g * e * (1.0 - e) for _, _, g, e, _ in opp_data)
        if v_inv < EPSILON:
            return rating
        v = 1.0 / v_inv

        delta_sum = sum(g * (s - e) for _, _, g, e, s in opp_data)
        delta = v * delta_sum

        new_sigma = self._new_sigma(rating.sigma, phi, v, delta)

        phi_star = math.sqrt(phi * phi + new_sigma * new_sigma)

        phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)

        mu_new = mu + phi_new * phi_new * delta_sum

        new_mu = GLICKO2_SCALE * mu_new + 1500.0
        new_phi = GLICKO2_SCALE * phi_new

        return Glicko2Rating(
            mu=new_mu,
            phi=new_phi,
            sigma=new_sigma,
            n_races=rating.n_races + 1,
            last_race_date=race_date,
        )

    def update_ratings(self, race_result: dict):
        runners = race_result.get('runners', [])
        if len(runners) < 2:
            return

        raw_surface = race_result.get('surface', 'good')
        surface = normalize_surface(raw_surface)
        race_date = race_result.get('date', datetime.now().strftime('%Y-%m-%d'))

        runner_scores = []
        for r in runners:
            pos = r.get('position', 99)
            margin = r.get('margin', 0.0)
            score = margin_score(pos, margin)
            runner_scores.append({
                'horse_name': r['horse_name'],
                'score': score,
            })

        surfaces_to_update = [surface, 'all']

        for surf in surfaces_to_update:
            current_ratings = {}
            for rs in runner_scores:
                name = rs['horse_name']
                current_ratings[name] = self._ensure_rating(name, surf)

            new_ratings = {}
            for rs in runner_scores:
                name = rs['horse_name']
                rating_i = current_ratings[name]

                opponents = []
                for rs_j in runner_scores:
                    if rs_j['horse_name'] == name:
                        continue
                    opp_rating = current_ratings[rs_j['horse_name']]
                    if rs['score'] > rs_j['score']:
                        pair_score = 1.0
                    elif rs['score'] < rs_j['score']:
                        pair_score = 0.0
                    else:
                        pair_score = 0.5
                    adjusted_score = pair_score * 0.7 + rs['score'] * 0.3
                    opponents.append((opp_rating, adjusted_score))

                new_ratings[name] = self._update_single_rating(
                    rating_i, opponents, race_date
                )

            for name, new_r in new_ratings.items():
                self.ratings[name][surf] = new_r

    def predict_win_proba(self, runners: list, surface: str = 'all') -> dict:
        if not runners:
            return {}

        ratings = {}
        for r in runners:
            name = r if isinstance(r, str) else r.get('horse_name', r.get('name', ''))
            ratings[name] = self.get_rating(name, surface)

        names = list(ratings.keys())
        n = len(names)
        if n == 0:
            return {}
        if n == 1:
            return {names[0]: 1.0}

        bt_scores = {}
        for i, name_i in enumerate(names):
            total = 0.0
            for j, name_j in enumerate(names):
                if i == j:
                    continue
                ri = ratings[name_i].mu
                rj = ratings[name_j].mu
                p = 1.0 / (1.0 + 10.0 ** ((rj - ri) / 400.0))
                total += p
            bt_scores[name_i] = total

        total_bt = sum(bt_scores.values())
        if total_bt < EPSILON:
            equal_p = 1.0 / n
            return {name: equal_p for name in names}

        return {name: score / total_bt for name, score in bt_scores.items()}

    def get_surface_advantage(self, horse_name: str) -> dict:
        if horse_name not in self.ratings:
            return {
                'firm': self.default_mu, 'good': self.default_mu,
                'soft': self.default_mu, 'heavy': self.default_mu,
                'best_surface': 'unknown', 'surface_spread': 0.0,
            }

        surfaces = ['firm', 'good', 'soft', 'heavy']
        result = {}
        best_surface = 'unknown'
        best_rating = -1

        for s in surfaces:
            r = self.get_rating(horse_name, s)
            result[s] = round(r.mu, 2)
            if r.n_races > 0 and r.mu > best_rating:
                best_rating = r.mu
                best_surface = s

        rated_surfaces = [result[s] for s in surfaces if self.get_rating(horse_name, s).n_races > 0]
        spread = max(rated_surfaces) - min(rated_surfaces) if len(rated_surfaces) >= 2 else 0.0

        result['best_surface'] = best_surface
        result['surface_spread'] = round(spread, 2)
        return result

    def save(self, path: str = None):
        path = path or self._default_path
        os.makedirs(os.path.dirname(path), exist_ok=True)

        data = {
            'tau': self.tau,
            'default_mu': self.default_mu,
            'default_phi': self.default_phi,
            'saved_at': datetime.now().isoformat(),
            'n_horses': len(self.ratings),
            'ratings': {},
        }
        for horse, surfaces in self.ratings.items():
            data['ratings'][horse] = {
                surf: r.to_dict() for surf, r in surfaces.items()
            }

        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def load(self, path: str = None):
        path = path or self._default_path
        if not os.path.exists(path):
            return

        with open(path, 'r') as f:
            data = json.load(f)

        self.tau = data.get('tau', self.tau)
        self.default_mu = data.get('default_mu', self.default_mu)
        self.default_phi = data.get('default_phi', self.default_phi)

        self.ratings = {}
        for horse, surfaces in data.get('ratings', {}).items():
            self.ratings[horse] = {
                surf: Glicko2Rating.from_dict(rd) for surf, rd in surfaces.items()
            }

    def get_report(self, horse_name: str = None) -> dict:
        if horse_name:
            if horse_name not in self.ratings:
                return {
                    'horse': horse_name,
                    'status': 'unrated',
                    'ratings': {},
                }
            surfaces = self.ratings[horse_name]
            report = {
                'horse': horse_name,
                'status': 'rated',
                'ratings': {
                    surf: {
                        'mu': round(r.mu, 2),
                        'phi': round(r.phi, 2),
                        'sigma': round(r.sigma, 6),
                        'n_races': r.n_races,
                        'confidence_interval': (
                            round(r.confidence_interval[0], 2),
                            round(r.confidence_interval[1], 2),
                        ),
                        'last_race_date': r.last_race_date,
                    }
                    for surf, r in surfaces.items()
                },
                'surface_advantage': self.get_surface_advantage(horse_name),
            }
            return report

        all_horses = []
        for horse, surfaces in self.ratings.items():
            all_rating = surfaces.get('all', Glicko2Rating())
            all_horses.append({
                'horse': horse,
                'rating': round(all_rating.mu, 2),
                'phi': round(all_rating.phi, 2),
                'n_races': all_rating.n_races,
                'last_race_date': all_rating.last_race_date,
            })

        all_horses.sort(key=lambda x: x['rating'], reverse=True)

        return {
            'total_horses': len(self.ratings),
            'top_rated': all_horses[:20],
            'summary': {
                'avg_rating': round(sum(h['rating'] for h in all_horses) / max(len(all_horses), 1), 2),
                'avg_phi': round(sum(h['phi'] for h in all_horses) / max(len(all_horses), 1), 2),
            } if all_horses else {},
        }


if __name__ == '__main__':
    engine = Glicko2Engine(tau=0.5)

    race1 = {
        'race_id': 'test_001',
        'surface': 'Good 4',
        'distance_m': 1200,
        'class': 'BM72',
        'date': '2026-01-15',
        'runners': [
            {'horse_name': 'Alpha', 'position': 1, 'margin': 0.0, 'sp_odds': 3.5},
            {'horse_name': 'Beta', 'position': 2, 'margin': 1.5, 'sp_odds': 4.0},
            {'horse_name': 'Gamma', 'position': 3, 'margin': 3.0, 'sp_odds': 6.0},
            {'horse_name': 'Delta', 'position': 4, 'margin': 5.0, 'sp_odds': 8.0},
            {'horse_name': 'Epsilon', 'position': 5, 'margin': 8.0, 'sp_odds': 15.0},
        ],
    }

    engine.update_ratings(race1)

    print("=== After Race 1 (Good) ===")
    for name in ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon']:
        r = engine.get_rating(name, 'good')
        r_all = engine.get_rating(name, 'all')
        print(f"  {name}: Good={r.mu:.1f} (phi={r.phi:.1f}), All={r_all.mu:.1f} (phi={r_all.phi:.1f})")

    race2 = {
        'race_id': 'test_002',
        'surface': 'Soft 5',
        'distance_m': 1600,
        'class': 'BM72',
        'date': '2026-01-22',
        'runners': [
            {'horse_name': 'Alpha', 'position': 3, 'margin': 4.0, 'sp_odds': 5.0},
            {'horse_name': 'Beta', 'position': 1, 'margin': 0.0, 'sp_odds': 3.0},
            {'horse_name': 'Gamma', 'position': 2, 'margin': 1.0, 'sp_odds': 4.5},
        ],
    }

    engine.update_ratings(race2)

    print("\n=== After Race 2 (Soft) ===")
    for name in ['Alpha', 'Beta', 'Gamma']:
        print(f"  {name}: Surface advantage = {engine.get_surface_advantage(name)}")

    print("\n=== Win Probabilities ===")
    probs = engine.predict_win_proba(
        [{'horse_name': 'Alpha'}, {'horse_name': 'Beta'}, {'horse_name': 'Gamma'}],
        surface='all'
    )
    for name, p in sorted(probs.items(), key=lambda x: x[1], reverse=True):
        print(f"  {name}: {p:.1%}")

    print("\n=== Full Report (Alpha) ===")
    report = engine.get_report('Alpha')
    print(json.dumps(report, indent=2))

    engine.save()
    print(f"\nRatings saved to {engine._default_path}")

    engine2 = Glicko2Engine()
    engine2.load()
    print(f"Loaded {len(engine2.ratings)} horses from file")
