#!/usr/bin/env python3
"""One-off historical analysis script for race performance by track + class."""

import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor(cursor_factory=RealDictCursor)

print("=" * 90)
print("PERFORMANCE BY TRACK + RACE CLASS (last 3 months)")
print("=" * 90)
cur.execute("""
    SELECT
        td.track,
        COALESCE(NULLIF(td.race_class, ''), 'Unknown') AS race_class,
        COUNT(*) AS total,
        SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) AS winners,
        SUM(CASE WHEN td.placed = 1 THEN 1 ELSE 0 END) AS placers,
        ROUND(AVG(td.market_odds)::numeric, 2) AS avg_odds,
        ROUND(AVG(td.predicted_win_prob)::numeric, 2) AS avg_pred_prob,
        ROUND((100.0 * SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS strike_rate,
        ROUND((100.0 * SUM(CASE WHEN td.placed = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS place_rate,
        ROUND(SUM(
            CASE WHEN td.won = 1 AND COALESCE(td.starting_price, 0) > 0
                 THEN (100.0 * td.starting_price) - 100.0
                 ELSE -100.0
            END
        )::numeric, 2) AS profit_loss
    FROM training_data td
    WHERE LOWER(td.track) IN ('flemington', 'caulfield', 'rosehill', 'randwick')
      AND td.race_date::date >= (CURRENT_DATE - INTERVAL '90 days')::date
      AND td.actual_position IS NOT NULL
    GROUP BY td.track, COALESCE(NULLIF(td.race_class, ''), 'Unknown')
    ORDER BY td.track, total DESC
""")
rows = cur.fetchall()
if rows:
    print(f"{'Track':<14} {'Class':<12} {'Total':>5} {'Win':>4} {'Place':>5} {'SR%':>6} {'PR%':>6} {'AvgOdds':>8} {'AvgProb':>8} {'P/L':>10}")
    print("-" * 90)
    for r in rows:
        print(f"{r['track']:<14} {r['race_class']:<12} {r['total']:>5} {r['winners']:>4} {r['placers']:>5} {r['strike_rate']:>6} {r['place_rate']:>6} {r['avg_odds']:>8} {r['avg_pred_prob']:>8} {r['profit_loss']:>10}")
else:
    print("No data found.")

print("\n" + "=" * 70)
print("TRACK SUMMARY (last 3 months)")
print("=" * 70)
cur.execute("""
    SELECT
        td.track,
        COUNT(*) AS total,
        SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) AS winners,
        ROUND((100.0 * SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS strike_rate,
        ROUND((100.0 * SUM(CASE WHEN td.placed = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS place_rate,
        ROUND(AVG(td.market_odds)::numeric, 2) AS avg_odds,
        ROUND(SUM(
            CASE WHEN td.won = 1 AND COALESCE(td.starting_price, 0) > 0
                 THEN (100.0 * td.starting_price) - 100.0
                 ELSE -100.0
            END
        )::numeric, 2) AS profit_loss,
        ROUND((
            SUM(CASE WHEN td.won = 1 AND COALESCE(td.starting_price, 0) > 0
                     THEN (100.0 * td.starting_price) - 100.0
                     ELSE -100.0 END)
            / NULLIF(COUNT(*) * 100.0, 0) * 100.0
        )::numeric, 1) AS roi
    FROM training_data td
    WHERE LOWER(td.track) IN ('flemington', 'caulfield', 'rosehill', 'randwick')
      AND td.race_date::date >= (CURRENT_DATE - INTERVAL '90 days')::date
      AND td.actual_position IS NOT NULL
    GROUP BY td.track
    ORDER BY total DESC
""")
rows = cur.fetchall()
if rows:
    print(f"{'Track':<14} {'Total':>5} {'Win':>4} {'SR%':>6} {'PR%':>6} {'AvgOdds':>8} {'P/L':>10} {'ROI%':>7}")
    print("-" * 70)
    for r in rows:
        print(f"{r['track']:<14} {r['total']:>5} {r['winners']:>4} {r['strike_rate']:>6} {r['place_rate']:>6} {r['avg_odds']:>8} {r['profit_loss']:>10} {r['roi']:>7}")

print("\n" + "=" * 70)
print("PERFORMANCE BY ODDS RANGE (last 3 months, target tracks)")
print("=" * 70)
cur.execute("""
    SELECT
        CASE
            WHEN td.market_odds BETWEEN 1.01 AND 3.0 THEN '$1-3'
            WHEN td.market_odds BETWEEN 3.01 AND 5.0 THEN '$3-5'
            WHEN td.market_odds BETWEEN 5.01 AND 8.0 THEN '$5-8'
            WHEN td.market_odds BETWEEN 8.01 AND 13.0 THEN '$8-13'
            WHEN td.market_odds > 13.0 THEN '$13+'
            ELSE 'Unknown'
        END AS odds_range,
        COUNT(*) AS total,
        SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) AS winners,
        ROUND((100.0 * SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS strike_rate,
        ROUND(AVG(td.market_odds)::numeric, 2) AS avg_odds,
        ROUND(AVG(td.predicted_win_prob)::numeric, 2) AS avg_pred,
        ROUND(SUM(
            CASE WHEN td.won = 1 AND COALESCE(td.starting_price, 0) > 0
                 THEN (100.0 * td.starting_price) - 100.0
                 ELSE -100.0
            END
        )::numeric, 2) AS profit_loss,
        ROUND((
            SUM(CASE WHEN td.won = 1 AND COALESCE(td.starting_price, 0) > 0
                     THEN (100.0 * td.starting_price) - 100.0
                     ELSE -100.0 END)
            / NULLIF(COUNT(*) * 100.0, 0) * 100.0
        )::numeric, 1) AS roi
    FROM training_data td
    WHERE LOWER(td.track) IN ('flemington', 'caulfield', 'rosehill', 'randwick')
      AND td.race_date::date >= (CURRENT_DATE - INTERVAL '90 days')::date
      AND td.actual_position IS NOT NULL
      AND td.market_odds > 0
    GROUP BY odds_range
    ORDER BY MIN(td.market_odds)
""")
rows = cur.fetchall()
if rows:
    print(f"{'Range':<10} {'Total':>5} {'Win':>4} {'SR%':>6} {'AvgOdds':>8} {'AvgPred':>8} {'P/L':>10} {'ROI%':>7}")
    print("-" * 70)
    for r in rows:
        print(f"{r['odds_range']:<10} {r['total']:>5} {r['winners']:>4} {r['strike_rate']:>6} {r['avg_odds']:>8} {r['avg_pred']:>8} {r['profit_loss']:>10} {r['roi']:>7}")

print("\n" + "=" * 70)
print("CALIBRATION CHECK: Predicted Prob vs Actual Win Rate (target tracks)")
print("=" * 70)
cur.execute("""
    SELECT
        CASE
            WHEN td.predicted_win_prob < 5 THEN '0-5%'
            WHEN td.predicted_win_prob < 10 THEN '5-10%'
            WHEN td.predicted_win_prob < 15 THEN '10-15%'
            WHEN td.predicted_win_prob < 20 THEN '15-20%'
            WHEN td.predicted_win_prob < 30 THEN '20-30%'
            WHEN td.predicted_win_prob >= 30 THEN '30%+'
            ELSE 'Unknown'
        END AS prob_bucket,
        COUNT(*) AS total,
        SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) AS winners,
        ROUND((100.0 * SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS actual_win_rate,
        ROUND(AVG(td.predicted_win_prob)::numeric, 1) AS avg_predicted,
        ROUND(AVG(td.market_odds)::numeric, 2) AS avg_odds
    FROM training_data td
    WHERE LOWER(td.track) IN ('flemington', 'caulfield', 'rosehill', 'randwick')
      AND td.race_date::date >= (CURRENT_DATE - INTERVAL '90 days')::date
      AND td.actual_position IS NOT NULL
    GROUP BY prob_bucket
    ORDER BY MIN(td.predicted_win_prob)
""")
rows = cur.fetchall()
if rows:
    print(f"{'Bucket':<10} {'Total':>5} {'Win':>4} {'ActualWR%':>10} {'AvgPred%':>9} {'AvgOdds':>8}")
    print("-" * 70)
    for r in rows:
        print(f"{r['prob_bucket']:<10} {r['total']:>5} {r['winners']:>4} {r['actual_win_rate']:>10} {r['avg_predicted']:>9} {r['avg_odds']:>8}")

print("\n" + "=" * 70)
print("MARCH 7, 2026 -- ALL SELECTIONS WITH RESULTS")
print("=" * 70)
cur.execute("""
    SELECT
        td.track,
        td.race_number,
        COALESCE(NULLIF(td.race_class, ''), '?') AS race_class,
        td.horse_name,
        td.actual_position,
        ROUND(td.predicted_win_prob::numeric, 1) AS pred_prob,
        ROUND(td.market_odds::numeric, 2) AS mkt_odds,
        ROUND(COALESCE(td.starting_price, 0)::numeric, 2) AS sp,
        CASE WHEN td.won = 1 THEN 'WIN' ELSE '' END AS result
    FROM training_data td
    WHERE td.race_date = '2026-03-07'
    ORDER BY td.track, td.race_number, td.actual_position
""")
rows = cur.fetchall()
if rows:
    print(f"{'Track':<14} {'R#':>2} {'Class':<10} {'Horse':<25} {'Pos':>3} {'Pred%':>6} {'MktOdds':>8} {'SP':>6} {'Result':<5}")
    print("-" * 90)
    for r in rows:
        print(f"{r['track']:<14} {r['race_number']:>2} {r['race_class']:<10} {r['horse_name']:<25} {r['actual_position']:>3} {r['pred_prob']:>6} {r['mkt_odds']:>8} {r['sp']:>6} {r['result']:<5}")
else:
    print("No March 7 data found in training_data.")

print("\n" + "=" * 70)
print("ALL TRACKS SUMMARY (last 3 months, top 15 by volume)")
print("=" * 70)
cur.execute("""
    SELECT
        td.track,
        COUNT(*) AS total,
        SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) AS winners,
        ROUND((100.0 * SUM(CASE WHEN td.won = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS strike_rate,
        ROUND((100.0 * SUM(CASE WHEN td.placed = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) AS place_rate,
        ROUND(AVG(td.market_odds)::numeric, 2) AS avg_odds,
        ROUND(SUM(
            CASE WHEN td.won = 1 AND COALESCE(td.starting_price, 0) > 0
                 THEN (100.0 * td.starting_price) - 100.0
                 ELSE -100.0
            END
        )::numeric, 2) AS profit_loss,
        ROUND((
            SUM(CASE WHEN td.won = 1 AND COALESCE(td.starting_price, 0) > 0
                     THEN (100.0 * td.starting_price) - 100.0
                     ELSE -100.0 END)
            / NULLIF(COUNT(*) * 100.0, 0) * 100.0
        )::numeric, 1) AS roi
    FROM training_data td
    WHERE td.race_date::date >= (CURRENT_DATE - INTERVAL '90 days')::date
      AND td.actual_position IS NOT NULL
    GROUP BY td.track
    ORDER BY total DESC
    LIMIT 15
""")
rows = cur.fetchall()
if rows:
    print(f"{'Track':<20} {'Total':>5} {'Win':>4} {'SR%':>6} {'PR%':>6} {'AvgOdds':>8} {'P/L':>10} {'ROI%':>7}")
    print("-" * 70)
    for r in rows:
        print(f"{r['track']:<20} {r['total']:>5} {r['winners']:>4} {r['strike_rate']:>6} {r['place_rate']:>6} {r['avg_odds']:>8} {r['profit_loss']:>10} {r['roi']:>7}")

cur.close()
conn.close()
print("\nDone.")
