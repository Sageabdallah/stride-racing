#!/usr/bin/env python3
"""Feature store: caches computed features to avoid redundant computation and enables reuse across training and inference."""

import os
import json
import time
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Callable
from collections import OrderedDict

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    PANDAS_AVAILABLE = False

VALID_FEATURE_GROUPS = frozenset([
    'speed_map', 'staleness', 'market', 'form', 'sectional', 'all',
])

MAX_MEMORY_ENTRIES = 10000
MAX_DISK_RACES = 1000


def _normalize_key(horse_name: str, race_id: str, feature_group: str) -> str:
    return f"{horse_name.strip().lower()}_{str(race_id).strip().lower()}_{feature_group.strip().lower()}"


def _now_ts() -> float:
    return time.time()


class FeatureStore:
    """Thread-safe feature cache backed by memory (LRU) and disk (JSON)."""

    def __init__(self, cache_dir: str = None, ttl_hours: int = 24):
        self.cache_dir = cache_dir or os.path.join(os.path.dirname(__file__), 'feature_cache')
        self.ttl_hours = ttl_hours
        self._memory_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._stats = {'hits': 0, 'misses': 0, 'evictions': 0}
        os.makedirs(self.cache_dir, exist_ok=True)

    def _is_expired(self, timestamp: float) -> bool:
        return (_now_ts() - timestamp) > self.ttl_hours * 3600

    def _evict_lru(self):
        while len(self._memory_cache) > MAX_MEMORY_ENTRIES:
            self._memory_cache.popitem(last=False)
            self._stats['evictions'] += 1

    def _disk_path(self, race_id: str) -> str:
        safe_id = str(race_id).strip().lower().replace('/', '_').replace('\\', '_')
        return os.path.join(self.cache_dir, f"{safe_id}.json")

    def _read_disk(self, race_id: str) -> Optional[dict]:
        path = self._disk_path(race_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_disk(self, race_id: str, data: dict):
        existing_files = [f for f in os.listdir(self.cache_dir) if f.endswith('.json')]
        if len(existing_files) >= MAX_DISK_RACES:
            existing_files.sort(
                key=lambda f: os.path.getmtime(os.path.join(self.cache_dir, f))
            )
            for old_file in existing_files[:len(existing_files) - MAX_DISK_RACES + 1]:
                try:
                    os.remove(os.path.join(self.cache_dir, old_file))
                except OSError:
                    pass

        path = self._disk_path(race_id)
        try:
            with open(path, 'w') as f:
                json.dump(data, f, default=str)
        except OSError:
            pass

    def get_features(self, horse_name: str, race_id: str, feature_group: str = 'all') -> Optional[dict]:
        key = _normalize_key(horse_name, race_id, feature_group)
        with self._lock:
            entry = self._memory_cache.get(key)
            if entry is not None:
                if not self._is_expired(entry['timestamp']):
                    self._memory_cache.move_to_end(key)
                    self._stats['hits'] += 1
                    return entry['features']
                else:
                    del self._memory_cache[key]

        disk_data = self._read_disk(race_id)
        if disk_data is not None:
            horse_key = horse_name.strip().lower()
            group_key = feature_group.strip().lower()
            horse_entry = disk_data.get(horse_key)
            if horse_entry and isinstance(horse_entry, dict):
                group_entry = horse_entry.get(group_key)
                if group_entry and isinstance(group_entry, dict):
                    ts = group_entry.get('timestamp', 0)
                    if not self._is_expired(ts):
                        features = group_entry.get('features', {})
                        with self._lock:
                            self._memory_cache[key] = {'features': features, 'timestamp': ts}
                            self._memory_cache.move_to_end(key)
                            self._evict_lru()
                            self._stats['hits'] += 1
                        return features

        self._stats['misses'] += 1
        return None

    def set_features(self, horse_name: str, race_id: str, features: dict, feature_group: str = 'all'):
        key = _normalize_key(horse_name, race_id, feature_group)
        ts = _now_ts()

        with self._lock:
            self._memory_cache[key] = {'features': features, 'timestamp': ts}
            self._memory_cache.move_to_end(key)
            self._evict_lru()

        disk_data = self._read_disk(race_id) or {}
        horse_key = horse_name.strip().lower()
        group_key = feature_group.strip().lower()
        if horse_key not in disk_data:
            disk_data[horse_key] = {}
        disk_data[horse_key][group_key] = {
            'features': features,
            'timestamp': ts,
        }
        self._write_disk(race_id, disk_data)

    def get_or_compute(self, horse_name: str, race_id: str, compute_fn: Callable, feature_group: str = 'all') -> dict:
        cached = self.get_features(horse_name, race_id, feature_group)
        if cached is not None:
            return cached
        features = compute_fn()
        self.set_features(horse_name, race_id, features, feature_group)
        return features

    def invalidate(self, horse_name: str = None, race_id: str = None):
        with self._lock:
            if horse_name is None and race_id is None:
                evicted = len(self._memory_cache)
                self._memory_cache.clear()
                self._stats['evictions'] += evicted
                for f in os.listdir(self.cache_dir):
                    if f.endswith('.json'):
                        try:
                            os.remove(os.path.join(self.cache_dir, f))
                        except OSError:
                            pass
                return

            keys_to_remove = []
            horse_lower = horse_name.strip().lower() if horse_name else None
            race_lower = str(race_id).strip().lower() if race_id else None

            for k in self._memory_cache:
                parts = k.rsplit('_', 1)
                if horse_lower and horse_lower in k:
                    keys_to_remove.append(k)
                elif race_lower and race_lower in k:
                    keys_to_remove.append(k)

            for k in keys_to_remove:
                del self._memory_cache[k]
                self._stats['evictions'] += 1

        if race_id is not None:
            path = self._disk_path(race_id)
            if horse_name is not None:
                disk_data = self._read_disk(race_id)
                if disk_data:
                    horse_key = horse_name.strip().lower()
                    if horse_key in disk_data:
                        del disk_data[horse_key]
                        self._write_disk(race_id, disk_data)
            else:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
        elif horse_name is not None:
            for f in os.listdir(self.cache_dir):
                if not f.endswith('.json'):
                    continue
                fpath = os.path.join(self.cache_dir, f)
                try:
                    with open(fpath, 'r') as fh:
                        disk_data = json.load(fh)
                    horse_key = horse_name.strip().lower()
                    if horse_key in disk_data:
                        del disk_data[horse_key]
                        with open(fpath, 'w') as fh:
                            json.dump(disk_data, fh, default=str)
                except (json.JSONDecodeError, OSError):
                    pass

    def get_stats(self) -> dict:
        total = self._stats['hits'] + self._stats['misses']
        hit_rate = (self._stats['hits'] / total * 100) if total > 0 else 0.0
        return {
            'hits': self._stats['hits'],
            'misses': self._stats['misses'],
            'evictions': self._stats['evictions'],
            'hit_rate_pct': round(hit_rate, 2),
            'memory_entries': len(self._memory_cache),
            'disk_files': len([f for f in os.listdir(self.cache_dir) if f.endswith('.json')]),
        }

    def cleanup_expired(self):
        with self._lock:
            expired_keys = [
                k for k, v in self._memory_cache.items()
                if self._is_expired(v['timestamp'])
            ]
            for k in expired_keys:
                del self._memory_cache[k]
                self._stats['evictions'] += 1

        for f in os.listdir(self.cache_dir):
            if not f.endswith('.json'):
                continue
            fpath = os.path.join(self.cache_dir, f)
            try:
                with open(fpath, 'r') as fh:
                    disk_data = json.load(fh)
                changed = False
                for horse_key in list(disk_data.keys()):
                    for group_key in list(disk_data[horse_key].keys()):
                        entry = disk_data[horse_key][group_key]
                        if isinstance(entry, dict) and self._is_expired(entry.get('timestamp', 0)):
                            del disk_data[horse_key][group_key]
                            changed = True
                    if not disk_data[horse_key]:
                        del disk_data[horse_key]
                        changed = True
                if changed:
                    if disk_data:
                        with open(fpath, 'w') as fh:
                            json.dump(disk_data, fh, default=str)
                    else:
                        os.remove(fpath)
            except (json.JSONDecodeError, OSError):
                pass

    def get_training_features(self, race_ids: list):
        if not PANDAS_AVAILABLE:
            raise ImportError("pandas is required for get_training_features")

        rows = []
        for race_id in race_ids:
            disk_data = self._read_disk(race_id)
            if not disk_data:
                continue
            for horse_key, groups in disk_data.items():
                if not isinstance(groups, dict):
                    continue
                all_entry = groups.get('all')
                if all_entry and isinstance(all_entry, dict):
                    features = all_entry.get('features', {})
                    if features:
                        row = {'race_id': race_id, 'horse_name': horse_key}
                        row.update(features)
                        rows.append(row)
                        continue
                merged = {}
                for group_key, entry in groups.items():
                    if isinstance(entry, dict) and 'features' in entry:
                        merged.update(entry['features'])
                if merged:
                    row = {'race_id': race_id, 'horse_name': horse_key}
                    row.update(merged)
                    rows.append(row)

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def cache_training_batch(self, features_df, race_id_col: str = 'race_id', horse_col: str = 'horse_name'):
        if not PANDAS_AVAILABLE:
            raise ImportError("pandas is required for cache_training_batch")
        if features_df is None or len(features_df) == 0:
            return

        feature_cols = [c for c in features_df.columns if c not in (race_id_col, horse_col)]

        for _, row in features_df.iterrows():
            race_id = str(row.get(race_id_col, ''))
            horse_name = str(row.get(horse_col, ''))
            if not race_id or not horse_name:
                continue
            features = {}
            for col in feature_cols:
                val = row[col]
                if PANDAS_AVAILABLE and pd.notna(val):
                    if hasattr(val, 'item'):
                        features[col] = val.item()
                    else:
                        features[col] = val
            self.set_features(horse_name, race_id, features, feature_group='all')


class FeatureRegistry:
    """Registry of all available features and their metadata."""

    def __init__(self):
        self.features: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, group: str, dtype: str, description: str, source_module: str):
        self.features[name] = {
            'name': name,
            'group': group,
            'dtype': dtype,
            'description': description,
            'source_module': source_module,
            'registered_at': datetime.now(timezone.utc).isoformat(),
        }

    def get_feature_info(self, name: str) -> dict:
        return self.features.get(name, {})

    def list_features(self, group: str = None) -> list:
        if group is None:
            return list(self.features.keys())
        return [n for n, meta in self.features.items() if meta.get('group') == group]

    def get_schema(self) -> dict:
        schema: Dict[str, list] = {}
        for name, meta in self.features.items():
            grp = meta.get('group', 'unknown')
            if grp not in schema:
                schema[grp] = []
            schema[grp].append({
                'name': name,
                'dtype': meta.get('dtype', 'float'),
                'description': meta.get('description', ''),
                'source_module': meta.get('source_module', ''),
            })
        return schema


def _build_default_registry() -> FeatureRegistry:
    registry = FeatureRegistry()

    _base_features = {
        'distance_strike_rate': ('form', 'float', 'Strike rate at this distance', 'advanced_features'),
        'course_strike_rate': ('form', 'float', 'Strike rate at this course', 'advanced_features'),
        'weighted_form_score': ('form', 'float', 'Weighted recent form score', 'advanced_features'),
        'is_first_up': ('form', 'bool', 'First start this preparation', 'advanced_features'),
        'is_second_up': ('form', 'bool', 'Second start this preparation', 'advanced_features'),
        'days_since_run': ('staleness', 'float', 'Days since last race start', 'temporal_staleness'),
        'jockey_trainer_strike_rate': ('form', 'float', 'Jockey-trainer combination strike rate', 'advanced_features'),
        'is_winning_combo': ('form', 'bool', 'Jockey-trainer is a winning combination', 'advanced_features'),
        'barrier_draw': ('form', 'int', 'Barrier draw position', 'enhanced_features'),
        'weight_kg': ('form', 'float', 'Weight carried in kilograms', 'enhanced_features'),
        'ground_suitability': ('form', 'float', 'Suitability score for track conditions', 'advanced_features'),
        'market_odds': ('market', 'float', 'Current market odds', 'market_analysis'),
    }

    _speed_features = {
        'running_style_score': ('speed_map', 'float', 'Numeric running style classification', 'speed_mapping'),
        'pace_advantage': ('speed_map', 'float', 'Pace advantage score from speed map', 'speed_mapping'),
        'is_high_pace_expected': ('speed_map', 'bool', 'Whether fast pace is expected', 'speed_mapping'),
        'distance_style_match': ('speed_map', 'float', 'Distance and running style compatibility', 'speed_mapping'),
    }

    _class_features = {
        'class_movement': ('form', 'float', 'Class movement from last start', 'enhanced_features'),
        'is_class_drop': ('form', 'bool', 'Horse dropping in class', 'enhanced_features'),
        'is_class_rise': ('form', 'bool', 'Horse rising in class', 'enhanced_features'),
        'is_first_time_stakes': ('form', 'bool', 'First time at stakes level', 'enhanced_features'),
    }

    _signals_features = {
        'is_blinkers_first_time': ('form', 'bool', 'First time blinkers applied', 'enhanced_features'),
        'is_elite_jockey': ('form', 'bool', 'Elite jockey engaged', 'jockey_momentum'),
        'is_elite_trainer': ('form', 'bool', 'Elite trainer stable', 'jockey_momentum'),
        'is_jockey_upgrade': ('form', 'bool', 'Jockey upgrade from last start', 'jockey_momentum'),
    }

    _track_bias_features = {
        'barrier_advantage': ('form', 'float', 'Barrier advantage from track bias', 'track_bias_points'),
        'track_bias_score': ('form', 'float', 'Track bias score', 'track_bias_points'),
    }

    _form_pattern_features = {
        'is_improving': ('form', 'bool', 'Horse showing improving form pattern', 'advanced_features'),
        'improvement_score': ('form', 'float', 'Form improvement score', 'advanced_features'),
        'is_in_form_cycle': ('form', 'bool', 'Horse is in a positive form cycle', 'advanced_features'),
        'has_dominant_win': ('form', 'bool', 'Recent dominant winning performance', 'advanced_features'),
    }

    _market_features = {
        'is_steam_move': ('market', 'bool', 'Horse is steaming in the market', 'market_analysis'),
        'is_drift': ('market', 'bool', 'Horse is drifting in the market', 'market_analysis'),
        'odds_movement_pct': ('market', 'float', 'Percentage odds movement', 'market_analysis'),
        'last_start_market_diff': ('market', 'float', 'Performance vs market expectation last start', 'market_analysis'),
        'avg_market_diff_3runs': ('market', 'float', 'Avg performance vs market over 3 runs', 'market_analysis'),
        'market_trend_shortening': ('market', 'bool', 'Market trend is shortening', 'market_analysis'),
        'market_trend_drifting': ('market', 'bool', 'Market trend is drifting', 'market_analysis'),
        'empirical_barrier_advantage': ('form', 'float', 'Empirical barrier advantage from historical data', 'empirical_barriers'),
    }

    _phase1_speed_map_features = {
        'predicted_settling_pos': ('speed_map', 'float', 'Predicted settling position at first turn', 'speed_mapping'),
        'settling_percentile': ('speed_map', 'float', 'Settling position as field percentile', 'speed_mapping'),
        'pace_pressure_index': ('speed_map', 'float', 'Pace pressure index for the race', 'speed_mapping'),
        'settling_difficulty': ('speed_map', 'float', 'Difficulty of settling into position', 'speed_mapping'),
        'settling_pace_interaction': ('speed_map', 'float', 'Interaction between settling and pace', 'speed_mapping'),
        'is_congested_speed': ('speed_map', 'bool', 'Congested speed battle expected', 'speed_mapping'),
        'speed_horse_ratio': ('speed_map', 'float', 'Ratio of speed horses in the field', 'speed_mapping'),
    }

    _phase1_staleness_features = {
        'days_since_last_normalized': ('staleness', 'float', 'Days since last start normalized (0-1)', 'temporal_staleness'),
        'prep_run_x_days_since': ('staleness', 'float', 'Prep run count x days since interaction', 'temporal_staleness'),
        'run_spacing_quality': ('staleness', 'float', 'Campaign run spacing regularity', 'temporal_staleness'),
        'empirical_freshness_score': ('staleness', 'float', 'Empirical freshness score from win-rate bands', 'temporal_staleness'),
        'is_quick_backup': ('staleness', 'bool', 'Quick backup (<10 days)', 'temporal_staleness'),
        'is_long_absence': ('staleness', 'bool', 'Long absence (>90 days)', 'temporal_staleness'),
        'distance_change_staleness': ('staleness', 'float', 'Distance change x staleness interaction', 'temporal_staleness'),
        'class_x_spell': ('staleness', 'float', 'Class level x spell length interaction', 'temporal_staleness'),
    }

    _phase1_market_features = {
        'steam_velocity': ('market', 'float', 'Rate of price shortening', 'market_velocity'),
        'drift_velocity': ('market', 'float', 'Rate of price drifting', 'market_velocity'),
        'late_move_indicator': ('market', 'bool', 'Late significant price movement', 'market_velocity'),
        'market_confidence': ('market', 'float', 'Field market confidence score', 'market_velocity'),
        'relative_move': ('market', 'float', 'Price move relative to field average', 'market_velocity'),
        'smart_money_score': ('market', 'float', 'Composite smart money score', 'market_velocity'),
        'is_insider_signal': ('market', 'bool', 'Potential insider signal detected', 'market_velocity'),
        'field_market_agreement': ('market', 'float', 'Field-wide market agreement level', 'market_velocity'),
    }

    all_groups = [
        _base_features, _speed_features, _class_features, _signals_features,
        _track_bias_features, _form_pattern_features, _market_features,
        _phase1_speed_map_features, _phase1_staleness_features, _phase1_market_features,
    ]
    for group in all_groups:
        for name, (grp, dtype, desc, src) in group.items():
            registry.register(name, grp, dtype, desc, src)

    return registry


DEFAULT_REGISTRY = _build_default_registry()


def get_feature_store(cache_dir: str = None, ttl_hours: int = 24) -> FeatureStore:
    return FeatureStore(cache_dir=cache_dir, ttl_hours=ttl_hours)


def get_feature_registry() -> FeatureRegistry:
    return DEFAULT_REGISTRY


if __name__ == '__main__':
    print("=" * 60)
    print("Feature Store Module - Test Suite")
    print("=" * 60)

    import tempfile
    import shutil

    test_dir = tempfile.mkdtemp(prefix='feature_store_test_')
    try:
        store = FeatureStore(cache_dir=test_dir, ttl_hours=1)

        print("\n--- Test 1: Set and get features ---")
        test_features = {
            'pace_advantage': 0.12,
            'running_style_score': 3,
            'is_high_pace_expected': True,
        }
        store.set_features('Winx', 'race_001', test_features, 'speed_map')
        retrieved = store.get_features('Winx', 'race_001', 'speed_map')
        assert retrieved == test_features, f"Expected {test_features}, got {retrieved}"
        print("  PASS: set/get round-trip")

        print("\n--- Test 2: Cache miss returns None ---")
        miss = store.get_features('Unknown Horse', 'race_999', 'all')
        assert miss is None, f"Expected None, got {miss}"
        print("  PASS: cache miss")

        print("\n--- Test 3: get_or_compute ---")
        compute_called = [False]
        def my_compute():
            compute_called[0] = True
            return {'computed': True, 'value': 42}

        result = store.get_or_compute('Test Horse', 'race_002', my_compute, 'form')
        assert compute_called[0], "compute_fn should have been called"
        assert result == {'computed': True, 'value': 42}

        compute_called[0] = False
        result2 = store.get_or_compute('Test Horse', 'race_002', my_compute, 'form')
        assert not compute_called[0], "compute_fn should NOT have been called (cached)"
        assert result2 == {'computed': True, 'value': 42}
        print("  PASS: get_or_compute")

        print("\n--- Test 4: Invalidate by race_id ---")
        store.set_features('Horse A', 'race_003', {'a': 1}, 'all')
        store.set_features('Horse B', 'race_003', {'b': 2}, 'all')
        store.invalidate(race_id='race_003')
        assert store.get_features('Horse A', 'race_003', 'all') is None
        assert store.get_features('Horse B', 'race_003', 'all') is None
        print("  PASS: invalidate by race_id")

        print("\n--- Test 5: Stats ---")
        stats = store.get_stats()
        print(f"  Stats: {stats}")
        assert stats['hits'] >= 2
        assert stats['misses'] >= 1
        print("  PASS: stats")

        print("\n--- Test 6: Feature Registry ---")
        registry = get_feature_registry()
        all_features = registry.list_features()
        print(f"  Total registered features: {len(all_features)}")
        assert len(all_features) >= 40, f"Expected 40+ features, got {len(all_features)}"

        speed_map_features = registry.list_features('speed_map')
        print(f"  Speed map features: {len(speed_map_features)}")
        assert 'pace_advantage' in speed_map_features

        market_features = registry.list_features('market')
        print(f"  Market features: {len(market_features)}")
        assert 'smart_money_score' in market_features

        staleness_features = registry.list_features('staleness')
        print(f"  Staleness features: {len(staleness_features)}")
        assert 'empirical_freshness_score' in staleness_features

        schema = registry.get_schema()
        print(f"  Schema groups: {list(schema.keys())}")
        print("  PASS: feature registry")

        print("\n--- Test 7: Training batch cache ---")
        if PANDAS_AVAILABLE:
            df = pd.DataFrame([
                {'race_id': 'race_010', 'horse_name': 'Alpha', 'pace_advantage': 0.1, 'form_score': 80},
                {'race_id': 'race_010', 'horse_name': 'Beta', 'pace_advantage': -0.05, 'form_score': 65},
                {'race_id': 'race_011', 'horse_name': 'Gamma', 'pace_advantage': 0.2, 'form_score': 90},
            ])
            store.cache_training_batch(df)
            train_df = store.get_training_features(['race_010', 'race_011'])
            print(f"  Training DF shape: {train_df.shape}")
            assert len(train_df) == 3, f"Expected 3 rows, got {len(train_df)}"
            print("  PASS: training batch")
        else:
            print("  SKIP: pandas not available")

        print("\n--- Test 8: Cleanup expired ---")
        store.cleanup_expired()
        print("  PASS: cleanup ran without error")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    print("All tests completed successfully.")
    print("=" * 60)
