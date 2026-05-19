"""Model Versioning & A/B Shadow Testing System — tracks model versions, compares performance, and runs shadow tests for horse racing ML predictions."""
import os
import json
import pickle
import hashlib
import shutil
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

try:
    from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


@dataclass
class ModelVersion:
    version_id: str
    created_at: str
    description: str
    config: dict
    metrics: dict
    feature_columns: list
    training_data_hash: str
    is_active: bool
    model_path: str


class ModelRegistry:
    def __init__(self, registry_dir: str = None):
        self.registry_dir = registry_dir or os.path.join(
            os.path.dirname(__file__), 'models', 'registry'
        )
        self.versions: Dict[str, ModelVersion] = {}
        self.active_version: Optional[str] = None
        os.makedirs(self.registry_dir, exist_ok=True)
        self.load_registry()

    def _next_version_id(self) -> str:
        if not self.versions:
            return 'v1.0'
        existing = []
        for vid in self.versions:
            try:
                parts = vid.lstrip('v').split('.')
                major = int(parts[0])
                minor = int(parts[1]) if len(parts) > 1 else 0
                existing.append((major, minor))
            except (ValueError, IndexError):
                continue
        if not existing:
            return 'v1.0'
        existing.sort()
        last_major, last_minor = existing[-1]
        return f'v{last_major}.{last_minor + 1}'

    @staticmethod
    def compute_data_hash(feature_columns: list, data_shape: tuple = None) -> str:
        content = json.dumps(sorted(feature_columns))
        if data_shape:
            content += f'|{data_shape}'
        return hashlib.md5(content.encode()).hexdigest()

    def register_version(self, model, version_id: str = None, description: str = '',
                         metrics: dict = None, config: dict = None,
                         feature_columns: list = None) -> ModelVersion:
        if version_id is None:
            version_id = self._next_version_id()

        if version_id in self.versions:
            raise ValueError(f"Version '{version_id}' already exists")

        version_dir = os.path.join(self.registry_dir, version_id)
        os.makedirs(version_dir, exist_ok=True)
        model_path = os.path.join(version_dir, 'model.pkl')

        with open(model_path, 'wb') as f:
            pickle.dump(model, f)

        feat_cols = feature_columns or []
        if hasattr(model, 'FEATURE_COLUMNS'):
            feat_cols = feat_cols or list(model.FEATURE_COLUMNS)

        model_config = config or {}
        if not model_config and hasattr(model, 'training_stats'):
            model_config = model.training_stats.get('metrics', {})

        model_metrics = metrics or {}

        data_hash = self.compute_data_hash(feat_cols)

        version = ModelVersion(
            version_id=version_id,
            created_at=datetime.now().isoformat(),
            description=description,
            config=model_config,
            metrics=model_metrics,
            feature_columns=feat_cols,
            training_data_hash=data_hash,
            is_active=False,
            model_path=model_path,
        )

        self.versions[version_id] = version
        self.save_registry()
        print(f"[Registry] Registered model version {version_id}")
        return version

    def activate_version(self, version_id: str):
        if version_id not in self.versions:
            raise ValueError(f"Version '{version_id}' not found")

        for vid, ver in self.versions.items():
            ver.is_active = False

        self.versions[version_id].is_active = True
        self.active_version = version_id
        self.save_registry()
        print(f"[Registry] Activated version {version_id}")

    def get_active_model(self):
        if not self.active_version:
            return None
        version = self.versions.get(self.active_version)
        if not version:
            return None
        if not os.path.exists(version.model_path):
            print(f"[Registry] Model file missing for {self.active_version}")
            return None
        with open(version.model_path, 'rb') as f:
            return pickle.load(f)

    def get_version(self, version_id: str) -> ModelVersion:
        if version_id not in self.versions:
            raise ValueError(f"Version '{version_id}' not found")
        return self.versions[version_id]

    def load_model(self, version_id: str):
        version = self.get_version(version_id)
        if not os.path.exists(version.model_path):
            raise FileNotFoundError(f"Model file missing: {version.model_path}")
        with open(version.model_path, 'rb') as f:
            return pickle.load(f)

    def list_versions(self) -> list:
        result = []
        for vid, ver in sorted(self.versions.items()):
            entry = {
                'version_id': ver.version_id,
                'created_at': ver.created_at,
                'description': ver.description,
                'is_active': ver.is_active,
                'metrics': ver.metrics,
                'n_features': len(ver.feature_columns),
            }
            result.append(entry)
        return result

    def compare_versions(self, version_a: str, version_b: str) -> dict:
        va = self.get_version(version_a)
        vb = self.get_version(version_b)

        metric_diff = {}
        all_metric_keys = set(list(va.metrics.keys()) + list(vb.metrics.keys()))
        for key in all_metric_keys:
            val_a = va.metrics.get(key)
            val_b = vb.metrics.get(key)
            if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                metric_diff[key] = {
                    'version_a': val_a,
                    'version_b': val_b,
                    'diff': round(val_b - val_a, 6),
                    'pct_change': round((val_b - val_a) / val_a * 100, 2) if val_a != 0 else None,
                }
            else:
                metric_diff[key] = {'version_a': val_a, 'version_b': val_b}

        features_a = set(va.feature_columns)
        features_b = set(vb.feature_columns)
        feature_diff = {
            'added': sorted(features_b - features_a),
            'removed': sorted(features_a - features_b),
            'common': len(features_a & features_b),
            'total_a': len(features_a),
            'total_b': len(features_b),
        }

        config_diff = {}
        all_config_keys = set(list(va.config.keys()) + list(vb.config.keys()))
        for key in all_config_keys:
            ca = va.config.get(key)
            cb = vb.config.get(key)
            if ca != cb:
                config_diff[key] = {'version_a': ca, 'version_b': cb}

        return {
            'version_a': version_a,
            'version_b': version_b,
            'metric_differences': metric_diff,
            'feature_differences': feature_diff,
            'config_changes': config_diff,
        }

    def delete_version(self, version_id: str):
        if version_id not in self.versions:
            raise ValueError(f"Version '{version_id}' not found")
        if self.active_version == version_id:
            raise ValueError("Cannot delete the active production version")

        version = self.versions[version_id]
        version_dir = os.path.dirname(version.model_path)
        if os.path.isdir(version_dir):
            shutil.rmtree(version_dir)

        del self.versions[version_id]
        self.save_registry()
        print(f"[Registry] Deleted version {version_id}")

    def save_registry(self):
        registry_path = os.path.join(self.registry_dir, 'registry.json')
        data = {
            'active_version': self.active_version,
            'versions': {vid: asdict(ver) for vid, ver in self.versions.items()},
        }
        with open(registry_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def load_registry(self):
        registry_path = os.path.join(self.registry_dir, 'registry.json')
        if not os.path.exists(registry_path):
            return
        try:
            with open(registry_path, 'r') as f:
                data = json.load(f)
            self.active_version = data.get('active_version')
            self.versions = {}
            for vid, vdata in data.get('versions', {}).items():
                self.versions[vid] = ModelVersion(**vdata)
            print(f"[Registry] Loaded {len(self.versions)} versions from disk")
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            print(f"[Registry] Failed to load registry: {e}")


class ShadowTester:
    def __init__(self, primary_model, shadow_model, name: str = 'shadow_test'):
        self.primary = primary_model
        self.shadow = shadow_model
        self.name = name
        self.comparison_log: List[dict] = []
        self.results: Dict[str, dict] = {}
        self.started_at = datetime.now().isoformat()

    def predict(self, features, **kwargs):
        primary_pred = self._safe_predict(self.primary, features, **kwargs)

        try:
            shadow_pred = self._safe_predict(self.shadow, features, **kwargs)
        except Exception as e:
            shadow_pred = None
            print(f"[Shadow] Shadow model prediction failed: {e}")

        race_id = kwargs.get('race_id', f"race_{len(self.comparison_log) + 1}")

        entry = {
            'race_id': race_id,
            'timestamp': datetime.now().isoformat(),
            'primary_predictions': self._to_serializable(primary_pred),
            'shadow_predictions': self._to_serializable(shadow_pred),
            'n_runners': len(features) if hasattr(features, '__len__') else 0,
        }

        if primary_pred is not None and shadow_pred is not None:
            primary_arr = np.array(primary_pred) if not isinstance(primary_pred, np.ndarray) else primary_pred
            shadow_arr = np.array(shadow_pred) if not isinstance(shadow_pred, np.ndarray) else shadow_pred
            if len(primary_arr) > 0 and len(shadow_arr) > 0 and len(primary_arr) == len(shadow_arr):
                entry['primary_top_pick'] = int(np.argmax(primary_arr))
                entry['shadow_top_pick'] = int(np.argmax(shadow_arr))
                entry['agrees_on_top'] = entry['primary_top_pick'] == entry['shadow_top_pick']
                entry['correlation'] = float(np.corrcoef(primary_arr, shadow_arr)[0, 1]) if len(primary_arr) > 1 else 1.0

        self.comparison_log.append(entry)
        return primary_pred

    def _safe_predict(self, model, features, **kwargs):
        if hasattr(model, 'predict_proba') and callable(model.predict_proba):
            if isinstance(features, pd.DataFrame):
                return model.predict_proba(features, **{k: v for k, v in kwargs.items() if k != 'race_id'})
            return model.predict_proba(features)
        if hasattr(model, 'predict') and callable(model.predict):
            return model.predict(features)
        raise ValueError("Model has no predict or predict_proba method")

    @staticmethod
    def _to_serializable(obj):
        if obj is None:
            return None
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (list, tuple)):
            return [float(x) if isinstance(x, (np.floating, float)) else x for x in obj]
        return obj

    def record_result(self, race_id: str, actual_winner: str):
        matching = [e for e in self.comparison_log if e['race_id'] == race_id]
        if not matching:
            print(f"[Shadow] No predictions found for race {race_id}")
            return

        entry = matching[-1]
        try:
            winner_idx = int(actual_winner)
        except (ValueError, TypeError):
            winner_idx = None

        result = {
            'race_id': race_id,
            'actual_winner': actual_winner,
            'winner_idx': winner_idx,
            'recorded_at': datetime.now().isoformat(),
        }

        primary_preds = entry.get('primary_predictions')
        shadow_preds = entry.get('shadow_predictions')

        if winner_idx is not None and primary_preds and shadow_preds:
            if winner_idx < len(primary_preds):
                result['primary_winner_prob'] = primary_preds[winner_idx]
            if winner_idx < len(shadow_preds):
                result['shadow_winner_prob'] = shadow_preds[winner_idx]

            result['primary_top_correct'] = entry.get('primary_top_pick') == winner_idx
            result['shadow_top_correct'] = entry.get('shadow_top_pick') == winner_idx

        self.results[race_id] = result

    def get_comparison_report(self) -> dict:
        n_predictions = len(self.comparison_log)
        n_results = len(self.results)

        if n_predictions == 0:
            return {
                'n_races_compared': 0,
                'primary_metrics': {},
                'shadow_metrics': {},
                'agreement_rate': 0.0,
                'shadow_better_pct': 0.0,
                'recommendation': 'needs_more_data',
            }

        agreements = [e.get('agrees_on_top', False) for e in self.comparison_log if 'agrees_on_top' in e]
        agreement_rate = sum(agreements) / len(agreements) if agreements else 0.0

        primary_metrics = {}
        shadow_metrics = {}
        primary_correct = 0
        shadow_correct = 0
        primary_probs = []
        shadow_probs = []
        actuals = []

        for race_id, res in self.results.items():
            winner_idx = res.get('winner_idx')
            if winner_idx is None:
                continue

            entry = next((e for e in self.comparison_log if e['race_id'] == race_id), None)
            if not entry:
                continue

            p_preds = entry.get('primary_predictions')
            s_preds = entry.get('shadow_predictions')
            if not p_preds or not s_preds:
                continue

            if winner_idx < len(p_preds):
                primary_probs.append(p_preds[winner_idx])
                actuals.append(1)
                for i, p in enumerate(p_preds):
                    if i != winner_idx:
                        primary_probs.append(p)
                        actuals.append(0)

            if winner_idx < len(s_preds):
                shadow_probs.append(s_preds[winner_idx])

                for i, p in enumerate(s_preds):
                    if i != winner_idx:
                        shadow_probs.append(p)

            if res.get('primary_top_correct'):
                primary_correct += 1
            if res.get('shadow_top_correct'):
                shadow_correct += 1

        if SKLEARN_AVAILABLE and len(primary_probs) > 1 and len(actuals) > 1:
            n_actual = len(actuals)
            p_probs = primary_probs[:n_actual]
            s_probs = shadow_probs[:n_actual] if len(shadow_probs) >= n_actual else shadow_probs

            try:
                primary_metrics['auc'] = round(roc_auc_score(actuals, p_probs), 4)
            except (ValueError, IndexError):
                pass
            try:
                primary_metrics['brier'] = round(brier_score_loss(actuals, p_probs), 4)
            except (ValueError, IndexError):
                pass

            if len(s_probs) == n_actual:
                try:
                    shadow_metrics['auc'] = round(roc_auc_score(actuals, s_probs), 4)
                except (ValueError, IndexError):
                    pass
                try:
                    shadow_metrics['brier'] = round(brier_score_loss(actuals, s_probs), 4)
                except (ValueError, IndexError):
                    pass

        if n_results > 0:
            primary_metrics['top_pick_accuracy'] = round(primary_correct / n_results, 4)
            shadow_metrics['top_pick_accuracy'] = round(shadow_correct / n_results, 4)

        shadow_better_count = sum(
            1 for res in self.results.values()
            if res.get('shadow_winner_prob', 0) > res.get('primary_winner_prob', 0)
        )
        shadow_better_pct = shadow_better_count / n_results if n_results > 0 else 0.0

        recommendation = 'needs_more_data'
        if n_results >= 50:
            shadow_auc = shadow_metrics.get('auc', 0)
            primary_auc = primary_metrics.get('auc', 0)
            if shadow_auc > primary_auc + 0.02:
                recommendation = 'promote_shadow'
            else:
                recommendation = 'keep_primary'

        return {
            'name': self.name,
            'started_at': self.started_at,
            'n_races_compared': n_results,
            'n_predictions_logged': n_predictions,
            'primary_metrics': primary_metrics,
            'shadow_metrics': shadow_metrics,
            'agreement_rate': round(agreement_rate, 4),
            'shadow_better_pct': round(shadow_better_pct, 4),
            'recommendation': recommendation,
        }

    def should_promote_shadow(self, min_races: int = 50, required_improvement: float = 0.02) -> bool:
        report = self.get_comparison_report()
        if report['n_races_compared'] < min_races:
            return False
        shadow_auc = report['shadow_metrics'].get('auc', 0)
        primary_auc = report['primary_metrics'].get('auc', 0)
        return shadow_auc > primary_auc + required_improvement

    def save_log(self, path: str = None):
        if path is None:
            path = os.path.join(
                os.path.dirname(__file__), 'models',
                f'shadow_test_{self.name}.json'
            )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            'name': self.name,
            'started_at': self.started_at,
            'comparison_log': self.comparison_log,
            'results': self.results,
            'report': self.get_comparison_report(),
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        print(f"[Shadow] Saved test log to {path}")


if __name__ == '__main__':
    print("=== Model Versioning & Shadow Testing System ===")

    registry = ModelRegistry()
    print(f"Registry dir: {registry.registry_dir}")
    print(f"Loaded versions: {len(registry.versions)}")
    print(f"Active version: {registry.active_version}")

    print("\nVersion list:")
    for v in registry.list_versions():
        print(f"  {v['version_id']} - active={v['is_active']} - {v['description']}")

    print("\n[OK] Model versioning system ready")
