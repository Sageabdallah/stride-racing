"""Advanced ML Model for Horse Racing Predictions — XGBoost/LightGBM/CatBoost ensemble."""
import os
import json
import pickle
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

try:
    import xgboost as xgb
    import lightgbm as lgb
    from catboost import CatBoostClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, precision_score, recall_score, f1_score
    from sklearn.calibration import CalibratedClassifierCV
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    ML_AVAILABLE = True
except ImportError as e:
    ML_AVAILABLE = False
    print(f"ML libraries not available: {e}")

try:
    from double_calibration import DoubleCalibrator
    DOUBLE_CALIBRATION_AVAILABLE = True
except ImportError:
    DOUBLE_CALIBRATION_AVAILABLE = False

try:
    from feature_drift_monitor import FeatureDriftMonitor
    DRIFT_MONITOR_AVAILABLE = True
except ImportError:
    DRIFT_MONITOR_AVAILABLE = False

try:
    from stacking_meta_learner import StackingMetaLearner
    STACKING_AVAILABLE = True
except ImportError:
    STACKING_AVAILABLE = False

try:
    from target_encoding import TargetEncoder
    TARGET_ENCODING_AVAILABLE = True
except ImportError:
    TARGET_ENCODING_AVAILABLE = False

try:
    from focal_loss import FocalLossConfig
    FOCAL_LOSS_AVAILABLE = True
except ImportError:
    FOCAL_LOSS_AVAILABLE = False

# NaN-preservation contract (single definition shared with retrain_v2.py).
# Pure-stdlib module, always importable.
from nan_contract import NAN_PRESERVE_SET


def _serve_nan_contract_enabled() -> bool:
    """STRIDE_SERVE_NAN_CONTRACT (default OFF) — when on, prepare_features
    passes NaN through for the contract columns instead of zero-filling them,
    matching what the trees were trained on. Off = exact legacy behaviour."""
    return os.environ.get("STRIDE_SERVE_NAN_CONTRACT", "false").strip().lower() in ("true", "1", "yes")


class RacingMLModel:
    """Ensemble ML model for horse racing predictions."""
    
    _model_performance = {
        'sprint': {'xgb': {'correct': 12, 'total': 100}, 'lgb': {'correct': 15, 'total': 100}, 'cat': {'correct': 10, 'total': 100}},
        'mile': {'xgb': {'correct': 22, 'total': 100}, 'lgb': {'correct': 20, 'total': 100}, 'cat': {'correct': 24, 'total': 100}},
        'staying': {'xgb': {'correct': 18, 'total': 100}, 'lgb': {'correct': 21, 'total': 100}, 'cat': {'correct': 19, 'total': 100}},
    }

    FEATURE_COLUMNS = [
        'distance_strike_rate',
        'course_strike_rate',
        'weighted_form_score',
        'is_first_up',
        'is_second_up',
        'days_since_run',
        'jockey_trainer_strike_rate',
        'is_winning_combo',
        'barrier_draw',
        'weight_kg',
        'ground_suitability',
        'market_odds',
        'class_movement',
        'is_class_drop',
        'is_class_rise',
        'is_improving',
        'improvement_score',
        'is_in_form_cycle',
        'has_dominant_win',
        # Phase 2: Biomechanical & statistical sectional features (Ward-Smith / Beyer / Thorograph)
        'z_200m',            # Z-score of last 200m speed vs race field (N≈0,1)
        'z_400m',            # Z-score of 400m section vs race field
        'z_600m',            # Z-score of 600m section vs race field
        'z_800m',            # Z-score of 800m section vs race field
        'lambda_decay',      # Velocity decay constant λ: low=stayer, high=sprinter/fader
        'svi',               # Sustained Velocity Index: >1.05=closer, <0.95=fader
        'rsi',               # Race Shape Index from horse's last run (pace environment)
        'trip_cost_seconds', # Seconds lost to wide barrier draw (Thorograph formula)
        'distance',
        'field_size',
        'class_level',
        # Phase 3: Trajectory features
        'form_direction_slope',
        'speed_rating_trajectory',
        'sectional_trajectory',
        'campaign_run_number',
        'weight_change',
        'jockey_booking_change',
        'fresh_x_trajectory',
        'first_up_win_rate',
        'second_up_win_rate',
        'consistency_score',
        'going_suitability',
        'fitness_x_distance',
        'sectional_x_going',
        'class_drop_x_trajectory',
        'campaign_run_x_fitness',
        'pace_pressure_score',
        'leader_advantage',
        'closer_advantage',
        'barrier_relevance_score',
        'field_size_context',
        'market_efficiency_flag',
        'td_pace_bias',
        'td_upset_rate',
        'td_closing_speed_bias',
        # Phase 4: Distance-change sectional intelligence
        # RED (<20% coverage) excluded: dist_sectional_slope, dist_sectional_recency_weighted,
        #   sectional_result_divergence, first_at_distance_sectional_quality, step_up_x_dist_slope
        'distance_direction_flag',
        'sectional_rank_at_distance',
        'has_sectional_data',
        # Phase 4: Bounce detection
        'is_bounce_candidate',
        'bounce_severity',
        'runs_since_peak',
        # Phase 2: Barrier trial features
        'trial_recency',          # days since most recent trial before this race (999 = no trial)
        'trial_count_60d',        # number of trials in 60 days before this race
        'trial_x_experience',     # best trial position percentile × (1/career_starts); first-starter signal
        'trainer_trial_pattern',  # post-trial win rate ÷ own first-up rate × credibility; 1.0 = neutral
        'trial_quality_score',    # quality_raw × field_multiplier × volume_factor; co-trialist strength
        # Phase 5: within-race relative market position (relative_market.py;
        # names/semantics match mc_api.extract_ml_features for train/serve parity)
        'fair_implied_prob',      # overround-corrected implied win % within the field (0-100)
        'odds_rank',              # 1 = market favourite; ties share the lower rank
        'odds_rank_pct',          # odds_rank / field size
        # Winner-pattern gap features (12P-8 rescue). Declared for schema
        # lockstep with retrain_v2; the live pkl predicts from its own stored
        # feature list, so the current model is unaffected until task 12.
        'prior_pb_close_underreaction',
        'cohort_fast_close_prior',
        'pos400_win_prior',
        'jockey_wet_residual',
    ]
    
    def __init__(self, model_path: str = None):
        self.model_path = model_path or os.path.join(
            os.path.dirname(__file__), 'models', 'racing_ensemble_v2.pkl'
        )
        self.xgb_model = None
        self.lgb_model = None
        self.catboost_model = None
        self.scaler = StandardScaler()
        self.is_trained = False
        self.feature_importance = {}
        self.training_stats = {}
        
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        
        if os.path.exists(self.model_path):
            self.load()
    
    def prepare_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """Prepare feature matrix from raw data."""
        # Use model's trained feature list if available, else fall back to FEATURE_COLUMNS
        cols = getattr(self, '_trained_feature_columns', None) or self.FEATURE_COLUMNS
        features = pd.DataFrame()

        # STRIDE_SERVE_NAN_CONTRACT (default off): columns in the shared
        # NaN-preserve contract (nan_contract.NAN_PRESERVE_SET — sectional/
        # z-score features, trial_recency, runs_since_peak) pass NaN through
        # so tree models route missingness exactly as in training; every other
        # column keeps the legacy zero-fill. Flag off = exact old behaviour.
        preserve = NAN_PRESERVE_SET if _serve_nan_contract_enabled() else frozenset()

        for col in cols:
            if col in data.columns:
                numeric = pd.to_numeric(data[col], errors='coerce')
                features[col] = numeric if col in preserve else numeric.fillna(0)
            else:
                features[col] = np.nan if col in preserve else 0

        # Enforce binary constraint on has_sectional_data
        if "has_sectional_data" in features.columns:
            features["has_sectional_data"] = features["has_sectional_data"].clip(0, 1).round().astype(int)

        return features
    
    def train(self, training_data: pd.DataFrame, target_col: str = 'won',
              use_optuna: bool = True, n_trials: int = 50) -> Dict[str, Any]:
        """Train ensemble model on historical race data."""
        if not ML_AVAILABLE:
            return {'error': 'ML libraries not available'}
        
        if len(training_data) < 50:
            return {'error': f'Insufficient training data: {len(training_data)} records (need 50+)'}
        
        X = self.prepare_features(training_data)
        y = training_data[target_col].astype(int)
        
        if y.sum() < 5:
            return {'error': f'Insufficient positive samples: {y.sum()} winners (need 5+)'}
        
        metrics = {}
        
        if FOCAL_LOSS_AVAILABLE:
            self._focal_config = FocalLossConfig(gamma=2.0, alpha=0.25)
            metrics['focal_loss'] = {'gamma': 2.0, 'alpha': 0.25, 'enabled': False, 'note': 'objective available but not passed to boosters; class weights handle imbalance'}
            print("[ML] Focal loss available (not applied — native class weights in use)")
        
        if TARGET_ENCODING_AVAILABLE:
            try:
                self.target_encoder = TargetEncoder(
                    columns=['jockey', 'trainer', 'track', 'going', 'race_class'],
                    smoothing=10.0, min_samples=5
                )
                cat_cols_available = [c for c in self.target_encoder.columns if c in training_data.columns]
                if cat_cols_available:
                    self.target_encoder.columns = cat_cols_available
                    training_data_encoded = self.target_encoder.fit_transform(training_data, target=target_col)
                    encoded_cols = [f'{c}_encoded' for c in cat_cols_available]
                    for ec in encoded_cols:
                        if ec in training_data_encoded.columns:
                            X[ec] = training_data_encoded[ec].values
                    metrics['target_encoding'] = {
                        'encoded_columns': cat_cols_available,
                        'n_categories': {c: len(self.target_encoder.encodings.get(c, {})) for c in cat_cols_available},
                    }
                    print(f"[ML] Target encoding fitted for {cat_cols_available}")
            except Exception as e:
                print(f"[ML] Target encoding error: {e}")
                self.target_encoder = None
        
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        
        # Use native class weights instead of SMOTE (audit fix #3).
        # SMOTE creates fictional winners that corrupt probability estimates;
        # native class weighting adjusts the loss function without fake data.
        neg_count = (y_train == 0).sum()
        pos_count = max((y_train == 1).sum(), 1)
        spw = neg_count / pos_count

        if use_optuna and n_trials > 0:
            best_params = self._optimize_hyperparameters(
                X_train_scaled, y_train, X_test_scaled, y_test, n_trials
            )
        else:
            best_params = self._get_default_params()

        self.xgb_model = xgb.XGBClassifier(**best_params['xgb'], scale_pos_weight=spw, random_state=42)
        self.xgb_model.fit(X_train_scaled, y_train)

        self.lgb_model = lgb.LGBMClassifier(**best_params['lgb'], is_unbalance=True, random_state=42, verbose=-1)
        self.lgb_model.fit(X_train_scaled, y_train)

        self.catboost_model = CatBoostClassifier(**best_params['catboost'], auto_class_weights="Balanced", random_seed=42, verbose=0)
        self.catboost_model.fit(X_train_scaled, y_train)
        
        y_pred_proba = self.predict_proba(X_test)
        y_pred = (y_pred_proba > 0.5).astype(int)
        
        metrics.update({
            'auc_roc': roc_auc_score(y_test, y_pred_proba),
            'precision': precision_score(y_test, y_pred, zero_division=0),
            'recall': recall_score(y_test, y_pred, zero_division=0),
            'f1': f1_score(y_test, y_pred, zero_division=0),
            'train_samples': len(X_train),
            'test_samples': len(X_test),
            'positive_rate_train': y_train.mean(),
            'positive_rate_test': y_test.mean(),
        })
        
        self._calculate_feature_importance(X)
        
        # Phase 2: Stacking Meta-Learner
        if STACKING_AVAILABLE:
            try:
                self.stacking_learner = StackingMetaLearner(n_folds=5)
                self.stacking_learner.fit(
                    X_train_scaled, y_train,
                    {'xgb': self.xgb_model, 'lgb': self.lgb_model, 'cat': self.catboost_model},
                    self.scaler
                )
                stack_report = self.stacking_learner.get_report()
                metrics['stacking'] = {
                    'fitted': self.stacking_learner.is_fitted,
                    'meta_weights': self.stacking_learner.get_meta_weights(),
                    'fold_auc_improvement': stack_report.get('auc_improvement', 0),
                }
                print(f"[ML] Stacking meta-learner fitted: {self.stacking_learner.is_fitted}")
            except Exception as e:
                print(f"[ML] Stacking error: {e}")
                self.stacking_learner = None
        
        # Phase 1: Double Calibration — train per-model + ensemble calibrators
        if DOUBLE_CALIBRATION_AVAILABLE and ML_AVAILABLE:
            try:
                xgb_proba = self.xgb_model.predict_proba(X_test_scaled)[:, 1]
                lgb_proba = self.lgb_model.predict_proba(X_test_scaled)[:, 1]
                cat_proba = self.catboost_model.predict_proba(X_test_scaled)[:, 1]
                self.double_calibrator = DoubleCalibrator()
                self.double_calibrator.fit(
                    xgb_proba, lgb_proba, cat_proba,
                    y_test.values, self._get_ensemble_weights('mile')
                )
                dc_report = self.double_calibrator.get_calibration_report()
                metrics['double_calibration'] = {
                    'brier_before': dc_report.get('brier_before_ensemble', 0),
                    'brier_after': dc_report.get('brier_after_ensemble', 0),
                    'improvement': dc_report.get('improvement_pct', 0),
                }
                print(f"[ML] Double calibration trained — Brier improvement: {dc_report.get('improvement_pct', 0):.1f}%")
            except Exception as e:
                print(f"[ML] Double calibration training error: {e}")
                self.double_calibrator = None
        
        # Phase 1: Feature Drift Monitor — snapshot feature importance
        if DRIFT_MONITOR_AVAILABLE and self.feature_importance:
            try:
                self._drift_monitor = FeatureDriftMonitor()
                self._drift_monitor.record_snapshot(
                    self.feature_importance,
                    metadata={
                        'trained_at': datetime.now().isoformat(),
                        'n_samples': len(training_data),
                        'n_features': X.shape[1],
                        'auc': metrics.get('auc_roc', 0),
                    }
                )
                drift_result = self._drift_monitor.compute_drift()
                metrics['drift_monitor'] = {
                    'snapshot_recorded': True,
                    'n_snapshots': len(self._drift_monitor.history),
                    'drift_detected': drift_result.get('drift_detected', False),
                    'js_divergence': drift_result.get('js_divergence', 0),
                }
                print(f"[ML] Drift monitor snapshot recorded ({len(self._drift_monitor.history)} total)")
            except Exception as e:
                print(f"[ML] Drift monitor error: {e}")
                self._drift_monitor = None
        
        self.is_trained = True
        # Persist the exact training-time column set (incl. any target-encoded
        # columns) so a reloaded artifact never shape-mismatches a grown
        # FEATURE_COLUMNS contract.
        self._trained_feature_columns = list(X.columns)
        self.training_stats = {
            'trained_at': datetime.now().isoformat(),
            'samples': len(training_data),
            'metrics': metrics,
            'feature_importance': self.feature_importance,
        }
        
        self.save()
        
        return {
            'success': True,
            'metrics': metrics,
            'feature_importance': dict(sorted(
                self.feature_importance.items(), 
                key=lambda x: x[1], reverse=True
            )[:10])
        }
    
    def _optimize_hyperparameters(self, X_train, y_train, X_test, y_test, n_trials: int) -> Dict:
        """Use Optuna to find optimal hyperparameters."""
        
        neg_n = (y_train == 0).sum()
        pos_n = max((y_train == 1).sum(), 1)
        spw = neg_n / pos_n

        def objective_xgb(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 50, 300),
                'max_depth': trial.suggest_int('max_depth', 3, 10),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3),
                'subsample': trial.suggest_float('subsample', 0.6, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
                'min_child_weight': trial.suggest_int('min_child_weight', 1, 10),
            }
            model = xgb.XGBClassifier(**params, scale_pos_weight=spw, random_state=42, verbosity=0)
            model.fit(X_train, y_train)
            pred = model.predict_proba(X_test)[:, 1]
            return roc_auc_score(y_test, pred)

        def objective_lgb(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 50, 300),
                'max_depth': trial.suggest_int('max_depth', 3, 10),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3),
                'subsample': trial.suggest_float('subsample', 0.6, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
                'num_leaves': trial.suggest_int('num_leaves', 10, 100),
            }
            model = lgb.LGBMClassifier(**params, is_unbalance=True, random_state=42, verbose=-1)
            model.fit(X_train, y_train)
            pred = model.predict_proba(X_test)[:, 1]
            return roc_auc_score(y_test, pred)
        
        study_xgb = optuna.create_study(direction='maximize')
        study_xgb.optimize(objective_xgb, n_trials=n_trials // 2, show_progress_bar=False)
        
        study_lgb = optuna.create_study(direction='maximize')
        study_lgb.optimize(objective_lgb, n_trials=n_trials // 2, show_progress_bar=False)
        
        return {
            'xgb': study_xgb.best_params,
            'lgb': study_lgb.best_params,
            'catboost': {
                'iterations': 200,
                'depth': 6,
                'learning_rate': 0.1,
            }
        }
    
    def _get_default_params(self) -> Dict:
        """Default hyperparameters when optimization is skipped."""
        return {
            'xgb': {
                'n_estimators': 100,
                'max_depth': 6,
                'learning_rate': 0.1,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
            },
            'lgb': {
                'n_estimators': 100,
                'max_depth': 6,
                'learning_rate': 0.1,
                'num_leaves': 31,
            },
            'catboost': {
                'iterations': 100,
                'depth': 6,
                'learning_rate': 0.1,
            }
        }
    
    def _calculate_feature_importance(self, X: pd.DataFrame):
        """Calculate aggregate feature importance from all models."""
        importance = {}
        
        if self.xgb_model:
            xgb_imp = dict(zip(X.columns, self.xgb_model.feature_importances_))
            for k, v in xgb_imp.items():
                importance[k] = importance.get(k, 0) + v / 3
        
        if self.lgb_model:
            lgb_imp = dict(zip(X.columns, self.lgb_model.feature_importances_))
            for k, v in lgb_imp.items():
                importance[k] = importance.get(k, 0) + v / 3
        
        if self.catboost_model:
            cat_imp = dict(zip(X.columns, self.catboost_model.feature_importances_))
            for k, v in cat_imp.items():
                importance[k] = importance.get(k, 0) + v / 3
        
        total = sum(importance.values()) or 1
        self.feature_importance = {k: v / total for k, v in importance.items()}
    
    def _get_race_category(self, distance_m: int) -> str:
        """Classify race by distance into sprint/mile/staying."""
        if distance_m < 1200:
            return 'sprint'
        elif distance_m <= 1600:
            return 'mile'
        else:
            return 'staying'

    def _get_ensemble_weights(self, race_category: str) -> dict:
        """Get dynamic ensemble weights based on tracked accuracy."""
        perf = self._model_performance.get(race_category, self._model_performance['mile'])
        
        accuracies = {}
        for model_key in ['xgb', 'lgb', 'cat']:
            stats = perf[model_key]
            accuracies[model_key] = stats['correct'] / max(stats['total'], 1)
        
        total_acc = sum(accuracies.values())
        if total_acc <= 0:
            return {'xgb': 1/3, 'lgb': 1/3, 'cat': 1/3}
        
        weights = {k: v / total_acc for k, v in accuracies.items()}
        return weights

    def update_model_performance(self, race_category: str, model_predictions: dict, actual_winner_idx: int):
        """
        Update per-model accuracy tracking after a race result.

        model_predictions: {'xgb': predicted_winner_idx, 'lgb': predicted_winner_idx, 'cat': predicted_winner_idx}
        """
        if race_category not in self._model_performance:
            return
        
        for model_key in ['xgb', 'lgb', 'cat']:
            pred = model_predictions.get(model_key)
            if pred is not None:
                self._model_performance[race_category][model_key]['total'] += 1
                if pred == actual_winner_idx:
                    self._model_performance[race_category][model_key]['correct'] += 1

    def predict_components(self, X: pd.DataFrame, distance_m: int = None) -> Dict:
        """Return base and ensemble probabilities without changing scoring."""
        if not self.is_trained:
            zeros = np.zeros(len(X))
            return {"xgb": zeros, "lightgbm": zeros, "catboost": zeros,
                    "ensemble": zeros, "method": "untrained", "weights": None}
        
        if isinstance(X, pd.DataFrame):
            # Tree models don't need scaling; only apply if scaler was fitted
            try:
                if hasattr(self.scaler, 'mean_') and self.scaler.mean_ is not None:
                    X_scaled = self.scaler.transform(X)
                else:
                    X_scaled = X.values
            except Exception:
                X_scaled = X.values
        else:
            X_scaled = X
        
        xgb_pred = self.xgb_model.predict_proba(X_scaled)[:, 1] if self.xgb_model else np.zeros(len(X_scaled))
        lgb_pred = self.lgb_model.predict_proba(X_scaled)[:, 1] if self.lgb_model else np.zeros(len(X_scaled))
        cat_pred = self.catboost_model.predict_proba(X_scaled)[:, 1] if self.catboost_model else np.zeros(len(X_scaled))
        
        # Try stacking meta-learner first. NB: retrain_v2 artifacts carry per-model _isotonic calibrators that are deliberately NOT applied here — pipeline-level isotonic (ProbabilityCalibrator in run_tips_pipeline.calibrate_and_score) calibrates the final output, and applying both layers would double-calibrate.
        if hasattr(self, 'stacking_learner') and self.stacking_learner is not None and self.stacking_learner.is_fitted:
            try:
                ensemble = self.stacking_learner.predict(xgb_pred, lgb_pred, cat_pred)
                if hasattr(self, 'double_calibrator') and self.double_calibrator is not None:
                    try:
                        race_cat = self._get_race_category(distance_m) if distance_m else 'mile'
                        weights = self._get_ensemble_weights(race_cat)
                        ensemble = self.double_calibrator.calibrate(
                            xgb_pred, lgb_pred, cat_pred, weights
                        )
                    except Exception:
                        pass
                return {"xgb": xgb_pred, "lightgbm": lgb_pred,
                        "catboost": cat_pred, "ensemble": ensemble,
                        "method": "stacking", "weights": None}
            except Exception:
                pass
        
        race_cat = self._get_race_category(distance_m) if distance_m else 'mile'
        weights = self._get_ensemble_weights(race_cat)
        
        if hasattr(self, 'double_calibrator') and self.double_calibrator is not None:
            try:
                ensemble = self.double_calibrator.calibrate(
                    xgb_pred, lgb_pred, cat_pred, weights
                )
                return {"xgb": xgb_pred, "lightgbm": lgb_pred,
                        "catboost": cat_pred, "ensemble": ensemble,
                        "method": "double_calibrator", "weights": weights}
            except Exception:
                pass
        
        ensemble = (xgb_pred * weights['xgb'] + lgb_pred * weights['lgb'] + cat_pred * weights['cat'])
        return {"xgb": xgb_pred, "lightgbm": lgb_pred,
                "catboost": cat_pred, "ensemble": ensemble,
                "method": "weighted_average", "weights": weights}

    def predict_proba(self, X: pd.DataFrame, distance_m: int = None) -> np.ndarray:
        """Get ensemble probability predictions with dynamic weighting."""
        return self.predict_components(X, distance_m=distance_m)["ensemble"]
    
    def predict_adjustment(self, features: Dict) -> float:
        """Get ML-based probability adjustment for a single runner. Returns a multiplier (0.5 - 2.0) to adjust base probability."""
        return self.predict_adjustment_with_stages(features)[0]

    def predict_adjustment_with_stages(self, features: Dict):
        """Return the legacy multiplier plus auditable base-model quantities."""
        if not self.is_trained:
            return 1.0, {"method": "untrained"}

        df = pd.DataFrame([features])
        X = self.prepare_features(df)
        components = self.predict_components(X)
        ml_prob = components["ensemble"][0]
        adjustment = 0.5 + (ml_prob * 1.5)
        adjustment = max(0.7, min(1.5, adjustment))
        audit = {
            "xgb": float(components["xgb"][0]),
            "lightgbm": float(components["lightgbm"][0]),
            "catboost": float(components["catboost"][0]),
            "ensemble": float(ml_prob),
            "method": components["method"],
        }
        return adjustment, audit
    
    def explain_prediction(self, features: Dict) -> Dict[str, float]:
        """Use feature importance to explain prediction. Returns top contributing factors."""
        if not self.is_trained or not self.feature_importance:
            return {}
        
        contributions = {}
        for feature, importance in self.feature_importance.items():
            value = features.get(feature, 0)
            if value and importance > 0.01:
                contribution = value * importance
                if contribution > 0:
                    contributions[feature] = round(contribution, 3)
        
        sorted_contrib = dict(sorted(
            contributions.items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:5])
        
        return sorted_contrib
    
    def save(self):
        """Save trained models to disk."""
        model_data = {
            'xgb_model': self.xgb_model,
            'lgb_model': self.lgb_model,
            'catboost_model': self.catboost_model,
            'scaler': self.scaler,
            'is_trained': self.is_trained,
            'feature_importance': self.feature_importance,
            'training_stats': self.training_stats,
            'feature_columns': getattr(self, '_trained_feature_columns', None)
                               or list(self.FEATURE_COLUMNS),
        }
        with open(self.model_path, 'wb') as f:
            pickle.dump(model_data, f)
    
    def load(self) -> bool:
        """Load trained models from disk."""
        try:
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)

            self.xgb_model = model_data.get('xgb_model')
            self.lgb_model = model_data.get('lgb_model')
            # Handle both key names (retrain_v2 uses 'cb_model', ml_model uses 'catboost_model')
            self.catboost_model = model_data.get('catboost_model') or model_data.get('cb_model')
            self.scaler = model_data.get('scaler', StandardScaler())
            self.feature_importance = model_data.get('feature_importance', {})
            self.training_stats = model_data.get('training_stats', model_data.get('cv_results', {}))
            # Mark as trained if at least one model loaded
            self.is_trained = model_data.get('is_trained',
                                             self.xgb_model is not None or self.lgb_model is not None)
            # Use the model's trained feature list if available (may differ from FEATURE_COLUMNS)
            saved_cols = model_data.get('feature_columns')
            if saved_cols and isinstance(saved_cols, list):
                self._trained_feature_columns = saved_cols
            return True
        except Exception as e:
            print(f"Failed to load model: {e}")
            return False
    
    def get_status(self) -> Dict:
        """Get model training status and metrics."""
        return {
            'is_trained': self.is_trained,
            'model_path': self.model_path,
            'training_stats': self.training_stats,
            'top_features': dict(sorted(
                self.feature_importance.items(),
                key=lambda x: x[1], reverse=True
            )[:5]) if self.feature_importance else {}
        }


_model_instance = None

def get_model() -> RacingMLModel:
    """Get singleton model instance."""
    global _model_instance
    if _model_instance is None:
        _model_instance = RacingMLModel()
    return _model_instance


def train_from_database(db_url: str = None) -> Dict:
    """Train model from training_data table."""
    import os
    db_url = db_url or os.environ.get('DATABASE_URL')
    
    if not db_url:
        return {'error': 'DATABASE_URL not set'}
    
    try:
        from sqlalchemy import create_engine
        engine = create_engine(db_url)
        
        query = """
        SELECT 
            distance_strike_rate, course_strike_rate, weighted_form_score,
            is_first_up::int, is_second_up::int, days_since_run,
            jockey_trainer_strike_rate, is_winning_combo::int,
            barrier, weight, ml_adjustment,
            COALESCE(won, 0) as won,
            COALESCE(placed, 0) as placed
        FROM training_data
        WHERE won IS NOT NULL
        """
        
        df = pd.read_sql(query, engine)
        
        if len(df) < 50:
            return {'error': f'Insufficient training data: {len(df)} records'}
        
        model = get_model()
        result = model.train(df, target_col='won', use_optuna=True, n_trials=30)
        
        return result
        
    except Exception as e:
        return {'error': str(e)}


if __name__ == '__main__':
    print("Testing ML Model...")
    model = RacingMLModel()
    print(f"Model status: {model.get_status()}")
    
    test_features = {
        'distance_strike_rate': 25.0,
        'course_strike_rate': 20.0,
        'weighted_form_score': 15.0,
        'is_first_up': 0,
        'is_second_up': 1,
        'freshness_score': 0.8,
        'jockey_trainer_strike_rate': 18.0,
        'is_winning_combo': 1,
        'barrier_draw': 3,
        'barrier_penalty': 0,
        'weight_kg': 56,
        'horse_age': 4,
    }
    
    adjustment = model.predict_adjustment(test_features)
    print(f"Test adjustment (untrained): {adjustment}")
