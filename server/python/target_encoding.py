"""
Target Encoding with Leave-One-Out (LOO) Smoothing for Horse Racing ML System.

Replaces categorical features with their smoothed mean target value.
LOO smoothing prevents target leakage during training.
"""
import json
import os
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any


class TargetEncoder:
    def __init__(self, columns: list = None, smoothing: float = 10.0, min_samples: int = 5):
        """
        Args:
            columns: list of column names to target-encode
            smoothing: Bayesian smoothing factor (higher = more shrinkage toward global mean)
        """
        self.columns = columns or ['jockey', 'trainer', 'track', 'going', 'race_class']
        self.smoothing = smoothing
        self.min_samples = min_samples
        self.is_fitted = False
        self.encodings = {}
        self.global_mean = 0.0
        self._category_stats = {}

    def fit(self, df: pd.DataFrame, target: str = 'won'):
        """
        Compute smoothed target encoding for each category.
        Smoothed value = (n * category_mean + smoothing * global_mean) / (n + smoothing)
        """
        if target not in df.columns:
            raise ValueError(f"Target column '{target}' not found in DataFrame")

        y = df[target].astype(float)
        self.global_mean = float(y.mean())
        self._target_col = target
        self.encodings = {}
        self._category_stats = {}

        for col in self.columns:
            if col not in df.columns:
                continue

            col_encodings = {}
            col_stats = {}
            groups = df.groupby(col)[target]

            for category, group_values in groups:
                cat_key = str(category)
                n_i = len(group_values)
                mean_i = float(group_values.mean())

                col_stats[cat_key] = {'n': n_i, 'mean': mean_i, 'sum': float(group_values.sum())}

                if n_i >= self.min_samples:
                    encoded = (n_i * mean_i + self.smoothing * self.global_mean) / (n_i + self.smoothing)
                else:
                    encoded = self.global_mean

                col_encodings[cat_key] = float(encoded)

            self.encodings[col] = col_encodings
            self._category_stats[col] = col_stats

        self.is_fitted = True

    def transform(self, df: pd.DataFrame, is_training: bool = False, noise_sigma: float = 0.01) -> pd.DataFrame:
        """
        Apply target encoding.
        If is_training=True, use LOO: exclude current row's target from the category mean to prevent data leakage.
        """
        if not self.is_fitted:
            raise RuntimeError("TargetEncoder has not been fitted. Call fit() first.")

        result = df.copy()
        target_col = getattr(self, '_target_col', 'won')

        for col in self.columns:
            if col not in df.columns:
                result[f'{col}_encoded'] = self.global_mean
                continue

            col_encodings = self.encodings.get(col, {})
            col_stats = self._category_stats.get(col, {})
            encoded_values = np.full(len(df), self.global_mean)

            if is_training and target_col in df.columns:
                y = df[target_col].astype(float).values
                categories = df[col].astype(str).values

                for i in range(len(df)):
                    cat_key = categories[i]
                    stats = col_stats.get(cat_key)

                    if stats is None or stats['n'] <= 1:
                        encoded_values[i] = self.global_mean
                    else:
                        n_i = stats['n']
                        sum_i = stats['sum']
                        y_i = y[i]
                        loo_sum = sum_i - y_i
                        loo_n = n_i - 1

                        if loo_n >= self.min_samples:
                            loo_mean = loo_sum / loo_n
                            encoded_values[i] = (loo_n * loo_mean + self.smoothing * self.global_mean) / (loo_n + self.smoothing)
                        elif loo_n > 0:
                            encoded_values[i] = self.global_mean
                        else:
                            encoded_values[i] = self.global_mean

                if noise_sigma > 0:
                    noise = np.random.normal(0, noise_sigma, size=len(df))
                    encoded_values = encoded_values + noise
            else:
                categories = df[col].astype(str).values
                for i in range(len(df)):
                    cat_key = categories[i]
                    encoded_values[i] = col_encodings.get(cat_key, self.global_mean)

            result[f'{col}_encoded'] = encoded_values

        return result

    def fit_transform(self, df: pd.DataFrame, target: str = 'won') -> pd.DataFrame:
        """Fit and transform in one step (with LOO for training)."""
        self.fit(df, target=target)
        return self.transform(df, is_training=True)

    def get_encoding_report(self) -> dict:
        """Return encoding stats: n_categories, top categories by encoded value, etc."""
        if not self.is_fitted:
            return {'error': 'Not fitted'}

        report = {
            'global_mean': self.global_mean,
            'smoothing': self.smoothing,
            'min_samples': self.min_samples,
            'columns': {},
        }

        for col in self.columns:
            col_enc = self.encodings.get(col, {})
            col_stats = self._category_stats.get(col, {})

            if not col_enc:
                continue

            sorted_cats = sorted(col_enc.items(), key=lambda x: x[1], reverse=True)
            top_5 = sorted_cats[:5]
            bottom_5 = sorted_cats[-5:] if len(sorted_cats) > 5 else []

            report['columns'][col] = {
                'n_categories': len(col_enc),
                'top_categories': {k: round(v, 4) for k, v in top_5},
                'bottom_categories': {k: round(v, 4) for k, v in bottom_5},
                'mean_encoded_value': round(float(np.mean(list(col_enc.values()))), 4),
                'std_encoded_value': round(float(np.std(list(col_enc.values()))), 4) if len(col_enc) > 1 else 0.0,
            }

        return report

    def save(self, filepath: str):
        """Save encodings to JSON file."""
        data = {
            'columns': self.columns,
            'smoothing': self.smoothing,
            'min_samples': self.min_samples,
            'global_mean': self.global_mean,
            'encodings': self.encodings,
            'category_stats': self._category_stats,
            'is_fitted': self.is_fitted,
        }
        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def load(self, filepath: str):
        """Load encodings from JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)

        self.columns = data['columns']
        self.smoothing = data['smoothing']
        self.min_samples = data['min_samples']
        self.global_mean = data['global_mean']
        self.encodings = data['encodings']
        self._category_stats = data.get('category_stats', {})
        self.is_fitted = data.get('is_fitted', True)
        self._target_col = 'won'
