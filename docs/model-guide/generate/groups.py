"""Assign every non-zero factor to exactly one theme, then prove the assignment
is complete (no factor missed, none double-counted, shares sum to the total)."""
import json

facts = json.load(open('facts.json'))
imp = dict(facts['ranked_nonzero'])

GROUPS = {
 "The betting market": ["market_odds"],
 "How the race will be run (pace and shape)": [
     "closer_advantage","pace_pressure_score","leader_advantage","td_pace_bias",
     "td_closing_speed_bias","rsi"],
 "Barrier and field size": [
     "barrier_draw","field_size","field_size_context","trip_cost_seconds",
     "barrier_relevance_score"],
 "Fitness and the training cycle": [
     "trainer_trial_pattern","days_since_run","trial_recency","trial_quality_score",
     "trial_x_experience","trial_count_60d","second_up_win_rate","first_up_win_rate",
     "bounce_severity","runs_since_peak","fitness_x_distance","is_bounce_candidate",
     "campaign_run_number","is_first_up","campaign_run_x_fitness"],
 "Recent form and momentum": [
     "weighted_form_score","speed_rating_trajectory","improvement_score",
     "sectional_trajectory","consistency_score","form_direction_slope",
     "is_improving","is_in_form_cycle"],
 "Sectional (in-running) speed data": [
     "z_800m","z_600m","lambda_decay","svi","z_400m","z_200m",
     "sectional_rank_at_distance","sectional_x_going","has_sectional_data"],
 "Track record at this course and trip": [
     "td_upset_rate","course_strike_rate","distance_strike_rate"],
 "Weight carried": ["weight_kg","weight_change"],
 "Jockey and trainer": [
     "jockey_booking_change","jockey_trainer_strike_rate","is_winning_combo"],
 "Race conditions and class": [
     "distance","market_efficiency_flag","class_level","distance_direction_flag"],
}

assigned = [f for fs in GROUPS.values() for f in fs]
missing = sorted(set(imp) - set(assigned))
extra   = sorted(set(assigned) - set(imp))
dupes   = sorted({f for f in assigned if assigned.count(f) > 1})
print("unassigned factors :", missing or "none")
print("unknown names      :", extra or "none")
print("duplicated         :", dupes or "none")
print("assigned count     :", len(assigned), "of", len(imp))

rows = sorted(((g, sum(imp[f] for f in fs)) for g, fs in GROUPS.items()),
              key=lambda r: -r[1])
total = sum(v for _, v in rows)
print(f"\n{'THEME':46s} {'SHARE':>8s}  factors")
for g, v in rows:
    print(f"{g:46s} {v*100:7.2f}%  {len(GROUPS[g])}")
print(f"{'TOTAL':46s} {total*100:7.2f}%  {len(assigned)}")
json.dump({"groups": GROUPS, "group_shares": {g: v for g, v in rows}},
          open('groups.json','w'), indent=1)
