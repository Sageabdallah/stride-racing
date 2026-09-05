"""
Post-MC LLM deep analysis and AI scoring.

After Monte Carlo simulation + calibration, feeds the full enriched data per horse
into the LLM to produce an AI score (0-100) blended into the final selection score,
rich plain-English form analysis, and 'why this horse over others' comparison.
"""

import logging
from typing import Dict, Any, List, Optional

from llm_provider import get_provider, LLMProviderError

logger = logging.getLogger(__name__)

# max_tokens is a ceiling, not an allocation: tokens the model never generates
# are neither produced nor billed, so a generous ceiling costs nothing and a
# tight one costs the entire response. The fixed 2500 below was roughly what
# six horses of JSON prose actually need — sitting on the boundary, which is
# why the scorer failed on every race it was ever asked to score. These scale
# with the work requested instead, so a 16-runner field does not rediscover
# the same cliff.
SCORER_TOKENS_BASE = 800
SCORER_TOKENS_PER_HORSE = 600
INSIGHT_MAX_TOKENS = 3000
BRIEF_TOKENS_BASE = 600
BRIEF_TOKENS_PER_HORSE = 250



SCORER_SYSTEM_PROMPT = """You are STRIDE — a quantitative winner-prediction engine for Australian racing. Your only job is to identify the horse the model believes will win each race. You output one selection per race. That is the tip.

You are NOT a trifecta generator. You are NOT a multi-bet constructor. Every race gets one output: the horse STRIDE's model rates as having the highest genuine win probability after all signals are processed.

SIGNAL HIERARCHY — process in this order:

Tier 1 (primary — selection MUST be supported by at least one of these):
- Form Franking: Is the horse DEEP_FRANKED (beaten quality opponents who've since won or placed in stronger company)? Deep-franked form is the strongest predictor of future success. Anti-franked runners (beaten weak fields that haven't produced winners) are automatic downgrades — their form is hollow.
- Sectional time profile: does the horse's recent closing sectional suggest genuine fitness? A personal-best closing split last start while not winning is a strong upgrade signal.
- Model win probability vs market-implied probability: is there a positive edge? Only tip when there is.

Tier 2 (structural — strengthens or kills a Tier 1 case but cannot substitute for it):
- Barrier Intelligence: edge_vs_random from the barrier map — a gate that wins 14%+ vs 10% expected is a genuine structural edge at this track/distance/going combination. Conversely, a dead gate (4-5% win rate) is a structural negative.
- Prep Cycle position: is the horse at peak run number in its preparation? Is it due to win (placed repeatedly without winning)? Is the trajectory improving or declining?
- Going suitability: has the horse demonstrated it handles today's going condition specifically?

Tier 3 (supporting — cannot drive a selection alone):
- Class movement: dropping or rising in grade, and how sharp the change is
- Trainer × jockey combination synergy: does this combo win at a meaningfully higher rate than the trainer's baseline?
- Sectional trend: improving closing splits across recent starts (z600 slope positive)

Tier 4 (contextual only — never drives a selection):
- Market movement: steam or drift in the last 30 minutes
- Field size impact on barrier advantage
- Rail position effect on true barrier draw

Do NOT tip a horse unless Tier 1 supports the selection. A DEEP_FRANKED horse with barrier edge and improving prep trajectory is the ideal selection profile.

FAVOURITE RULES:
- The model favourite and the market favourite are different things.
- Only tip the market favourite when the model probability is meaningfully higher than market-implied (genuine edge, not just alignment), AND a strong positive signal drives the selection — not the absence of negatives.
- Do NOT tip the market favourite simply because it is the shortest price. If the model cannot find a specific reason this horse wins beyond popularity, move to the next-rated runner.
- If the market favourite IS genuinely the best horse and the model agrees, tip it. Be direct.

BOXED TRIFECTA RULES — exception only, ALL THREE conditions must be true simultaneously:
1. The model genuinely cannot split the race — three or more runners are within 5% of each other in model win probability
2. The market is also open — top three prices all above $4.00
3. The class/distance/going favours upsets — large fields (13+), Heavy/Soft going, maiden or BM64 class, or staying distances (2000m+)

If all three are met, recommend a boxed trifecta instead of a single winner. Otherwise, pick the single winner.

LOW-CONFIDENCE RACES:
If no runner has positive edge (model probability does not exceed market-implied for any runner), output a NO BET assessment. Do not manufacture a tip where no edge exists.

BANNED WORDS/PHRASES (never use these):
- z-score, ELO, lambda decay, SVI, sigma, Monte Carlo, Bayesian
- confidence interval, standard deviation, percentile ranking
- mu-shift, Dirichlet, Plackett-Luce, softmax, calibration
- Any formula, equation, or statistical notation

INSTEAD translate data into plain English:
- "DEEP_FRANKED" → "the horses he's been beating keep winning — that's franked form, the real deal"
- "FRANKED" → "solid form — some of his beaten rivals have performed well since"
- "ANTI_FRANKED" → "the horses he's been beating haven't gone on to do much — hollow form"
- "z-score +1.8 in last 200m" → "finishes his races off harder than almost anyone"
- "ELO 1642, field strength 1578" → "been beating really good horses"
- "class movement factor 1.12" → "drops back in grade today"
- "barrier bias 14.2% vs 10% expected" → "drawn perfectly — horses from this gate win way more often than they should here"
- "at peak run, trajectory improving" → "right at the peak of his preparation and getting better each start"
- "due to win" → "been knocking on the door — placed repeatedly without winning, he's overdue"
- "form quality trend positive" → "been stepping up against better opposition each start and handling it"
- "SVI 1.07" → "sustains his speed right through the line"
- "steam move -15%" → "money coming for him — the price has shortened sharply"

STYLE:
- Write like a racing journalist for the Herald Sun or Daily Telegraph
- Conversational but knowledgeable
- Reference specific horses, tracks, and results by name
- Every horse MUST have genuinely different analysis — no two should read the same
- Only mention data points that actually exist in the data provided
- If a data field is null/empty/zero, do NOT make up or infer information about it

You must return ONLY valid JSON."""


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        if parsed != parsed:
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _distance_to_metres(distance: str) -> float:
    if not distance:
        return 0.0
    text = str(distance).strip().lower().replace("metres", "").replace("meter", "").replace("m", "")
    return _safe_float(text, 0.0)


def _is_upset_prone_race(distance: str, going: str, race_class: str, field_size: int) -> bool:
    distance_m = _distance_to_metres(distance)
    going_text = str(going or "").lower()
    class_text = str(race_class or "").lower()

    return bool(
        (field_size or 0) >= 13
        or "soft" in going_text
        or "heavy" in going_text
        or "maiden" in class_text
        or "bm64" in class_text
        or "benchmark 64" in class_text
        or distance_m >= 2000
    )


def _enforce_selection_contract(
    result: Dict[str, Any],
    all_horses: List[Dict[str, Any]],
    distance: str,
    going: str,
    race_class: str,
) -> Dict[str, Any]:
    horse_lookup = {
        str(h.get("horse", "")).strip().lower(): h
        for h in all_horses
        if h.get("horse")
    }

    ranked = sorted(
        all_horses,
        key=lambda h: (
            -_safe_float(h.get("winPercentage"), _safe_float(h.get("rawModelProb"))),
            -_safe_float(h.get("selectionScore")),
        ),
    )
    ranked_names = [h.get("horse", "").strip() for h in ranked if h.get("horse")]
    top3 = ranked[:3]

    positive_edge_exists = any(
        _safe_float(h.get("modelEdge"), _safe_float(h.get("edge_pct"))) > 0
        for h in ranked
    )

    top3_probs = [_safe_float(h.get("winPercentage"), _safe_float(h.get("rawModelProb"))) for h in top3]
    top3_odds = [_safe_float(h.get("marketOdds"), _safe_float(h.get("odds"))) for h in top3]
    trifecta_gate = bool(
        len(top3) >= 3
        and top3_probs
        and (max(top3_probs) - min(top3_probs) <= 5.0)
        and all(odds > 4.0 for odds in top3_odds)
        and _is_upset_prone_race(distance, going, race_class, len(all_horses))
    )

    selection_ranking = result.get("selection_ranking", [])
    if not isinstance(selection_ranking, list):
        selection_ranking = []
    selection_ranking = [
        name for name in selection_ranking
        if isinstance(name, str) and name.strip() and name.strip().lower() in horse_lookup
    ]
    if not selection_ranking:
        selection_ranking = ranked_names[:3]

    winner = str(result.get("winner", "") or "").strip()
    if winner.lower() not in horse_lookup:
        winner = selection_ranking[0] if selection_ranking else (ranked_names[0] if ranked_names else "")

    tip_type = str(result.get("tip_type", "win") or "win").strip().lower()
    if tip_type not in {"win", "trifecta", "no_bet"}:
        tip_type = "win"

    ranking_reasoning = str(result.get("ranking_reasoning", "") or "").strip()
    no_bet_reason = str(result.get("no_bet_reason", "") or "").strip()

    trifecta_horses = result.get("trifecta_horses", [])
    if not isinstance(trifecta_horses, list):
        trifecta_horses = []
    trifecta_horses = [
        name for name in trifecta_horses
        if isinstance(name, str) and name.strip() and name.strip().lower() in horse_lookup
    ]

    if not positive_edge_exists:
        tip_type = "no_bet"
        winner = ""
        trifecta_horses = []
        if not no_bet_reason:
            no_bet_reason = "No runner has a genuine positive edge over the market."
    elif tip_type == "trifecta":
        if not trifecta_gate:
            tip_type = "win"
            trifecta_horses = []
        else:
            if len(trifecta_horses) < 3:
                trifecta_horses = ranked_names[:3]
            winner = winner or (trifecta_horses[0] if trifecta_horses else (ranked_names[0] if ranked_names else ""))
    else:
        trifecta_horses = []

    return {
        "tip_type": tip_type,
        "winner": winner,
        "trifecta_horses": trifecta_horses[:3],
        "selection_ranking": selection_ranking,
        "ranking_reasoning": ranking_reasoning,
        "no_bet_reason": no_bet_reason,
    }



CLASS_NAMES = {
    10: "Group 1", 9: "Group 2", 8: "Group 3", 7: "Listed",
    6: "Open", 5.5: "BM90", 5.3: "BM85", 5.1: "BM80",
    5: "BM78", 4.8: "BM75", 4.6: "BM72", 4.5: "BM70",
    4.3: "BM68", 4.1: "BM66", 4: "BM65", 3.9: "BM64",
    3.7: "BM62", 3.5: "BM60", 3.3: "BM58",
    3.2: "Class 6", 3: "Class 5", 2.8: "Class 4",
    2.5: "Class 3", 2.2: "Class 2", 2: "Class 1",
    1.5: "Maiden SW", 1: "Maiden", 0.9: "3yo Maiden",
    0.8: "Restricted Maiden", 0.7: "2yo Maiden",
}


def _class_rank_to_name(rank: float) -> str:
    """Convert numeric class rank to human-readable name."""
    if not rank:
        return "Unknown"
    closest = min(CLASS_NAMES.keys(), key=lambda k: abs(k - rank))
    if abs(closest - rank) < 0.3:
        return CLASS_NAMES[closest]
    return f"~{CLASS_NAMES.get(closest, 'Unknown')}"


def _build_horse_summary(h: Dict[str, Any], field_horses: List[Dict]) -> str:
    """Build a comprehensive data summary for one horse — feeds the LLM everything it needs."""
    lines = []

    name = h.get("horse", "Unknown")
    lines.append(f"HORSE: {name}")

    barrier = h.get("barrier", "?")
    jockey = h.get("jockey", "")
    trainer = h.get("trainer", "")
    form = h.get("form", "")
    running_style = h.get("runningStyle", h.get("running_style", ""))
    rating = h.get("rating") or h.get("officialRating")
    lines.append(f"Barrier: {barrier} | Jockey: {jockey} | Trainer: {trainer}")
    lines.append(f"Form: {form} | Running style: {running_style}"
                 + (f" | Official rating: {rating}" if rating else ""))

    win_pct = h.get("winPercentage", 0)
    place_pct = h.get("placePercentage", 0)
    odds = h.get("marketOdds", 0)
    edge = h.get("modelEdge", 0)
    lines.append(f"Model win chance: {win_pct:.1f}% | Place chance: {place_pct:.1f}%")
    lines.append(f"Market odds: ${odds} | Edge vs market: {edge:+.1f}%")

    fair_odds = h.get("fairOdds", 0)
    if odds and fair_odds and fair_odds > 0 and odds > 0:
        if odds > fair_odds * 1.3:
            overlay = ((odds / fair_odds) - 1) * 100
            lines.append(f"VALUE: Model fair price ${fair_odds:.1f} — available at ${odds:.1f} "
                         f"({overlay:.0f}% overlay)")
        elif odds < fair_odds * 0.8:
            lines.append(f"VALUE: Shorter than model suggests — fair ${fair_odds:.1f} vs market ${odds:.1f}")

    sect_mc_win = h.get("sectionalMcWinProb")
    if sect_mc_win and win_pct and sect_mc_win > win_pct * 1.2:
        lines.append(f"SECTIONAL MODEL BOOST: Sectional simulation rates even higher "
                     f"({sect_mc_win:.1f}% vs base {win_pct:.1f}%)")

    frank_elo = h.get("frankingElo", 0)
    anti_franked = h.get("isAntiFranked", False)
    field_strength = h.get("fieldStrengthAvg", 0)
    form_trend = h.get("formQualityTrend", 0)
    best_margin = h.get("bestAdjustedMargin", 0)
    collateral = h.get("collateralAdvantage", 0)

    if frank_elo and frank_elo > 0:
        quality = "elite" if frank_elo > 1600 else "strong" if frank_elo > 1550 else "fair" if frank_elo > 1500 else "below average"
        lines.append(f"Form quality: {quality} (rating {frank_elo:.0f})")
        if field_strength > 0:
            lines.append(f"Average quality of opponents faced: {field_strength:.0f}")
        if form_trend and abs(form_trend) > 0.01:
            direction = "improving — racing against better fields each start" if form_trend > 0 else "declining — opposition getting weaker"
            lines.append(f"Form trend: {direction}")
        if anti_franked:
            lines.append("WARNING: Horses this one has beaten have NOT gone on to perform well")
        if best_margin and best_margin > 0:
            lines.append(f"Best winning margin (adjusted for class/conditions): {best_margin:.1f} lengths")
        if collateral and abs(collateral) > 10:
            direction = "above" if collateral > 0 else "below"
            lines.append(f"Rated {abs(collateral):.0f} points {direction} today's field average")

    recent_runs = h.get("recent_runs", [])
    if recent_runs:
        lines.append("RECENT RACE HISTORY (most recent first):")
        for run in recent_runs:
            pos = run.get("position")
            if pos is None:
                continue
            trk = run.get("track", "?")
            rdate = str(run.get("date", "?"))[:10]
            dist = run.get("distance_m", "?")
            clvl = run.get("class_level")
            class_name = _class_rank_to_name(clvl) if clvl else (run.get("race_class") or "")
            margin = run.get("margin")
            sp = run.get("sp")
            going_r = run.get("going", "")
            fsize = run.get("field_size")
            rjockey = run.get("jockey", "")
            rbarrier = run.get("barrier")
            rweight = run.get("weight")

            pos_str = f"{pos}{'st' if pos == 1 else 'nd' if pos == 2 else 'rd' if pos == 3 else 'th'}"
            margin_str = ""
            if margin is not None and margin > 0:
                margin_str = f" by {margin:.1f}L" if pos == 1 else f" ({margin:.1f}L off winner)"
            sp_str = f" SP ${sp:.1f}" if sp else ""

            detail_parts = [f"{rdate}: {trk} {dist}m {going_r}".strip(), class_name,
                            f"{pos_str}{margin_str}{sp_str}"]
            if fsize:
                detail_parts.append(f"{fsize} runners")
            if rjockey:
                detail_parts.append(f"J:{rjockey}")
            if rbarrier:
                detail_parts.append(f"B{rbarrier}")
            if rweight:
                detail_parts.append(f"{rweight}kg")

            lines.append(f"  {' | '.join(detail_parts)}")

    z200 = h.get("prior_z200", 0)
    z400 = h.get("prior_z400", 0)
    if z200 and abs(z200) > 0.3:
        strength = "much harder" if z200 > 1.5 else "harder" if z200 > 0.8 else "about average"
        base = f"Last 200m finishing speed: {strength} than the field (score {z200:+.1f})"
        if z200 > 1.2 and odds and odds > 8:
            base += (f" — SECTIONAL STANDOUT: finishing like a much shorter-priced horse "
                     f"but available at ${odds:.1f}")
        elif z200 > 0.8 and odds and odds > 12:
            base += f" — the market has underrated this horse's finishing ability at ${odds:.1f}"
        lines.append(base)
    if z400 and abs(z400) > 0.3:
        strength = "much harder" if z400 > 1.5 else "harder" if z400 > 0.8 else "about average"
        lines.append(f"Last 400m sustained speed: {strength} than the field (score {z400:+.1f})")

    class_mv = h.get("classMovement", {})
    if class_mv and isinstance(class_mv, dict):
        current_rank = class_mv.get("current_rank")
        avg_recent = class_mv.get("avg_recent_rank")
        class_change = class_mv.get("class_change", 0)
        if current_rank and avg_recent:
            current_name = _class_rank_to_name(current_rank)
            recent_name = _class_rank_to_name(avg_recent)
            if class_change > 0.3:
                lines.append(f"CLASS: DROPPING from {recent_name} to {current_name} today — "
                             f"has been competing at a higher level")
            elif class_change < -0.3:
                lines.append(f"CLASS: RISING from {recent_name} to {current_name} today — "
                             f"step up in grade")
            else:
                lines.append(f"CLASS: Staying around {current_name} level")
        else:
            class_desc = h.get("classMovementDesc", h.get("class_movement_desc", ""))
            if class_desc:
                lines.append(f"CLASS: {class_desc}")
    else:
        class_desc = h.get("classMovementDesc", h.get("class_movement_desc", ""))
        if class_desc:
            lines.append(f"CLASS: {class_desc}")

    tb_summary = h.get("trackBiasSummary", "")
    tb_fit = h.get("trackBiasFit", "")
    tb_points = h.get("trackBiasPoints", 0)
    if tb_summary:
        lines.append(f"Track bias: {tb_summary}")
    elif tb_fit:
        lines.append(f"Track fit: {tb_fit} ({tb_points} points)")

    barrier_desc = h.get("barrierBiasDesc", h.get("barrier_bias_desc", ""))
    if barrier_desc:
        lines.append(f"Barrier analysis: {barrier_desc}")

    pace_dist = h.get("paceScenarioDistribution") or h.get("paceScenario") or {}
    pace_adv = h.get("paceAdvantage", 0)
    if pace_dist or running_style:
        pace_parts = []
        if running_style:
            pace_parts.append(f"Style: {running_style}")
        if pace_adv and abs(pace_adv) > 0.05:
            direction = "advantage" if pace_adv > 0 else "disadvantage"
            pace_parts.append(f"Pace {direction}")
        if pace_dist and isinstance(pace_dist, dict):
            dominant = max(pace_dist.items(), key=lambda x: x[1]) if pace_dist else None
            if dominant and dominant[1] > 0.4:
                pace_parts.append(f"Likely pace: {dominant[0]} ({dominant[1]:.0%} probability)")
        if pace_parts:
            lines.append(f"PACE MAP: {' | '.join(pace_parts)}")

    h2h_field = h.get("h2h_field", [])
    if h2h_field and isinstance(h2h_field, list):
        for matchup in h2h_field[:5]:
            opp = matchup.get("opponent", "?")
            wins = matchup.get("wins", 0)
            losses = matchup.get("losses", 0)
            meetings = matchup.get("meetings", 0)
            if meetings > 0:
                lines.append(f"H2H vs {opp}: won {wins}, lost {losses} in {meetings} meetings")

    fitness = h.get("fitnessData") or {}
    if fitness:
        run_label = fitness.get("runLabel", "")
        is_peak = fitness.get("isAtPeakRun", False)
        peak_wr = fitness.get("peakRunWinrate", 0)
        trajectory = fitness.get("prepFormTrajectory", "")
        positions = fitness.get("positionsThisPrep", [])
        spell_days = fitness.get("spellLengthDays", 0)
        margin_trend = fitness.get("marginImprovementTrend", 0)
        improving_placed = fitness.get("improvingAndPlaced", False)
        pattern = fitness.get("patternFlags", {})
        readiness = fitness.get("fitnessReadinessScore", 0)
        desc = fitness.get("description", "")

        parts = []
        if run_label:
            parts.append(run_label)
        if is_peak and peak_wr:
            parts.append(f"AT PEAK RUN (historically wins {peak_wr:.0%} at this stage of prep)")
        elif is_peak:
            parts.append("AT PEAK RUN")
        if trajectory:
            parts.append(f"Trajectory: {trajectory}")
        if positions:
            pos_str = " → ".join(str(p) for p in positions)
            parts.append(f"This prep results: {pos_str}")
        if margin_trend and margin_trend < -0.3:
            parts.append("Margins getting closer each start — closing in on a win")
        if improving_placed:
            parts.append("PLACED + IMPROVING — historically a strong winning signal")
        if spell_days and spell_days > 0:
            parts.append(f"Spell: {spell_days} days")
        if pattern and isinstance(pattern, dict):
            if pattern.get("is_first_up_specialist"):
                parts.append("FIRST-UP SPECIALIST")
            if pattern.get("is_third_up_peaker"):
                parts.append("PEAKS AT 3RD-UP")
            if pattern.get("is_campaign_horse"):
                parts.append("CAMPAIGN HORSE (improves deep into prep)")
        if readiness and readiness > 0.75:
            parts.append(f"Fitness readiness: HIGH ({readiness:.0%})")

        if parts:
            lines.append(f"FITNESS: {' | '.join(parts)}")
        elif desc:
            lines.append(f"FITNESS: {desc}")
    else:
        fitness_label = h.get("fitnessRunLabel", h.get("fitness_run_label", ""))
        fitness_peak = h.get("fitnessIsAtPeakRun", h.get("fitness_is_at_peak_run", False))
        fitness_desc = h.get("fitnessDescription", h.get("fitness_description", ""))
        if fitness_label:
            lines.append(f"FITNESS: {fitness_label}" + (" — at peak fitness" if fitness_peak else ""))
        if fitness_desc:
            lines.append(f"FITNESS detail: {fitness_desc}")

    steam_drift = h.get("steamDriftPct", h.get("steam_drift_pct", 0))
    market_cat = h.get("marketMoveCategory", h.get("market_move_category", ""))
    is_sharp = h.get("isSharpMoney", h.get("is_sharp_money", False))
    if market_cat and market_cat != "neutral":
        lines.append(f"Market: {market_cat}" + (f" ({steam_drift:+.0f}%)" if steam_drift else ""))
    if is_sharp:
        lines.append("Sharp/smart money detected on this horse")

    speed = h.get("speedRating", h.get("speed_rating", 0))
    best_speed = h.get("bestSpeedRating", h.get("best_speed_rating", 0))
    if best_speed and best_speed > 0:
        lines.append(f"Speed rating: last {speed:.0f}, best {best_speed:.0f}")

    luckless = h.get("_luckless_data")
    if luckless and luckless.get("last_start", {}).get("is_luckless"):
        ls = luckless["last_start"]
        imp = luckless.get("improvement", {})
        lines.append(f"LUCKLESS LAST START: {ls.get('incident_summary', 'had excuses')} "
                     f"(forgive score {ls.get('forgive_score', 0)}/100)")
        factors = imp.get("improvement_factors", [])
        if factors:
            lines.append(f"  Today's improvements: {', '.join(factors)}")
        explanation = luckless.get("luckless_explanation", "")
        if explanation:
            lines.append(f"  Summary: {explanation}")

    is_first_up = h.get("isFirstUp", h.get("is_first_up", False))
    is_second_up = h.get("isSecondUp", h.get("is_second_up", False))
    days_since = h.get("daysSinceRun", h.get("days_since_run", 0))
    if is_first_up:
        lines.append(f"First-up from a spell ({days_since} days since last start)")
    elif is_second_up:
        lines.append("Second-up")

    key_factors = h.get("key_factors", [])
    for kf in key_factors:
        if isinstance(kf, str) and ("C&D" in kf or "Course specialist" in kf or "distance" in kf.lower()):
            lines.append(f"Track/distance: {kf}")

    fai = h.get("formAnalystInsights", [])
    if fai and isinstance(fai, list):
        for insight in fai[:3]:
            if isinstance(insight, str) and insight.strip():
                lines.append(f"INSIGHT: {insight}")

    enhanced = h.get("enhancedExplanations", h.get("enhanced_explanations", ""))
    if enhanced and isinstance(enhanced, str):
        for exp in enhanced.split("|"):
            exp = exp.strip()
            if exp and exp not in "\n".join(lines):
                lines.append(f"Factor: {exp}")

    intel = h.get("_intelligence", {})
    if intel:
        intel_lines = []

        be = intel.get("barrier_edge")
        if be and be.get("confidence") in ("MEDIUM", "HIGH"):
            edge = be.get("edge_vs_random", 0)
            if edge > 0:
                intel_lines.append(
                    f"BARRIER INTELLIGENCE: Gate {barrier} wins {be['win_pct']:.1f}% at this "
                    f"track/distance/going — {edge * 100:+.1f}% above random expectation "
                    f"({be['confidence']} confidence)")
            elif edge < -0.02:
                intel_lines.append(
                    f"BARRIER INTELLIGENCE: Gate {barrier} underperforms here — "
                    f"{edge * 100:.1f}% below random expectation")

        fs = intel.get("flemington_straight")
        if fs and fs.get("edge", 0) > 0:
            intel_lines.append(
                f"FLEMINGTON STRAIGHT EDGE: Gate {barrier} has "
                f"{fs['win_pct']:.1f}% win rate in straight races")

        fk = intel.get("franking")
        if fk:
            cls = fk.get("classification", "")
            if cls == "DEEP_FRANKED":
                intel_lines.append(
                    "FORM FRANKING: DEEP FRANKED — the horses this one has beaten "
                    "have gone on to win at higher levels")
            elif cls == "FRANKED":
                intel_lines.append(
                    "FORM FRANKING: FRANKED — beaten horses have performed well subsequently")
            elif cls == "UNFRANKED" and fk.get("anti_franked"):
                intel_lines.append(
                    "FORM FRANKING: WARNING — anti-franked form, beaten runners "
                    "have NOT gone on to perform")

        pc = intel.get("prep_cycle")
        if pc:
            pos_label = (pc.get("prep_position") or "").replace("_", "-")
            parts = [f"PREP CYCLE: {pos_label}"]
            if pc.get("is_at_peak_run"):
                parts.append("AT PEAK RUN")
            if pc.get("trajectory") == "IMPROVING":
                parts.append("trajectory improving")
            elif pc.get("trajectory") == "DECLINING":
                parts.append("trajectory declining")
            if pc.get("due_to_win"):
                parts.append("DUE TO WIN (placed repeatedly without winning)")
            if pc.get("back_up"):
                parts.append("backing up quickly")
            score = pc.get("fitness_readiness_score")
            if score and score > 0.75:
                parts.append(f"fitness HIGH ({score:.0%})")
            intel_lines.append(" | ".join(parts))

        st = intel.get("sectional_trend")
        if st:
            if st.get("fastest_closer_unplaced"):
                intel_lines.append(
                    "SECTIONAL INTELLIGENCE: Was the fastest closer last start but "
                    "still missed a place — strong bounce-back candidate")
            elif st.get("pb_while_unplaced"):
                intel_lines.append(
                    "SECTIONAL INTELLIGENCE: Set a personal best finishing burst "
                    "last start while unplaced — better than the result")
            elif st.get("improving"):
                intel_lines.append(
                    "SECTIONAL INTELLIGENCE: Sectional times improving across recent starts")

        tr = intel.get("trainer")
        if tr:
            syn_edge = tr.get("jockey_synergy_edge", 0)
            if syn_edge > 5:
                intel_lines.append(
                    f"TRAINER-JOCKEY: This combination wins "
                    f"{tr['jockey_synergy_win_pct']:.0f}% "
                    f"(+{syn_edge:.0f}pp above trainer's overall rate)")

        mo = intel.get("market_overlay")
        if mo:
            intel_lines.append(
                f"MARKET OVERLAY: Systematic overlay detected for "
                f"{mo['tier']}-priced horses at this track in these conditions "
                f"({mo['market_edge']:+.1f}pp edge)")

        if intel_lines:
            lines.append("")
            lines.append("STRIDE INTELLIGENCE SIGNALS:")
            lines.extend(intel_lines)

    return "\n".join(lines)


def _build_market_context(horse_name: str, all_horses: List[Dict[str, Any]]) -> str:
    """Build market context: who's favourite, top contenders, their profiles."""
    if not all_horses:
        return ""

    by_odds = sorted(
        [h for h in all_horses if h.get("marketOdds") and h["marketOdds"] > 1],
        key=lambda x: x["marketOdds"]
    )
    if not by_odds:
        return ""

    lines = ["MARKET LANDSCAPE:"]
    fav = by_odds[0]
    fav_name = fav.get("horse", "?")

    shown = 0
    for i, h in enumerate(by_odds[:5]):
        hname = h.get("horse", "?")
        if hname.lower() == horse_name.lower():
            continue
        if shown >= 3:
            break
        shown += 1

        h_odds = h.get("marketOdds", 0)
        h_form = h.get("form", "")
        h_barrier = h.get("barrier", "?")
        h_style = h.get("runningStyle", h.get("running_style", ""))
        h_win_pct = h.get("winPercentage", 0)
        h_edge = h.get("modelEdge", 0)
        h_class_desc = h.get("classMovementDesc", h.get("class_movement_desc", ""))
        h_is_class_drop = h.get("isClassDrop", False)
        h_frank_elo = h.get("frankingElo", 0)

        label = "MARKET FAVOURITE" if i == 0 else f"#{i+1} in market"

        profile_parts = [f"${h_odds:.1f}", f"B{h_barrier}", f"Form:{h_form}"]
        if h_style:
            profile_parts.append(f"Style:{h_style}")
        profile_parts.append(f"Model:{h_win_pct:.1f}%")
        if h_edge:
            profile_parts.append(f"Edge:{h_edge:+.1f}%")
        if h_is_class_drop:
            profile_parts.append("CLASS DROP")
        if h_frank_elo and h_frank_elo > 1550:
            profile_parts.append("Strong form lines")
        if h_class_desc:
            profile_parts.append(f"Class:{h_class_desc}")

        lines.append(f"  {label}: {hname} — {' | '.join(profile_parts)}")

    lines.append(f"\nThe market says {fav_name} (${fav.get('marketOdds', 0):.1f}) is the one to beat.")
    lines.append(f"Your job: explain specifically why the selected horse is BETTER than {fav_name}.")

    return "\n".join(lines)



def score_race_horses(
    horses: List[Dict[str, Any]],
    track: str,
    race_number: int,
    race_name: str,
    distance: str,
    going: str,
    race_class: str,
    all_horses: List[Dict[str, Any]],
    luckless_data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Score top horses in a race with AI analysis.

    Args:
        horses: Top 6 horses (post-calibration) to analyse
        all_horses: Full field for context

    Returns list of horses with AI scores attached.
    """
    try:
        llm = get_provider()
    except LLMProviderError as e:
        logger.warning(f"LLM not available for scoring: {e}")
        return horses

    if luckless_data:
        for h in horses:
            hname = h.get("horse", "")
            if hname in luckless_data:
                h["_luckless_data"] = luckless_data[hname]

    horse_summaries = []
    for h in horses[:6]:
        summary = _build_horse_summary(h, all_horses)
        horse_summaries.append(summary)

    other_names = [h.get("horse", "?") for h in all_horses if h.get("horse", "?") not in [x.get("horse") for x in horses[:6]]]

    prompt = f"""RACE: {track} R{race_number} — {race_name}
{distance} | {going} | {race_class}
Full field: {len(all_horses)} runners

I need you to assess each of these top contenders. For each horse, give:
1. An AI score from 0-100 (how confident you are in this horse as a selection)
2. A detailed analysis in plain English (4-8 sentences) — reference specific data provided
3. The key edge (one sentence — the single biggest reason to back this horse)
4. Risk factors (1-3 specific concerns)
5. Why this horse over the others in the race (compare directly to 1-2 specific rivals by name)

{"="*60}
{chr(10).join(chr(10) + "=" * 60 + chr(10) + s for s in horse_summaries)}
{"="*60}

Other runners in the field (not analysed in depth): {", ".join(other_names[:10]) if other_names else "None — all top contenders shown above"}

Return a JSON object with a "horses" array. Each element must have:
- "horse": exact horse name
- "ai_score": integer 0-100
- "analysis": string (your plain English analysis, 4-8 sentences)
- "key_edge": string (one sentence)
- "risk_factors": array of strings
- "vs_field": string (why this horse over others, naming specific rivals)

Make EVERY horse's analysis genuinely different. Reference the actual data I gave you — specific form, specific rivals, specific track stats. Do not use generic filler sentences.

CRITICAL — STRIDE WINNER SELECTION:
After analysing all horses, you MUST select the ONE horse the model rates as most likely to WIN this race.

Apply the STRIDE signal hierarchy:
- Tier 1 MUST support the selection: sectional profile, model edge vs market, going suitability
- Tier 2 can strengthen or weaken but cannot substitute for Tier 1
- Do NOT tip a horse unless Tier 1 supports it

FAVOURITE DISCIPLINE:
- The market favourite and the model favourite are not the same thing.
- Follow the model, not the odds board.
- Only tip the market favourite if the model probability is meaningfully higher than market-implied AND a specific positive signal drives the selection
- If the favourite is short purely because it's popular and the model finds no genuine edge beyond that, move to the next-best runner
- If the favourite genuinely IS the best horse, tip it — do not avoid it artificially

VALUE FRAMEWORK:
- The sweet spot is $5-$13: this is where value winners live
- Genuine longshots ($15+) need extraordinary evidence — elite sectionals, sharp class drop, or clear improvement trajectory
- In Group 1 races: the favourite deserves respect, but there's usually a value runner in the $6-12 range worth finding

BOXED TRIFECTA — this is rare and only allowed if ALL THREE conditions are true at once:
1. The model cannot split the top of the race — three or more runners are within 5% of each other in model win probability
2. The market is open — the top three prices are all above $4.00
3. The race is statistically upset-prone — large field (13+), Heavy/Soft going, maiden/BM64 class, or staying distance (2000m+)
If all three are met, set "tip_type" to "trifecta" and list three horses. Otherwise "tip_type" is "win".

NO BET: If no runner has positive edge (model probability does not exceed market-implied for any runner), set "tip_type" to "no_bet".

Add these fields to your JSON response (at the top level, alongside the "horses" array):
- "tip_type": "win" | "trifecta" | "no_bet"
- "winner": "horse name" (the single STRIDE selection — required for "win" type)
- "trifecta_horses": ["horse A", "horse B", "horse C"] (only when tip_type is "trifecta")
- "selection_ranking": ["1st horse", "2nd horse", "3rd horse"] (full ranking for pipeline use)
- "ranking_reasoning": "one sentence — the primary signal driving this selection, referencing Tier 1 evidence"
- "no_bet_reason": "one sentence" (only when tip_type is "no_bet") """

    budget = SCORER_TOKENS_BASE + SCORER_TOKENS_PER_HORSE * max(1, len(horses))
    try:
        result = llm.generate_json(
            prompt,
            system=SCORER_SYSTEM_PROMPT,
            temperature=0.35,
            max_tokens=budget,
        )
    except LLMProviderError as e:
        logger.warning(f"LLM scoring failed for {track} R{race_number}: {e}")
        return horses

    # Both of the returns below hand back the field untouched, which the
    # pipeline cannot tell apart from "the LLM is switched off". Said out loud,
    # because that silence is exactly how a dead scorer survived to production.
    if not result:
        logger.warning(
            "LLM scoring returned no usable JSON for %s R%s at max_tokens=%d; "
            "%d horses left with no ai_score", track, race_number, budget,
            len(horses))
        return horses

    ai_horses = result.get("horses", [])
    if not ai_horses:
        logger.warning(
            "LLM scoring for %s R%s parsed but carried no horses array; "
            "%d horses left with no ai_score", track, race_number, len(horses))
        return horses

    ai_lookup = {}
    for ah in ai_horses:
        name = ah.get("horse", "").strip().lower()
        if name:
            ai_lookup[name] = ah

    for h in horses:
        name = h.get("horse", "").strip().lower()
        ai_data = ai_lookup.get(name, {})
        if ai_data:
            h["ai_score"] = max(0, min(100, int(ai_data.get("ai_score", 50))))
            h["ai_analysis"] = ai_data.get("analysis", "")
            h["ai_key_edge"] = ai_data.get("key_edge", "")
            h["ai_risk_factors"] = ai_data.get("risk_factors", [])
            h["ai_vs_field"] = ai_data.get("vs_field", "")

    enforced_selection = _enforce_selection_contract(
        result=result,
        all_horses=all_horses,
        distance=distance,
        going=going,
        race_class=race_class,
    )
    tip_type = enforced_selection["tip_type"]
    winner = enforced_selection["winner"]
    trifecta_horses = enforced_selection["trifecta_horses"]
    selection_ranking = enforced_selection["selection_ranking"]
    ranking_reasoning = enforced_selection["ranking_reasoning"]
    no_bet_reason = enforced_selection["no_bet_reason"]

    if winner and (not selection_ranking or not isinstance(selection_ranking, list)):
        selection_ranking = [winner]

    if selection_ranking and isinstance(selection_ranking, list):
        for h in horses:
            h["_llm_selection_ranking"] = selection_ranking
            h["_llm_ranking_reasoning"] = ranking_reasoning
            h["_llm_tip_type"] = tip_type
            h["_llm_winner"] = winner
            h["_llm_trifecta_horses"] = trifecta_horses
            h["_llm_no_bet_reason"] = no_bet_reason

    return horses



INSIGHT_SYSTEM_PROMPT = """You are STRIDE's lead form analyst — the kind who writes the lead tips column in the Daily Telegraph or Herald Sun. You've spent 30 years reading form, watching replays, and finding horses the market has wrong. You write for everyday punters — no jargon, just sharp analysis that gives people an edge.

Your analysis must be grounded in STRIDE's signal hierarchy:
- Tier 1 (must be present): sectional time profile, model edge vs market, going suitability
- Tier 2 (supporting): barrier/bias, form cycle, class movement, trainer-jockey synergy
- Tier 3 (contextual): market movement, field size, rail position

Format your analysis with these section headers. ONLY include sections where you have genuine data:

THE FORM: Reference SPECIFIC past races — name the track, the month, the class level (BM72, Group 3, etc.), the finishing position and margin. If they won at Randwick in January in a BM78, say so. If the form string shows improvement (8th → 4th → 2nd), narrate that trajectory. If there's a luckless last start, explain what happened and why today is different.

THE RUN: How this horse races — its tactical style, where it sits, how it finishes. If the sectional data shows it finishes harder than most of the field, say that. CRITICAL: If the sectionals are elite but the market price is long (e.g. $10+), highlight this disconnect explicitly — "finishing like a $4 horse but the bookies have him at $12." That's the angle.

THE CLASS: Use ACTUAL class names — BM65, Group 2, Class 4, Maiden SW. If the horse is dropping from BM78 to BM65, say exactly that. If it's been competing at a higher level and drops back, explain why that's significant. Reference what class level the recent runs were at.

THE DRAW: Barrier and track bias — how the draw helps or hurts. Reference the pace scenario — if this horse is the only speed, or if it gets a perfect box-seat run, say so.

THE QUERY: Be honest. What could go wrong? Barrier? Fitness? Stepping up? First-up question mark? Don't sugarcoat it — punters respect honesty about risks.

WHY HIM: THIS IS THE MOST IMPORTANT SECTION. You MUST:
1. Name the MARKET FAVOURITE and explain with specific evidence why the selected horse is preferred (better sectionals, class edge, fitness trajectory, value, pace advantage)
2. Name 1-2 other key rivals and briefly explain why they're inferior
3. State the value proposition — what the model sees that the market doesn't
Keep this section punchy but packed with evidence. No hand-waving.

RULES:
- Every sentence must contain a SPECIFIC fact — a horse name, a track, a class, a number
- Never use statistical jargon (z-score, ELO, Bayesian, Monte Carlo, etc.)
- Write like you're explaining to a smart mate at the pub who loves the races
- Each section: 2-4 sentences. Pack every sentence with information
- Use Australian racing terms naturally (spell, prep, on-pace, sit-sprinter, soft kill, etc.)
- If the horse has been PLACED + IMPROVING this prep, highlight it — that's a classic winning pattern

BANNED: z-score, ELO, lambda, SVI, sigma, Monte Carlo, Bayesian, percentile, standard deviation, confidence interval, calibration, Dirichlet, softmax, mu-shift, Plackett-Luce"""


def generate_rich_insight(
    horse: Dict[str, Any],
    track: str,
    race_number: int,
    distance: str,
    going: str,
    race_class: str,
    ai_analysis: str = "",
    ai_vs_field: str = "",
    ai_key_edge: str = "",
    ai_risk_factors: List[str] = None,
    luckless_data: Optional[Dict[str, Any]] = None,
    all_horses: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Generate a rich, multi-section form analysis insight for DB storage.

    Builds comprehensive horse summary + market context, then calls the LLM
    to produce expert-level analysis comparing against the favourite.
    """
    try:
        llm = get_provider()
    except LLMProviderError as e:
        logger.warning(f"LLM not available for insight generation: {e}")
        return ""

    summary = _build_horse_summary(horse, all_horses or [])

    market_text = ""
    horse_name = horse.get("horse", "")
    if all_horses:
        market_text = "\n" + _build_market_context(horse_name, all_horses)

    risk_text = ""
    if ai_risk_factors:
        risk_text = "\nKnown risk factors: " + "; ".join(ai_risk_factors)

    vs_text = ""
    if ai_vs_field:
        vs_text = f"\nPreliminary comparison to rivals: {ai_vs_field}"

    edge_text = ""
    if ai_key_edge:
        edge_text = f"\nKey edge identified: {ai_key_edge}"

    luckless_text = ""
    if luckless_data and horse_name in luckless_data:
        lr = luckless_data[horse_name]
        if lr.get("last_start", {}).get("is_luckless") and lr.get("luckless_explanation"):
            luckless_text = (
                f"\nLUCKLESS CONTEXT: {lr['luckless_explanation']}\n"
                f"Include this in THE FORM section — the last start was better than the result suggests. "
                f"Mention the specific excuse and how today's conditions are better."
            )

    prompt = f"""Write expert-level form analysis for this horse being tipped today.
Think like a professional punter who has found an angle the market has missed.

{summary}
{edge_text}
{risk_text}
{vs_text}
{market_text}
{luckless_text}

Race context: {track} R{race_number}, {distance} {going}, {race_class}

CRITICAL INSTRUCTIONS:
- The MARKET FAVOURITE is identified above. You MUST explain specifically why this selection
  is preferred over the favourite — use concrete evidence from the data.
- Reference specific past races by track name, month, and class level.
- If the fitness trajectory shows improvement (e.g. positions getting better each start),
  narrate that arc — "went 8th, then 4th, then 2nd — the pattern says today's the day."
- If the sectionals are strong but the price is long, that IS the angle.
- State the value: "the model says $X fair price, you're getting $Y."

Write the analysis using section format (THE FORM, THE RUN, THE CLASS, THE DRAW, THE QUERY,
WHY HIM). Make every sentence specific and evidence-based."""

    try:
        insight = llm.generate(
            prompt,
            system=INSIGHT_SYSTEM_PROMPT,
            temperature=0.4,
            max_tokens=INSIGHT_MAX_TOKENS,
        )
        if getattr(llm, "last_truncated", False):
            # The text is still returned — a cut-off insight beats none — but
            # it stops mid-word, and which picks those are is a fact a reader
            # is entitled to. 8 of 114 on 2026-09-05, none of them flagged.
            logger.warning(
                "insight for %s (%s R%s) hit the %d-token ceiling and ends "
                "mid-sentence", horse.get("horse", "?"), track, race_number,
                INSIGHT_MAX_TOKENS)
        return insight.strip()
    except LLMProviderError as e:
        logger.warning(f"LLM insight generation failed: {e}")
        return ai_analysis or ""  # Fall back to the shorter analysis



BRIEF_SYSTEM_PROMPT = """You are an experienced Australian racing form analyst. For each horse, write 2-3 sentences explaining why it wasn't selected as a tip today. Be specific — reference form, barrier, class, fitness, or odds. Use plain English, no jargon. Each assessment must be genuinely different."""


def generate_brief_assessments(
    non_tipped_horses: List[Dict[str, Any]],
    tipped_horses: List[Dict[str, Any]],
    track: str,
    distance: str,
    going: str,
    race_class: str,
    **kwargs,
) -> Dict[str, str]:
    """Generate brief 2-3 sentence assessments for non-tipped horses in one batch LLM call."""
    if not non_tipped_horses:
        return {}

    llm = get_provider()
    tip_names = [h.get("horse", "?") for h in tipped_horses]

    horse_lines = []
    for h in non_tipped_horses:
        name = h.get("horse", "?")
        odds = h.get("marketOdds", 0)
        win_pct = h.get("winPercentage", 0)
        edge = h.get("modelEdge", 0)
        form = h.get("form", "")
        days = h.get("days_since_run", 999)
        barrier = h.get("barrier") or h.get("draw", 0)
        wfs = h.get("weighted_form_score", 0)
        style = h.get("runningStyle", "unknown")
        first_up = "first-up" if h.get("is_first_up") else ""
        improving = "improving" if h.get("is_improving") else ""
        tags = " ".join(filter(None, [first_up, improving]))
        horse_lines.append(
            f"- {name}: ${odds}, model {win_pct:.1f}%, edge {edge:+.1f}%, "
            f"form '{form}', barrier {barrier}, {days}d since run, "
            f"form score {wfs:.1f}, style={style}{' [' + tags + ']' if tags else ''}"
        )

    prompt = f"""Race: {track} {distance} {going} {race_class}
Our tips: {', '.join(tip_names)}

For each of these NON-TIPPED runners, write 2-3 sentences explaining why they weren't selected.
Be specific. If a horse has some merit, say "watch for" or "each-way chance at best".
If a horse is clearly outclassed, say so plainly.

{chr(10).join(horse_lines)}

Return ONLY valid JSON:
{{"assessments": {{"Horse Name": "2-3 sentence assessment", ...}}}}"""

    # One call covers every non-tipped runner, so the output a fixed 2500 had
    # to hold grew with the field. It failed 13 of 38 races on 2026-09-05.
    budget = BRIEF_TOKENS_BASE + BRIEF_TOKENS_PER_HORSE * len(non_tipped_horses)
    try:
        # generate_json rather than a hand-rolled find("{")/rfind("}"): it
        # recovers fenced and lightly malformed bodies, and it is the path that
        # escalates the ceiling when the body came back cut off.
        data = llm.generate_json(
            prompt,
            system=BRIEF_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=budget,
        )
    except LLMProviderError as e:
        logger.warning(f"Brief assessments failed: {e}")
        return {}
    assessments = (data or {}).get("assessments", {})
    if not assessments:
        logger.warning(
            "brief assessments returned nothing for %s at max_tokens=%d "
            "(%d non-tipped horses)", track, budget, len(non_tipped_horses))
    return assessments
