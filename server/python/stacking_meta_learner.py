"""Stacking meta-learner for the horse racing ML ensemble, learning optimal combination of base model predictions using out-of-fold training."""
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

try:
    from sklearn.linear_model import LogisticRegression, RidgeClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score
    import xgboost as xgb
    import lightgbm as lgb
    from catboost import CatBoostClassifier
    STACKING_DEPS_AVAILABLE = True
except ImportError:
    STACKING_DEPS_AVAILABLE = False


class StackingMetaLearner:
    """Stacking ensemble that trains a meta-learner (logistic regression) on out-of-fold predictions from XGBoost, LightGBM, and CatBoost."""

    def __init__(self, n_folds: int = 5, meta_model_type: str = 'logistic'):
        self.n_folds = n_folds
        self.meta_model_type = meta_model_type
        self.is_fitted = False
        self.meta_model = None
        self.meta_scaler = None
        self._report: Dict[str, Any] = {}

    def fit(self, X, y, base_models: dict, scaler):
        """
        Train the meta-learner using out-of-fold predictions from base models.

        X: scaled feature matrix (numpy array)
        Models: dict of {name: fitted_model}
        """
        if not STACKING_DEPS_AVAILABLE:
            return

        try:
            X_arr = np.array(X)
            y_arr = np.array(y).astype(int)

            if len(X_arr) < self.n_folds * 10:
                print(f"[Stacking] Not enough samples ({len(X_arr)}) for {self.n_folds}-fold stacking")
                return

            xgb_params = base_models['xgb'].get_params()
            xgb_params.pop('use_label_encoder', None)

            lgb_params = base_models['lgb'].get_params()

            cat_model_ref = base_models['cat']
            cat_params = {}
            for p in ['iterations', 'depth', 'learning_rate', 'l2_leaf_reg']:
                try:
                    val = cat_model_ref.get_param(p)
                    if val is not None:
                        cat_params[p] = val
                except Exception:
                    pass
            if not cat_params:
                cat_params = {'iterations': 100, 'depth': 6, 'learning_rate': 0.1}

            n_samples = len(X_arr)
            oof_xgb = np.zeros(n_samples)
            oof_lgb = np.zeros(n_samples)
            oof_cat = np.zeros(n_samples)

            skf = StratifiedKFold(n_splits=self.n_folds, shuffle=True, random_state=42)

            fold_metrics = []

            for fold_idx, (train_idx, val_idx) in enumerate(skf.split(X_arr, y_arr)):
                X_fold_train, X_fold_val = X_arr[train_idx], X_arr[val_idx]
                y_fold_train, y_fold_val = y_arr[train_idx], y_arr[val_idx]

                if y_fold_train.sum() < 2 or (len(y_fold_train) - y_fold_train.sum()) < 2:
                    continue

                fold_xgb = xgb.XGBClassifier(**xgb_params)
                fold_xgb.fit(X_fold_train, y_fold_train)
                xgb_val_pred = fold_xgb.predict_proba(X_fold_val)[:, 1]

                fold_lgb = lgb.LGBMClassifier(**lgb_params)
                fold_lgb.fit(X_fold_train, y_fold_train)
                lgb_val_pred = fold_lgb.predict_proba(X_fold_val)[:, 1]

                fold_cat = CatBoostClassifier(**cat_params, random_seed=42, verbose=0)
                fold_cat.fit(X_fold_train, y_fold_train)
                cat_val_pred = fold_cat.predict_proba(X_fold_val)[:, 1]

                oof_xgb[val_idx] = np.clip(xgb_val_pred, 0.01, 0.99)
                oof_lgb[val_idx] = np.clip(lgb_val_pred, 0.01, 0.99)
                oof_cat[val_idx] = np.clip(cat_val_pred, 0.01, 0.99)

                fold_auc = {}
                try:
                    fold_auc['xgb'] = roc_auc_score(y_fold_val, xgb_val_pred)
                    fold_auc['lgb'] = roc_auc_score(y_fold_val, lgb_val_pred)
                    fold_auc['cat'] = roc_auc_score(y_fold_val, cat_val_pred)
                    avg_pred = (xgb_val_pred + lgb_val_pred + cat_val_pred) / 3.0
                    fold_auc['avg'] = roc_auc_score(y_fold_val, avg_pred)
                except Exception:
                    pass

                fold_metrics.append({'fold': fold_idx, **fold_auc})

            used_mask = (oof_xgb != 0) | (oof_lgb != 0) | (oof_cat != 0)
            if used_mask.sum() < 20:
                print("[Stacking] Not enough valid OOF predictions")
                return

            oof_xgb_used = oof_xgb[used_mask]
            oof_lgb_used = oof_lgb[used_mask]
            oof_cat_used = oof_cat[used_mask]
            y_used = y_arr[used_mask]

            meta_features = self._build_meta_features(oof_xgb_used, oof_lgb_used, oof_cat_used)

            self.meta_scaler = StandardScaler()
            meta_features_scaled = self.meta_scaler.fit_transform(meta_features)

            if self.meta_model_type == 'ridge':
                self.meta_model = RidgeClassifier(alpha=1.0)
            else:
                self.meta_model = LogisticRegression(
                    C=1.0, solver='lbfgs', max_iter=1000, random_state=42
                )

            self.meta_model.fit(meta_features_scaled, y_used)

            try:
                if hasattr(self.meta_model, 'predict_proba'):
                    stacked_pred = self.meta_model.predict_proba(meta_features_scaled)[:, 1]
                else:
                    stacked_pred = self.meta_model.decision_function(meta_features_scaled)
                    stacked_pred = 1.0 / (1.0 + np.exp(-stacked_pred))

                stacked_auc = roc_auc_score(y_used, stacked_pred)
                avg_pred = (oof_xgb_used + oof_lgb_used + oof_cat_used) / 3.0
                avg_auc = roc_auc_score(y_used, avg_pred)
                auc_improvement = stacked_auc - avg_auc
            except Exception:
                stacked_auc = 0.0
                avg_auc = 0.0
                auc_improvement = 0.0

            self.is_fitted = True

            self._report = {
                'fold_metrics': fold_metrics,
                'stacked_auc': round(stacked_auc, 4),
                'avg_ensemble_auc': round(avg_auc, 4),
                'auc_improvement': round(auc_improvement, 4),
                'n_folds': self.n_folds,
                'n_samples_used': int(used_mask.sum()),
                'meta_model_type': self.meta_model_type,
            }

            print(f"[Stacking] Fitted — Stacked AUC: {stacked_auc:.4f}, "
                  f"Avg AUC: {avg_auc:.4f}, Improvement: {auc_improvement:+.4f}")

        except Exception as e:
            print(f"[Stacking] Fit failed: {e}")
            self.is_fitted = False

    def predict(self, xgb_pred: np.ndarray, lgb_pred: np.ndarray, cat_pred: np.ndarray) -> np.ndarray:
        """
        Predict using meta-learner. Takes base model predictions as input.
        Returns calibrated ensemble probabilities.
        """
        xgb_pred = np.clip(np.array(xgb_pred), 0.01, 0.99)
        lgb_pred = np.clip(np.array(lgb_pred), 0.01, 0.99)
        cat_pred = np.clip(np.array(cat_pred), 0.01, 0.99)

        if not self.is_fitted or self.meta_model is None or self.meta_scaler is None:
            return (xgb_pred + lgb_pred + cat_pred) / 3.0

        try:
            meta_features = self._build_meta_features(xgb_pred, lgb_pred, cat_pred)
            meta_features_scaled = self.meta_scaler.transform(meta_features)

            if hasattr(self.meta_model, 'predict_proba'):
                preds = self.meta_model.predict_proba(meta_features_scaled)[:, 1]
            else:
                decision = self.meta_model.decision_function(meta_features_scaled)
                preds = 1.0 / (1.0 + np.exp(-decision))

            return np.clip(preds, 0.01, 0.99)
        except Exception:
            return (xgb_pred + lgb_pred + cat_pred) / 3.0

    def get_meta_weights(self) -> dict:
        """
        Extract the learned weights for each base model.
        Returns {'xgb': w1, 'lgb': w2, 'cat': w3, 'interaction': w4}.
        """
        if not self.is_fitted or self.meta_model is None:
            return {'xgb': 1/3, 'lgb': 1/3, 'cat': 1/3, 'interaction_features': {}}

        try:
            coefs = self.meta_model.coef_[0]
            feature_names = ['xgb', 'lgb', 'cat', 'max_pred', 'min_pred', 'pred_std', 'mean_pred']

            base_coefs = coefs[:3]
            abs_coefs = np.abs(base_coefs)
            total = abs_coefs.sum()
            if total > 0:
                weights = abs_coefs / total
            else:
                weights = np.array([1/3, 1/3, 1/3])

            interaction_features = {}
            for i, name in enumerate(feature_names[3:], start=3):
                if i < len(coefs):
                    interaction_features[name] = round(float(coefs[i]), 4)

            return {
                'xgb': round(float(weights[0]), 4),
                'lgb': round(float(weights[1]), 4),
                'cat': round(float(weights[2]), 4),
                'interaction_features': interaction_features,
            }
        except Exception:
            return {'xgb': 1/3, 'lgb': 1/3, 'cat': 1/3, 'interaction_features': {}}

    def get_report(self) -> dict:
        """Return training metrics: meta-model coefficients, fold AUCs, etc."""
        report = dict(self._report)

        if self.is_fitted and self.meta_model is not None:
            try:
                report['meta_coefficients'] = self.meta_model.coef_[0].tolist()
                report['meta_intercept'] = float(self.meta_model.intercept_[0])
            except Exception:
                pass

        return report

    def _build_meta_features(self, xgb_pred: np.ndarray, lgb_pred: np.ndarray,
                             cat_pred: np.ndarray) -> np.ndarray:
        """Build the 7-feature meta-feature matrix from base predictions."""
        preds = np.column_stack([xgb_pred, lgb_pred, cat_pred])
        max_pred = preds.max(axis=1)
        min_pred = preds.min(axis=1)
        pred_std = preds.std(axis=1)
        mean_pred = preds.mean(axis=1)

        return np.column_stack([xgb_pred, lgb_pred, cat_pred,
                                max_pred, min_pred, pred_std, mean_pred])


def integrate_stacking_into_model(model):
    """
    Called after model.train() to fit the stacking meta-learner.
    Uses the model's own base models and training data.
    """
    if not STACKING_DEPS_AVAILABLE:
        print("[Stacking] Dependencies not available")
        return None

    if not model.is_trained:
        print("[Stacking] Model not trained yet")
        return None

    if model.xgb_model is None or model.lgb_model is None or model.catboost_model is None:
        print("[Stacking] Base models not available")
        return None

    learner = StackingMetaLearner(n_folds=5)
    return learner
