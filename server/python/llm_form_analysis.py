"""
Analyses each race field BEFORE Monte Carlo simulation.
Produces structured pace/dynamics assessment and small confidence adjustments.

The LLM sees: track, distance, going, class, and every runner's form/stats.
It returns structured JSON that gets applied as bounded mu-shifts to MC input.
"""

import json
import logging
from typing import Dict, Any, List, Optional

from llm_provider import get_provider, LLMProviderError

logger = logging.getLogger(__name__)

# Maximum mu-shift the LLM can apply (prevents model domination)
MAX_ADJUSTMENT = 0.08

SYSTEM_PROMPT = """You are STRIDE's pre-simulation race analyst. You're looking at a race field before the Monte Carlo model runs. Your job is to identify dynamics that a pure numbers model might miss, using STRIDE's signal hierarchy:

TIER 1 — primary (these drive the biggest adjustments):
- Form Franking: Is the horse DEEP_FRANKED (beaten quality opponents who've since won)? Deep-franked form is the strongest upgrade signal. Anti-franked runners (beaten weak fields) should be downgraded — their form is hollow.
- Sectional time profile: does any runner's recent closing sectional suggest genuine fitness the model might underrate? A personal-best closing split last start while not winning is a strong upgrade signal.
- Pace shape: who leads, is there speed pressure, will it be a genuine tempo or a sit-and-sprint?

TIER 2 — structural (strengthen or weaken a Tier 1 case):
- Barrier Intelligence: does the runner's gate have a statistically significant edge or disadvantage at this track/distance/going?
- Prep Cycle: is the runner at peak run, due to win, or trajectory improving/declining?
- Going suitability: does the going specifically suit or hinder certain runners based on demonstrated form on this surface?
- Class context: is anyone dropping or rising significantly in grade?

TIER 3 — supporting:
- Trainer × jockey combination synergy
- Course form, distance form
- Field size impact on race dynamics

You must return ONLY valid JSON with these exact keys:
{
  "pace_scenario": "one sentence describing the likely pace shape",
  "key_race_dynamics": "2-3 sentences on the most important factors in this race",
  "confidence_adjustments": {"runner_number": adjustment, ...},
  "watch_runners": [runner_numbers],
  "reasoning": "2-3 sentences explaining your adjustments"
}

Rules for confidence_adjustments:
- Use runner NUMBER (saddlecloth) as key, as a string
- Values must be between -0.08 and +0.08
- Only adjust runners where you see a clear form/dynamics edge or disadvantage
- Most runners should NOT be adjusted (omit them)
- Positive = you think the model might underrate this horse
- Negative = you think the model might overrate this horse
- Give highest-magnitude adjustments to Tier 1 signals (sectionals, going, pace). Do NOT give a large adjustment based on Tier 2/3 alone.

Be concise. No markdown. Just the JSON object."""


def analyse_race_field(
    track: str,
    distance: str,
    going: str,
    race_class: str,
    race_number: int,
    runners: List[Dict[str, Any]],
    luckless_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Pre-MC analysis of a race field. Returns structured dynamics or None on failure."""
    try:
        llm = get_provider()
    except LLMProviderError as e:
        logger.warning(f"LLM not available for pre-analysis: {e}")
        return None

    runner_lines = []
    for r in runners:
        if r.get("scratched"):
            continue
        num = r.get("number", "?")
        name = r.get("horse", "Unknown")
        barrier = r.get("draw") or r.get("barrier", "?")
        jockey = r.get("jockey", "TBA") or "TBA"
        trainer = r.get("trainer", "Unknown") or "Unknown"
        form = r.get("form", "-") or "-"
        weight = r.get("weight", "")
        age = r.get("age", "")

        odds_list = r.get("odds", [])
        odds_str = ""
        if odds_list and isinstance(odds_list, list) and len(odds_list) > 0:
            last_odds = odds_list[-1] if isinstance(odds_list[-1], (int, float)) else odds_list[-1].get("odds", "")
            if last_odds:
                odds_str = f"${last_odds}"

        stats = r.get("stats", {}) or {}
        ground_info = ""
        going_lower = going.lower() if going else ""
        if "soft" in going_lower or "heavy" in going_lower:
            soft_stats = stats.get("ground_soft_stats") or stats.get("ground_heavy_stats")
            if soft_stats and isinstance(soft_stats, dict):
                total = soft_stats.get("total", 0)
                wins = soft_stats.get("first", 0)
                if int(total) > 0:
                    ground_info = f"wet:{wins}/{total}"
        elif "firm" in going_lower:
            firm_stats = stats.get("ground_firm_stats")
            if firm_stats and isinstance(firm_stats, dict):
                total = firm_stats.get("total", 0)
                wins = firm_stats.get("first", 0)
                if int(total) > 0:
                    ground_info = f"firm:{wins}/{total}"

        cd_stats = stats.get("course_distance_stats", {}) or {}
        cd_info = ""
        if cd_stats and int(cd_stats.get("total", 0)) > 0:
            cd_info = f"C&D:{cd_stats.get('first', 0)}/{cd_stats.get('total', 0)}"

        last_raced = stats.get("last_raced", "")
        last_won = stats.get("last_won", "")

        parts = [f"#{num} {name}", f"B{barrier}", f"J:{jockey}", f"T:{trainer}",
                 f"Form:{form}", age]
        if weight:
            parts.append(f"{weight}kg")
        if odds_str:
            parts.append(odds_str)
        if ground_info:
            parts.append(ground_info)
        if cd_info:
            parts.append(cd_info)
        if last_raced:
            parts.append(f"LastRan:{last_raced}")

        if luckless_data and name in luckless_data:
            lr = luckless_data[name]
            if lr.get("last_start", {}).get("is_luckless"):
                ls = lr["last_start"]
                imp = lr.get("improvement", {})
                parts.append(f"LUCKLESS(score={ls.get('forgive_score', 0)}, "
                             f"{ls.get('dominant_incident', '?')}, "
                             f"barrier_imp={imp.get('barrier_improvement', 0):+d})")

        runner_lines.append(" | ".join(p for p in parts if p))

    field_text = "\n".join(runner_lines)

    prompt = f"""Race: {track} R{race_number} — {distance} {going} ({race_class})
Field size: {len(runner_lines)} runners

RUNNERS:
{field_text}

Analyse this field and return the JSON assessment."""

    try:
        result = llm.generate_json(prompt, system=SYSTEM_PROMPT, temperature=0.3, max_tokens=2000)
    except LLMProviderError as e:
        logger.warning(f"LLM pre-analysis failed for {track} R{race_number}: {e}")
        return None

    if not result:
        return None

    adjustments = result.get("confidence_adjustments", {})
    if isinstance(adjustments, dict):
        clamped = {}
        for k, v in adjustments.items():
            try:
                val = float(v)
                clamped[str(k)] = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, val))
            except (ValueError, TypeError):
                continue
        result["confidence_adjustments"] = clamped

    return result


def apply_pre_mc_adjustments(
    runners: List[Dict[str, Any]],
    analysis: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Apply LLM confidence adjustments to runner list before MC simulation.
    Adds a small mu-shift via 'llm_mu_adjustment' field.
    """
    adjustments = analysis.get("confidence_adjustments", {})
    if not adjustments:
        return runners

    for runner in runners:
        num = str(runner.get("number", ""))
        if num in adjustments:
            runner["llm_mu_adjustment"] = adjustments[num]

    return runners
