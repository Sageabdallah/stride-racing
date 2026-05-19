#!/usr/bin/env python3
"""
ML model training: trains the XGBoost/LightGBM/CatBoost ensemble from training_data table.
Works with both historical imported data and archived selections.
"""
import os
import sys
import json
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))


def extract_distance_from_text(distance_str):
    """Extract numeric distance from string like '1400m' or '1400'."""
    if not distance_str:
        return 1400
    if isinstance(distance_str, (int, float)):
        return float(distance_str)
    try:
        clean = str(distance_str).lower().replace('m', '').replace(',', '').strip()
        return float(clean)
    except:
        return 1400


def calculate_barrier_penalty(barrier, distance, is_sprint=None):
    """Calculate barrier penalty based on draw position and distance."""
    if not barrier or barrier <= 0:
        return 0
    
    if is_sprint is None:
        is_sprint = distance < 1200
    
    if is_sprint:
        if barrier > 12:
            return 0.15
        elif barrier > 10:
            return 0.08
        elif barrier > 8:
            return 0.03
    else:
        if barrier > 14:
            return 0.08
        elif barrier > 12:
            return 0.04
    
    return 0


def calculate_weight_penalty(weight):
    """Calculate penalty for heavy weights."""
    if not weight or weight <= 0:
        return 0
    # Penalize weights above 55kg
    return max(0, (weight - 55) * 0.02)


def calculate_form_score(form_str):
    """Calculate weighted form score from form string."""
    if not form_str:
        return 0
    
    form = str(form_str).replace('-', '').replace(' ', '')
    score = 0
    weights = [1.0, 0.8, 0.6, 0.4, 0.2]
    
    for i, char in enumerate(form[:5]):
        weight = weights[i] if i < len(weights) else 0.1
        if char == '1':
            score += 10 * weight
        elif char == '2':
            score += 7 * weight
        elif char == '3':
            score += 5 * weight
        elif char == '4':
            score += 3 * weight
        elif char == '5':
            score += 2 * weight
        elif char.isdigit():
            score += 1 * weight
    
    return score


def main():
    try:
        from ml_model import RacingMLModel
        
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            print(json.dumps({
                'success': False,
                'error': 'DATABASE_URL not set'
            }))
            return
        
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)
        
        # First, try to get comprehensive data from training_data with selections
        query_with_selections = """
        SELECT 
            t.barrier,
            t.weight,
            t.market_odds,
            t.starting_price,
            t.won,
            t.placed,
            t.actual_position,
            t.distance,
            t.predicted_win_prob,
            t.predicted_place_prob,
            t.expected_value,
            t.race_class,
            t.going,
            t.jockey,
            t.trainer,
            COALESCE(s.distance_strike_rate, 0) as distance_strike_rate,
            COALESCE(s.course_strike_rate, 0) as course_strike_rate,
            COALESCE(s.weighted_form_score, 0) as weighted_form_score,
            CASE WHEN s.is_first_up THEN 1 ELSE 0 END as is_first_up,
            CASE WHEN s.is_second_up THEN 1 ELSE 0 END as is_second_up,
            COALESCE(s.days_since_run, 30) as days_since_run,
            COALESCE(s.jockey_trainer_strike_rate, 0) as jockey_trainer_strike_rate,
            CASE WHEN s.is_winning_combo THEN 1 ELSE 0 END as is_winning_combo,
            COALESCE(s.ml_adjustment, 1) as ml_adjustment,
            COALESCE(s.ground_suitability, 1) as ground_suitability
        FROM training_data t
        LEFT JOIN selections s ON t.selection_id = s.id
        WHERE (t.actual_position IS NOT NULL OR t.won IS NOT NULL)
        """
        
        # Simpler query for pure historical data (no selections join needed)
        query_historical = """
        SELECT 
            barrier,
            weight,
            market_odds,
            starting_price,
            won,
            placed,
            actual_position,
            distance,
            predicted_win_prob,
            predicted_place_prob,
            expected_value,
            race_class,
            going,
            jockey,
            trainer
        FROM training_data
        WHERE (actual_position IS NOT NULL OR won IS NOT NULL)
        """
        
        with engine.connect() as conn:
            try:
                df = pd.read_sql(query_with_selections, conn)
            except:
                df = pd.read_sql(query_historical, conn)
        
        if len(df) < 50:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) as count FROM training_data"))
                total = result.fetchone()[0]
            
            print(json.dumps({
                'success': False,
                'error': f'Insufficient training data with results: {len(df)} records (need 50+). Total records: {total}. Import historical data or wait for more race results.',
                'total_records': int(total),
                'usable_records': len(df)
            }))
            return
        
        winners = int(df['won'].sum()) if 'won' in df.columns else 0
        if winners < 5:
            print(json.dumps({
                'success': False,
                'error': f'Insufficient winners in training data: {winners} (need 5+)',
                'total_records': len(df),
                'winners': winners
            }))
            return
        
        print(f"Processing {len(df)} training records with {winners} winners...", file=sys.stderr)
        
        if 'distance' in df.columns:
            df['distance_numeric'] = df['distance'].apply(extract_distance_from_text)
        else:
            df['distance_numeric'] = 1400
        
        df['is_sprint'] = (df['distance_numeric'] < 1200).astype(int)
        
        if 'barrier_penalty' not in df.columns:
            df['barrier_penalty'] = df.apply(
                lambda row: calculate_barrier_penalty(
                    row.get('barrier', 0),
                    row.get('distance_numeric', 1400)
                ), axis=1
            )
        
        if 'weight_penalty' not in df.columns:
            df['weight_penalty'] = df['weight'].apply(calculate_weight_penalty)
        
        # Market-implied probability
        df['market_implied_prob'] = np.where(
            df['market_odds'] > 1,
            100 / df['market_odds'],
            10
        )
        
        feature_columns = [
            'barrier', 'weight', 'market_odds', 'market_implied_prob',
            'distance_numeric', 'is_sprint', 'barrier_penalty', 'weight_penalty'
        ]
        
        if 'predicted_win_prob' in df.columns:
            feature_columns.append('predicted_win_prob')
        if 'expected_value' in df.columns:
            feature_columns.append('expected_value')
        
        selections_features = [
            'distance_strike_rate', 'course_strike_rate', 'weighted_form_score',
            'is_first_up', 'is_second_up', 'days_since_run',
            'jockey_trainer_strike_rate', 'is_winning_combo',
            'ml_adjustment', 'ground_suitability'
        ]
        
        for col in selections_features:
            if col in df.columns:
                feature_columns.append(col)
        
        feature_columns = list(dict.fromkeys(feature_columns))
        
        for col in feature_columns:
            if col not in df.columns:
                df[col] = 0
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        df['won'] = pd.to_numeric(df['won'], errors='coerce').fillna(0).astype(int)
        df['placed'] = pd.to_numeric(df['placed'], errors='coerce').fillna(0).astype(int)
        
        train_df = df[feature_columns + ['won', 'placed']].copy()
        train_df = train_df.fillna(0)
        
        print(f"Training with {len(feature_columns)} features: {feature_columns}", file=sys.stderr)
        print(f"Sample data shape: {train_df.shape}", file=sys.stderr)
        
        model = RacingMLModel()
        result = model.train(train_df, target_col='won', use_optuna=True, n_trials=30)
        
        result['training_stats'] = {
            'total_records': len(df),
            'winners': winners,
            'placed': int(df['placed'].sum()),
            'feature_count': len(feature_columns),
            'features': feature_columns
        }
        
        print(json.dumps(result))
        
    except Exception as e:
        import traceback
        print(json.dumps({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }))


if __name__ == '__main__':
    main()
