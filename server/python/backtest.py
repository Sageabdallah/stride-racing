#!/usr/bin/env python3
"""Walk-forward ensemble backtesting system for the racing ML model."""

import os
import sys
import json
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

DB_URL = os.environ.get('DATABASE_URL', '')

try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False
    print("WARNING: scikit-learn not available")

try:
    from xgboost import XGBClassifier
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("WARNING: xgboost not available, will use GBM only")


def get_training_data():
    from sqlalchemy import create_engine, text
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT race_id, track, race_number, race_date, distance, going, race_class,
                   horse_name, horse_number, barrier, jockey, trainer, weight,
                   market_odds, actual_position, won, placed, starting_price
            FROM training_data
            WHERE actual_position IS NOT NULL AND starting_price > 0 AND race_date IS NOT NULL
            ORDER BY race_date, track, race_number
        """))
        rows = result.fetchall()
        columns = result.keys()
        return [dict(zip(columns, row)) for row in rows]


def build_horse_history(data):
    race_field_sizes = defaultdict(int)
    for r in data:
        key = f"{r['race_date']}_{r['track']}_{r['race_number']}"
        race_field_sizes[key] += 1

    horse_history = defaultdict(list)
    for r in data:
        name = r.get('horse_name', '')
        if name:
            key = f"{r['race_date']}_{r['track']}_{r['race_number']}"
            r_copy = dict(r)
            r_copy['_field_size'] = race_field_sizes.get(key, 8)
            horse_history[name].append(r_copy)
    for name in horse_history:
        horse_history[name].sort(key=lambda x: x.get('race_date', ''))
    return horse_history


def get_horse_runs_before(horse_name, race_date, horse_history):
    runs = horse_history.get(horse_name, [])
    return [r for r in runs if r.get('race_date', '') < race_date]


def group_by_race(data):
    races = defaultdict(list)
    for r in data:
        key = f"{r['race_date']}_{r['track']}_{r['race_number']}"
        races[key].append(r)
    return races


def parse_distance(d):
    try:
        return int(str(d).replace('m', '').strip())
    except:
        return 1400


def get_class_level(race_class):
    rc = str(race_class).upper()
    if 'GROUP 1' in rc:
        return 10
    elif 'GROUP 2' in rc:
        return 9
    elif 'GROUP 3' in rc:
        return 8
    elif 'LISTED' in rc:
        return 7
    elif 'OPEN' in rc:
        return 6
    elif 'BM' in rc:
        try:
            return min(6, int(''.join(c for c in rc if c.isdigit())[:3]) // 15)
        except:
            return 3
    elif 'MDN' in rc or 'MAIDEN' in rc:
        return 1
    return 2


def is_wet(going):
    g = str(going).lower()
    return 'soft' in g or 'heavy' in g


def get_barrier_group(barrier):
    if barrier <= 4:
        return 'inside'
    elif barrier <= 8:
        return 'middle'
    else:
        return 'wide'


def get_dist_type(distance):
    return 'sprint' if distance < 1200 else 'longer'


def extract_features(runner, race_runners, stats, horse_history, race_date):
    sp = float(runner.get('starting_price', 0) or runner.get('market_odds', 0) or 10)
    if sp <= 1:
        sp = 10

    field_size = len(race_runners)
    barrier = int(runner.get('barrier', 0) or 0)
    weight = float(runner.get('weight', 55) or 55)
    distance = parse_distance(runner.get('distance', '1400'))
    market_prob = 1.0 / sp

    all_sps = [float(r.get('starting_price', 0) or r.get('market_odds', 0) or 10) for r in race_runners]
    all_sps = [s for s in all_sps if s > 1]
    if not all_sps:
        all_sps = [sp]

    fav_sp = min(all_sps)
    sorted_sps = sorted(all_sps)
    try:
        sp_rank = sorted_sps.index(sp)
    except ValueError:
        sp_rank = len(sorted_sps) // 2

    total_implied = sum(1.0 / s for s in all_sps)
    overround = total_implied - 1.0

    jockey = runner.get('jockey', '')
    trainer = runner.get('trainer', '')
    track = runner.get('track', '')

    j_data = stats['jockey'].get(jockey, {'strike_rate': 0.08, 'runs': 0, 'place_rate': 0.25})
    t_data = stats['trainer'].get(trainer, {'strike_rate': 0.08, 'runs': 0, 'place_rate': 0.25})
    jt_key = f"{jockey}|{trainer}"
    jt_data = stats['jockey_trainer'].get(jt_key, {'strike_rate': 0.08, 'runs': 0})

    inside_sr = stats['track_barrier'].get(track, {}).get('inside_sr', 0.10)
    outside_sr = stats['track_barrier'].get(track, {}).get('outside_sr', 0.10)
    barrier_advantage = 0
    if barrier <= 4:
        barrier_advantage = inside_sr - 0.10
    elif barrier >= 10:
        barrier_advantage = outside_sr - 0.10

    going = str(runner.get('going', '')).lower()
    race_class = str(runner.get('race_class', ''))
    class_level = get_class_level(race_class)

    horse_name = runner.get('horse_name', '')
    prior_runs = get_horse_runs_before(horse_name, race_date, horse_history)

    form_trend_slope = 0.0
    avg_finish_last3 = 0.5
    best_finish_last3 = 0.5
    runs_since_win = 15
    days_since_last_run = 60.0 / 90.0

    if len(prior_runs) >= 3:
        last5 = prior_runs[-5:]
        positions = []
        for pr in last5:
            pos = pr.get('actual_position')
            if pos is not None:
                try:
                    positions.append(float(pos))
                except:
                    pass
        if len(positions) >= 3:
            x_vals = np.arange(len(positions))
            slope, _ = np.polyfit(x_vals, positions, 1)
            form_trend_slope = slope

    if len(prior_runs) >= 1:
        last3 = prior_runs[-3:]
        finishes = []
        for pr in last3:
            pos = pr.get('actual_position')
            if pos is not None:
                try:
                    pr_field = max(float(pr.get('_field_size', 8)), 2)
                    finishes.append(float(pos) / pr_field)
                except:
                    finishes.append(0.5)
        if finishes:
            avg_finish_last3 = np.mean(finishes)
            best_finish_last3 = min(finishes)

    if prior_runs:
        count = 0
        for pr in reversed(prior_runs):
            if pr.get('won', 0) == 1:
                break
            count += 1
        runs_since_win = min(count, 15)

        last_date_str = prior_runs[-1].get('race_date', '')
        if last_date_str:
            try:
                last_dt = datetime.strptime(last_date_str, '%Y-%m-%d')
                current_dt = datetime.strptime(race_date, '%Y-%m-%d')
                days_since_last_run = (current_dt - last_dt).days / 90.0
            except:
                pass

    class_change = 0.0
    class_drop_close_up = 0
    class_ceiling = 0

    if len(prior_runs) >= 1:
        last3_classes = []
        for pr in prior_runs[-3:]:
            last3_classes.append(get_class_level(pr.get('race_class', '')))
        if last3_classes:
            avg_class = np.mean(last3_classes)
            class_change = (class_level - avg_class) / 10.0

    if len(prior_runs) >= 2:
        last_class = get_class_level(prior_runs[-1].get('race_class', ''))
        if class_level <= last_class - 2:
            higher_runs = [pr for pr in prior_runs if get_class_level(pr.get('race_class', '')) >= last_class]
            if higher_runs:
                last_higher = higher_runs[-1]
                if last_higher.get('actual_position') is not None:
                    try:
                        if float(last_higher['actual_position']) <= 4:
                            class_drop_close_up = 1
                    except:
                        pass

    if len(prior_runs) >= 3:
        high_class_runs = [pr for pr in prior_runs if get_class_level(pr.get('race_class', '')) >= class_level]
        if len(high_class_runs) >= 3:
            any_placed = any(pr.get('placed', 0) == 1 for pr in high_class_runs)
            if not any_placed:
                class_ceiling = 1

    wet_track_win_rate = 0.10
    wet_specialist_score = 0.0
    going_suitability = 0.0

    if prior_runs:
        wet_runs = [pr for pr in prior_runs if is_wet(pr.get('going', ''))]
        dry_runs = [pr for pr in prior_runs if not is_wet(pr.get('going', ''))]

        if len(wet_runs) >= 3:
            wet_wins = sum(1 for pr in wet_runs if pr.get('won', 0) == 1)
            wet_track_win_rate = wet_wins / len(wet_runs)

        if len(wet_runs) >= 3 and len(dry_runs) >= 3:
            wet_wr = sum(1 for pr in wet_runs if pr.get('won', 0) == 1) / len(wet_runs)
            dry_wr = sum(1 for pr in dry_runs if pr.get('won', 0) == 1) / len(dry_runs)
            wet_specialist_score = wet_wr - dry_wr

        today_wet = is_wet(runner.get('going', ''))
        if today_wet:
            going_suitability = wet_specialist_score
        else:
            going_suitability = -wet_specialist_score

    bg = get_barrier_group(barrier)
    dt = get_dist_type(distance)
    tbd_key = (track, bg, dt)

    tbd_stats = stats.get('track_barrier_dist', {})
    tbd_data = tbd_stats.get(tbd_key, {'win_rate': 0.10})
    track_barrier_dist_winrate = tbd_data.get('win_rate', 0.10)

    track_dt_key = (track, dt)
    track_dt_stats = stats.get('track_dist_avg', {})
    track_dt_avg = track_dt_stats.get(track_dt_key, 0.10)
    barrier_dist_advantage = track_barrier_dist_winrate - track_dt_avg

    wide_sprint_penalty = 0
    if barrier >= 9 and distance < 1200 and track_barrier_dist_winrate < 0.07:
        wide_sprint_penalty = 1

    flemington_middle_bias = 0
    if 'flemington' in track.lower() and distance >= 1200 and 5 <= barrier <= 8:
        flemington_middle_bias = 1

    return [
        market_prob,
        np.log(sp),
        field_size,
        barrier / 20.0,
        weight / 60.0,
        distance / 2000.0,
        1 if abs(sp - fav_sp) < 0.5 else 0,
        sp_rank / max(field_size, 1),
        overround,
        j_data['strike_rate'],
        min(j_data['runs'] / 100, 1.0),
        j_data.get('place_rate', 0.25),
        t_data['strike_rate'],
        min(t_data['runs'] / 100, 1.0),
        t_data.get('place_rate', 0.25),
        jt_data['strike_rate'],
        min(jt_data.get('runs', 0) / 20, 1.0),
        barrier_advantage,
        1 if 'heavy' in going else 0,
        1 if 'soft' in going and 'heavy' not in going else 0,
        class_level / 10.0,
        1 if distance < 1200 else 0,
        1 if 1200 <= distance <= 1600 else 0,
        1 if distance > 2000 else 0,
        weight * barrier / 1000.0,
        sp / max(field_size, 1),
        market_prob * j_data['strike_rate'] * 10,
        market_prob * t_data['strike_rate'] * 10,
        form_trend_slope,
        avg_finish_last3,
        best_finish_last3,
        runs_since_win / 15.0,
        days_since_last_run,
        class_change,
        class_drop_close_up,
        class_ceiling,
        wet_track_win_rate,
        wet_specialist_score,
        going_suitability,
        track_barrier_dist_winrate,
        barrier_dist_advantage,
        wide_sprint_penalty,
        flemington_middle_bias,
    ]


def build_stats(train_data):
    jockey_stats = defaultdict(lambda: {'wins': 0, 'runs': 0, 'places': 0, 'strike_rate': 0.08, 'place_rate': 0.25})
    trainer_stats = defaultdict(lambda: {'wins': 0, 'runs': 0, 'places': 0, 'strike_rate': 0.08, 'place_rate': 0.25})
    jt_stats = defaultdict(lambda: {'wins': 0, 'runs': 0, 'strike_rate': 0.08})
    track_barrier = defaultdict(lambda: {'inside_wins': 0, 'inside_runs': 0, 'outside_wins': 0, 'outside_runs': 0, 'inside_sr': 0.10, 'outside_sr': 0.10})
    tbd_stats = defaultdict(lambda: {'wins': 0, 'runs': 0, 'win_rate': 0.10})
    track_dist_totals = defaultdict(lambda: {'wins': 0, 'runs': 0})

    for r in train_data:
        jockey = r.get('jockey', '')
        trainer = r.get('trainer', '')
        track = r.get('track', '')
        won = r.get('won', 0) == 1
        placed = r.get('placed', 0) == 1
        barrier = int(r.get('barrier', 0) or 0)
        distance = parse_distance(r.get('distance', '1400'))

        if jockey:
            jockey_stats[jockey]['runs'] += 1
            if won: jockey_stats[jockey]['wins'] += 1
            if placed: jockey_stats[jockey]['places'] += 1
        if trainer:
            trainer_stats[trainer]['runs'] += 1
            if won: trainer_stats[trainer]['wins'] += 1
            if placed: trainer_stats[trainer]['places'] += 1
        jt_key = f"{jockey}|{trainer}"
        if jockey and trainer:
            jt_stats[jt_key]['runs'] += 1
            if won: jt_stats[jt_key]['wins'] += 1
        if track:
            if barrier <= 4:
                track_barrier[track]['inside_runs'] += 1
                if won: track_barrier[track]['inside_wins'] += 1
            elif barrier >= 10:
                track_barrier[track]['outside_runs'] += 1
                if won: track_barrier[track]['outside_wins'] += 1

        if track:
            bg = get_barrier_group(barrier)
            dt = get_dist_type(distance)
            tbd_key = (track, bg, dt)
            tbd_stats[tbd_key]['runs'] += 1
            if won:
                tbd_stats[tbd_key]['wins'] += 1

            td_key = (track, dt)
            track_dist_totals[td_key]['runs'] += 1
            if won:
                track_dist_totals[td_key]['wins'] += 1

    for s in jockey_stats.values():
        if s['runs'] >= 10:
            s['strike_rate'] = s['wins'] / s['runs']
            s['place_rate'] = s['places'] / s['runs']
    for s in trainer_stats.values():
        if s['runs'] >= 10:
            s['strike_rate'] = s['wins'] / s['runs']
            s['place_rate'] = s['places'] / s['runs']
    for s in jt_stats.values():
        if s['runs'] >= 3:
            s['strike_rate'] = s['wins'] / s['runs']
    for s in track_barrier.values():
        if s['inside_runs'] >= 10:
            s['inside_sr'] = s['inside_wins'] / s['inside_runs']
        if s['outside_runs'] >= 10:
            s['outside_sr'] = s['outside_wins'] / s['outside_runs']
    for s in tbd_stats.values():
        if s['runs'] >= 5:
            s['win_rate'] = s['wins'] / s['runs']

    track_dist_avg = {}
    for td_key, s in track_dist_totals.items():
        if s['runs'] >= 10:
            track_dist_avg[td_key] = s['wins'] / s['runs']
        else:
            track_dist_avg[td_key] = 0.10

    return {
        'jockey': dict(jockey_stats),
        'trainer': dict(trainer_stats),
        'jockey_trainer': dict(jt_stats),
        'track_barrier': dict(track_barrier),
        'track_barrier_dist': dict(tbd_stats),
        'track_dist_avg': dict(track_dist_avg),
    }


def train_ensemble(train_data, stats, horse_history):
    if not ML_AVAILABLE:
        return None, None, None, None

    train_races = group_by_race(train_data)
    X_list, y_list = [], []

    for race_key, runners in train_races.items():
        if len(runners) < 3:
            continue
        race_date = runners[0].get('race_date', '')
        for runner in runners:
            X_list.append(extract_features(runner, runners, stats, horse_history, race_date))
            y_list.append(1 if runner.get('won', 0) == 1 else 0)

    if len(X_list) < 100:
        return None, None, None, None

    X = np.array(X_list)
    y = np.array(y_list)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    gbm = GradientBoostingClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.1,
        min_samples_leaf=30, subsample=0.8, random_state=42,
    )
    gbm.fit(X_scaled, y)

    lr = LogisticRegression(C=1.0, max_iter=1000, solver='lbfgs')
    lr.fit(X_scaled, y)

    xgb_model = None
    if XGB_AVAILABLE:
        xgb_model = XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.1,
            reg_alpha=0.1, reg_lambda=1.0,
            use_label_encoder=False, eval_metric='logloss',
            random_state=42,
        )
        xgb_model.fit(X_scaled, y)

    train_probs = gbm.predict_proba(X_scaled)[:, 1]
    winners_mask = y == 1
    sep = train_probs[winners_mask].mean() / max(train_probs[~winners_mask].mean(), 0.001)
    print(f"    Ensemble trained: {len(X)} samples, {y.sum()} winners, GBM separation: {sep:.2f}x")

    models = {'gbm': gbm, 'lr': lr, 'xgb': xgb_model}
    return models, scaler, X, y


def predict_ensemble(race_runners, stats, models, scaler, horse_history, race_date):
    results = {}
    feature_list = []
    names = []

    for runner in race_runners:
        feats = extract_features(runner, race_runners, stats, horse_history, race_date)
        feature_list.append(feats)
        names.append(runner.get('horse_name', ''))

    if not feature_list:
        return results

    X = np.array(feature_list)
    X_scaled = scaler.transform(X)

    gbm_probs = models['gbm'].predict_proba(X_scaled)[:, 1]
    lr_probs = models['lr'].predict_proba(X_scaled)[:, 1]

    if models.get('xgb') is not None:
        xgb_probs = models['xgb'].predict_proba(X_scaled)[:, 1]
    else:
        xgb_probs = gbm_probs.copy()

    raw_ensemble = 0.40 * gbm_probs + 0.35 * xgb_probs + 0.25 * lr_probs

    total = raw_ensemble.sum()
    if total > 0:
        ensemble_norm = raw_ensemble / total
    else:
        ensemble_norm = raw_ensemble

    gbm_total = gbm_probs.sum()
    xgb_total = xgb_probs.sum()
    lr_total = lr_probs.sum()
    gbm_norm = gbm_probs / max(gbm_total, 1e-10)
    xgb_norm = xgb_probs / max(xgb_total, 1e-10)
    lr_norm = lr_probs / max(lr_total, 1e-10)

    for i, name in enumerate(names):
        agreement = 1.0 - np.std([gbm_norm[i], xgb_norm[i], lr_norm[i]])
        results[name] = {
            'prob': ensemble_norm[i],
            'agreement': agreement,
        }

    return results


def optimize_threshold(train_data, stats, models, scaler, horse_history):
    dates = sorted(set(r['race_date'] for r in train_data))
    if len(dates) < 10:
        return 0.20

    split_idx = int(len(dates) * 0.8)
    val_start = dates[split_idx]
    val_data = [r for r in train_data if r['race_date'] >= val_start]
    val_races = group_by_race(val_data)

    if len(val_races) < 10:
        return 0.20

    race_preds = {}
    for race_key, runners in val_races.items():
        race_date = runners[0].get('race_date', '')
        preds = predict_ensemble(runners, stats, models, scaler, horse_history, race_date)
        race_preds[race_key] = (runners, preds)

    best_threshold = 0.20
    best_roi = -999

    for thresh_int in range(15, 41):
        thresh = thresh_int / 100.0
        total_bets = 0
        total_returned = 0

        for race_key, (runners, preds) in race_preds.items():
            candidates = []
            for runner in runners:
                name = runner.get('horse_name', '')
                pred = preds.get(name, {})
                prob = pred.get('prob', 0)
                sp = float(runner.get('starting_price', 0) or 10)
                if sp <= 1:
                    continue
                if prob >= thresh:
                    implied = 1.0 / sp
                    edge = prob - implied
                    if edge >= 0.02 and 2.0 <= sp <= 8.0:
                        ev = prob * sp - 1
                        candidates.append((runner, prob, sp, ev))

            if candidates:
                best = max(candidates, key=lambda x: x[3])
                total_bets += 1
                if best[0].get('won', 0) == 1:
                    total_returned += 100 * best[2]

        staked = total_bets * 100
        if total_bets >= 15 and staked > 0:
            roi = (total_returned - staked) / staked * 100
            if roi > best_roi:
                best_roi = roi
                best_threshold = thresh

    print(f"    Optimized threshold: {best_threshold:.2f} (validation ROI: {best_roi:+.1f}%)")
    return best_threshold


def get_strategies(optimized_threshold):
    strategies = [
        ('ML Baseline ($2-$15, 2%+ edge)', {'min_odds': 2.0, 'max_odds': 15.0, 'min_win_prob': 0.10, 'min_edge': 0.02, 'min_field': 5, 'max_field': 18}),
        ('ML $3-$8 Mid-Range', {'min_odds': 3.0, 'max_odds': 8.0, 'min_win_prob': 0.12, 'min_edge': 0.03, 'min_field': 5, 'max_field': 14}),
        ('ML $3-$10 Moderate Edge 5%', {'min_odds': 3.0, 'max_odds': 10.0, 'min_win_prob': 0.10, 'min_edge': 0.05, 'min_field': 5, 'max_field': 16}),
        ('ML $4-$12 Value', {'min_odds': 4.0, 'max_odds': 12.0, 'min_win_prob': 0.08, 'min_edge': 0.03, 'min_field': 5, 'max_field': 16}),
        ('ML $2-$5 Short Price High Prob', {'min_odds': 2.0, 'max_odds': 5.0, 'min_win_prob': 0.22, 'min_edge': 0.02, 'min_field': 5, 'max_field': 14}),
        ('ML $3-$10 Selective 8%+ edge', {'min_odds': 3.0, 'max_odds': 10.0, 'min_win_prob': 0.12, 'min_edge': 0.08, 'min_field': 6, 'max_field': 14}),
        ('ML $2.5-$6 Conservative', {'min_odds': 2.5, 'max_odds': 6.0, 'min_win_prob': 0.15, 'min_edge': 0.03, 'min_field': 5, 'max_field': 14}),
        ('ML $3-$8 7-12 field', {'min_odds': 3.0, 'max_odds': 8.0, 'min_win_prob': 0.12, 'min_edge': 0.04, 'min_field': 7, 'max_field': 12}),
        ('ML $3-$8 Strong Edge 6%', {'min_odds': 3.0, 'max_odds': 8.0, 'min_win_prob': 0.14, 'min_edge': 0.06, 'min_field': 5, 'max_field': 16}),
        ('ML $2-$8 Min 5% Edge', {'min_odds': 2.0, 'max_odds': 8.0, 'min_win_prob': 0.12, 'min_edge': 0.05, 'min_field': 5, 'max_field': 16}),
        ('ML $3-$6 Sweet Spot', {'min_odds': 3.0, 'max_odds': 6.0, 'min_win_prob': 0.16, 'min_edge': 0.04, 'min_field': 6, 'max_field': 12}),
        ('ML $4-$8 Value Focus', {'min_odds': 4.0, 'max_odds': 8.0, 'min_win_prob': 0.10, 'min_edge': 0.04, 'min_field': 6, 'max_field': 14}),
        ('Ensemble Optimized', {
            'min_odds': 2.0, 'max_odds': 8.0,
            'min_win_prob': optimized_threshold,
            'min_edge': 0.02,
            'min_field': 5, 'max_field': 16,
            'min_agreement': 0.85,
        }),
    ]
    return strategies


def evaluate_strategy(label, cfg, test_races, race_predictions):
    bets = []
    for race_key, runners in test_races.items():
        field_size = len(runners)
        if field_size < cfg['min_field'] or field_size > cfg['max_field']:
            continue

        preds = race_predictions.get(race_key, {})
        candidates = []

        for runner in runners:
            sp = float(runner.get('starting_price', 0) or 0)
            if sp < cfg['min_odds'] or sp > cfg['max_odds']:
                continue

            name = runner.get('horse_name', '')
            pred_info = preds.get(name, {})
            win_prob = pred_info.get('prob', 0) if isinstance(pred_info, dict) else pred_info
            agreement = pred_info.get('agreement', 1.0) if isinstance(pred_info, dict) else 1.0

            if win_prob < cfg['min_win_prob']:
                continue

            if 'min_agreement' in cfg and agreement < cfg['min_agreement']:
                continue

            implied = 1.0 / sp
            edge = win_prob - implied
            if edge < cfg['min_edge']:
                continue

            ev = (win_prob * sp) - 1
            candidates.append({
                'runner': runner,
                'win_prob': win_prob,
                'edge': edge,
                'ev': ev,
                'sp': sp,
                'agreement': agreement,
            })

        if candidates:
            best = max(candidates, key=lambda x: x['ev'])
            bets.append(best)

    total_bets = len(bets)
    wins = sum(1 for b in bets if b['runner'].get('won', 0) == 1)
    placed = sum(1 for b in bets if b['runner'].get('placed', 0) == 1)
    staked = total_bets * 100
    returned = sum(100 * b['sp'] for b in bets if b['runner'].get('won', 0) == 1)
    pnl = returned - staked
    roi = (pnl / staked * 100) if staked > 0 else 0
    sr = (wins / total_bets * 100) if total_bets > 0 else 0
    pr = (placed / total_bets * 100) if total_bets > 0 else 0
    avg_sp = float(np.mean([b['sp'] for b in bets])) if bets else 0
    avg_win_sp = float(np.mean([b['sp'] for b in bets if b['runner'].get('won', 0) == 1])) if wins > 0 else 0

    by_track = defaultdict(lambda: {'bets': 0, 'wins': 0, 'staked': 0, 'returned': 0})
    by_odds = defaultdict(lambda: {'bets': 0, 'wins': 0, 'staked': 0, 'returned': 0})
    by_month = defaultdict(lambda: {'bets': 0, 'wins': 0, 'staked': 0, 'returned': 0})

    for b in bets:
        r = b['runner']
        won = r.get('won', 0) == 1
        ret = 100 * b['sp'] if won else 0

        track = r.get('track', '')
        by_track[track]['bets'] += 1
        by_track[track]['staked'] += 100
        if won:
            by_track[track]['wins'] += 1
            by_track[track]['returned'] += ret

        sp_val = b['sp']
        if sp_val < 3: ob = '$2-$3'
        elif sp_val < 5: ob = '$3-$5'
        elif sp_val < 8: ob = '$5-$8'
        elif sp_val < 12: ob = '$8-$12'
        else: ob = '$12+'
        by_odds[ob]['bets'] += 1
        by_odds[ob]['staked'] += 100
        if won:
            by_odds[ob]['wins'] += 1
            by_odds[ob]['returned'] += ret

        month = r.get('race_date', '')[:7]
        by_month[month]['bets'] += 1
        by_month[month]['staked'] += 100
        if won:
            by_month[month]['wins'] += 1
            by_month[month]['returned'] += ret

    all_sps_list = [b['sp'] for b in bets]
    win_sps_list = [b['sp'] for b in bets if b['runner'].get('won', 0) == 1]

    return {
        'label': label,
        'config': cfg,
        'total_bets': total_bets,
        'total_wins': wins,
        'total_placed': placed,
        'all_sps': all_sps_list,
        'win_sps': win_sps_list,
        'strike_rate': round(sr, 1),
        'place_rate': round(pr, 1),
        'total_staked': staked,
        'total_returned': returned,
        'pnl': pnl,
        'roi': round(roi, 1),
        'avg_sp': round(avg_sp, 2),
        'avg_win_sp': round(avg_win_sp, 2),
        'by_track': {k: dict(v) for k, v in by_track.items()},
        'by_odds_range': {k: dict(v) for k, v in by_odds.items()},
        'by_month': {k: dict(v) for k, v in by_month.items()},
    }


def merge_strategy_results(results_list):
    merged = {}
    for results in results_list:
        for r in results:
            label = r['label']
            if label not in merged:
                merged[label] = {
                    'label': label,
                    'config': r['config'],
                    'total_bets': 0,
                    'total_wins': 0,
                    'total_placed': 0,
                    'total_staked': 0,
                    'total_returned': 0,
                    'all_sps': [],
                    'win_sps': [],
                    'by_track': defaultdict(lambda: {'bets': 0, 'wins': 0, 'staked': 0, 'returned': 0}),
                    'by_odds_range': defaultdict(lambda: {'bets': 0, 'wins': 0, 'staked': 0, 'returned': 0}),
                    'by_month': defaultdict(lambda: {'bets': 0, 'wins': 0, 'staked': 0, 'returned': 0}),
                }
            m = merged[label]
            m['total_bets'] += r['total_bets']
            m['total_wins'] += r['total_wins']
            m['total_placed'] += r.get('total_placed', 0)
            m['total_staked'] += r['total_staked']
            m['total_returned'] += r['total_returned']
            m['all_sps'].extend(r.get('all_sps', []))
            m['win_sps'].extend(r.get('win_sps', []))

            for track, s in r.get('by_track', {}).items():
                for k in ['bets', 'wins', 'staked', 'returned']:
                    m['by_track'][track][k] += s.get(k, 0)
            for ob, s in r.get('by_odds_range', {}).items():
                for k in ['bets', 'wins', 'staked', 'returned']:
                    m['by_odds_range'][ob][k] += s.get(k, 0)
            for mo, s in r.get('by_month', {}).items():
                for k in ['bets', 'wins', 'staked', 'returned']:
                    m['by_month'][mo][k] += s.get(k, 0)

    final = []
    for label, m in merged.items():
        pnl = m['total_returned'] - m['total_staked']
        roi = (pnl / m['total_staked'] * 100) if m['total_staked'] > 0 else 0
        sr = (m['total_wins'] / m['total_bets'] * 100) if m['total_bets'] > 0 else 0
        pr = (m['total_placed'] / m['total_bets'] * 100) if m['total_bets'] > 0 else 0
        avg_sp = float(np.mean(m['all_sps'])) if m['all_sps'] else 0
        avg_win_sp = float(np.mean(m['win_sps'])) if m['win_sps'] else 0

        final.append({
            'label': label,
            'config': m['config'],
            'total_bets': m['total_bets'],
            'total_wins': m['total_wins'],
            'strike_rate': round(sr, 1),
            'place_rate': round(pr, 1),
            'total_staked': m['total_staked'],
            'total_returned': m['total_returned'],
            'pnl': pnl,
            'roi': round(roi, 1),
            'avg_sp': round(avg_sp, 2),
            'avg_win_sp': round(avg_win_sp, 2),
            'by_track': {k: dict(v) for k, v in m['by_track'].items()},
            'by_odds_range': {k: dict(v) for k, v in m['by_odds_range'].items()},
            'by_month': {k: dict(v) for k, v in m['by_month'].items()},
        })

    return final


def walk_forward_backtest(data):
    all_dates = sorted(set(r['race_date'] for r in data))
    if len(all_dates) < 30:
        print("  ERROR: Not enough dates for walk-forward")
        return []

    earliest = datetime.strptime(all_dates[0], '%Y-%m-%d')
    latest = datetime.strptime(all_dates[-1], '%Y-%m-%d')

    print(f"\n  WALK-FORWARD ENSEMBLE BACKTEST")
    print(f"    Data range: {all_dates[0]} to {all_dates[-1]}")
    print(f"    Total records: {len(data)}")

    all_window_results = []
    test_dates_all = []
    window_count = 0

    for month_offset in range(7, 14):
        test_month_start = earliest + timedelta(days=month_offset * 30)
        train_end = test_month_start
        train_start = train_end - timedelta(days=180)
        test_end = test_month_start + timedelta(days=30)

        train_start_str = train_start.strftime('%Y-%m-%d')
        train_end_str = train_end.strftime('%Y-%m-%d')
        test_end_str = test_end.strftime('%Y-%m-%d')

        if train_end_str > all_dates[-1]:
            break

        train_data = [r for r in data if train_start_str <= r['race_date'] < train_end_str]
        test_data = [r for r in data if train_end_str <= r['race_date'] < test_end_str]

        if len(train_data) < 200 or len(test_data) < 20:
            print(f"\n  Window {month_offset}: Skipping (train={len(train_data)}, test={len(test_data)} - insufficient)")
            continue

        window_count += 1
        print(f"\n  Window {window_count} (month {month_offset}):")
        print(f"    Train: {train_start_str} to {train_end_str} ({len(train_data)} records)")
        print(f"    Test:  {train_end_str} to {test_end_str} ({len(test_data)} records)")

        history_data = [r for r in data if r['race_date'] < train_end_str]
        horse_history = build_horse_history(history_data)

        print("    Building stats...")
        stats = build_stats(train_data)

        print("    Training ensemble...")
        result = train_ensemble(train_data, stats, horse_history)
        models, scaler = result[0], result[1]

        if models is None:
            print("    Skipping window - model training failed")
            continue

        print("    Optimizing threshold...")
        optimized_threshold = optimize_threshold(train_data, stats, models, scaler, horse_history)

        print("    Computing predictions on test data...")
        test_races = group_by_race(test_data)
        race_predictions = {}
        for race_key, runners in test_races.items():
            race_date = runners[0].get('race_date', '')
            preds = predict_ensemble(runners, stats, models, scaler, horse_history, race_date)
            race_predictions[race_key] = preds

        print(f"    Predictions for {len(test_races)} test races")

        strategies = get_strategies(optimized_threshold)
        window_results = []
        for label, cfg in strategies:
            r = evaluate_strategy(label, cfg, test_races, race_predictions)
            window_results.append(r)

        all_window_results.append(window_results)

        for r in test_data:
            test_dates_all.append(r['race_date'])

        profitable = [r for r in window_results if r['roi'] > 0 and r['total_bets'] >= 5]
        if profitable:
            best_w = max(profitable, key=lambda x: x['roi'])
            print(f"    Best window strategy: {best_w['label']} (ROI: {best_w['roi']:+.1f}%, {best_w['total_bets']} bets)")
        else:
            print(f"    No profitable strategies this window")

    if not all_window_results:
        print("  ERROR: No valid windows produced")
        return []

    print(f"\n  Aggregating results across {len(all_window_results)} windows...")
    merged = merge_strategy_results(all_window_results)

    test_start = min(test_dates_all) if test_dates_all else ''
    test_end_final = max(test_dates_all) if test_dates_all else ''

    for r in merged:
        r['test_start'] = test_start
        r['test_end'] = test_end_final

    return merged


def print_result_detail(r):
    print(f"\n  {'='*60}")
    print(f"  {r['label']}")
    print(f"  {'='*60}")
    print(f"    Bets: {r['total_bets']} | Winners: {r['total_wins']} ({r['strike_rate']}%)")
    print(f"    Staked: ${r['total_staked']:,.0f} | Returned: ${r['total_returned']:,.0f}")
    print(f"    P&L: ${r['pnl']:+,.0f} | ROI: {r['roi']:+.1f}%")
    print(f"    Avg SP: ${r['avg_sp']:.2f} | Avg Winner SP: ${r['avg_win_sp']:.2f}")

    print(f"\n    BY TRACK:")
    for track, s in sorted(r.get('by_track', {}).items(), key=lambda x: -x[1].get('bets', 0)):
        t_roi = ((s['returned'] - s['staked']) / s['staked'] * 100) if s.get('staked', 0) > 0 else 0
        t_sr = (s['wins'] / s['bets'] * 100) if s.get('bets', 0) > 0 else 0
        print(f"      {track:30s} | {s['bets']:3d} bets | {s['wins']:2d}W ({t_sr:5.1f}%) | ROI: {t_roi:+6.1f}%")

    print(f"\n    BY ODDS:")
    for ob in ['$2-$3', '$3-$5', '$5-$8', '$8-$12', '$12+']:
        if ob in r.get('by_odds_range', {}):
            s = r['by_odds_range'][ob]
            o_roi = ((s['returned'] - s['staked']) / s['staked'] * 100) if s.get('staked', 0) > 0 else 0
            o_sr = (s['wins'] / s['bets'] * 100) if s.get('bets', 0) > 0 else 0
            print(f"      {ob:12s} | {s['bets']:3d} bets | {s['wins']:2d}W ({o_sr:5.1f}%) | ROI: {o_roi:+6.1f}%")

    print(f"\n    BY MONTH:")
    for month in sorted(r.get('by_month', {}).keys()):
        s = r['by_month'][month]
        m_roi = ((s['returned'] - s['staked']) / s['staked'] * 100) if s.get('staked', 0) > 0 else 0
        m_sr = (s['wins'] / s['bets'] * 100) if s.get('bets', 0) > 0 else 0
        print(f"      {month:12s} | {s['bets']:3d} bets | {s['wins']:2d}W ({m_sr:5.1f}%) | ROI: {m_roi:+6.1f}%")


def main():
    if not DB_URL:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("  WIZBET - WALK-FORWARD ENSEMBLE BACKTESTING SYSTEM")
    print("=" * 70)
    print("\n  Methodology:")
    print("    - Walk-forward rolling windows (6-month train, 1-month test)")
    print("    - Ensemble: GBM + XGBoost + Logistic Regression")
    print("    - Horse history features (form, class, wet track)")
    print("    - Track-specific barrier-distance bias features")
    print("    - Confidence threshold optimization per window")
    print("    - ROI on Starting Price (SP) only | Flat $100 stakes")
    print("    - NO data leakage")

    print("\n  Loading data...")
    data = get_training_data()
    print(f"  Total records: {len(data)}")

    if not data:
        print("  ERROR: No data found")
        sys.exit(1)

    all_results = walk_forward_backtest(data)

    if not all_results:
        print("  ERROR: No results produced")
        sys.exit(1)

    print(f"\n\n{'='*70}")
    print(f"  STRATEGY COMPARISON (Walk-Forward Aggregated)")
    print(f"{'='*70}")
    print(f"\n  {'Strategy':<40s} | {'Bets':>5s} | {'Win%':>6s} | {'ROI':>8s} | {'P&L':>10s}")
    print(f"  {'-'*40}-+-{'-'*5}-+-{'-'*6}-+-{'-'*8}-+-{'-'*10}")

    for r in sorted(all_results, key=lambda x: -x['roi']):
        print(f"  {r['label']:<40s} | {r['total_bets']:>5d} | {r['strike_rate']:>5.1f}% | {r['roi']:>+7.1f}% | ${r['pnl']:>+9,.0f}")

    top3 = sorted(all_results, key=lambda x: -x['roi'])[:3]
    for r in top3:
        print_result_detail(r)

    best = top3[0] if top3 else None

    if best:
        print(f"\n\n  BEST STRATEGY: {best['label']}")
        print(f"    ROI: {best['roi']:+.1f}% | Win: {best['strike_rate']}% | Bets: {best['total_bets']} | P&L: ${best['pnl']:+,.0f}")

        output = {
            'available': True,
            'backtest_date': datetime.now().isoformat(),
            'methodology': 'Walk-forward ensemble backtest with 6-month rolling windows, no data leakage',
            'staking': 'Flat $100 per bet',
            'test_period': f"{best.get('test_start', '')} to {best.get('test_end', '')}",
            'best_strategy': {
                'label': best['label'],
                'config': best['config'],
                'roi': best['roi'],
                'strike_rate': best['strike_rate'],
                'place_rate': best['place_rate'],
                'total_bets': best['total_bets'],
                'total_wins': best['total_wins'],
                'pnl': best['pnl'],
                'avg_sp': best['avg_sp'],
                'avg_win_sp': best['avg_win_sp'],
            },
            'all_strategies': [{
                'label': r['label'],
                'roi': r['roi'],
                'strike_rate': r['strike_rate'],
                'total_bets': r['total_bets'],
                'pnl': r['pnl'],
                'avg_sp': r['avg_sp'],
            } for r in sorted(all_results, key=lambda x: -x['roi'])],
            'best_by_track': best.get('by_track', {}),
            'best_by_month': best.get('by_month', {}),
            'best_by_odds': best.get('by_odds_range', {}),
        }

        os.makedirs('backtest_results', exist_ok=True)
        with open('backtest_results/backtest_results.json', 'w') as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\n  Results saved to: backtest_results/backtest_results.json")

    print()


if __name__ == '__main__':
    main()
