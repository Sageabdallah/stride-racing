#!/usr/bin/env python3
"""
Canonical normalisation, validation, pace map, and feature engineering stage.
Runs BEFORE mc_api.py subprocess.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from normalize import (
    normalize_track_name,
    normalize_distance,
    normalize_weight,
    normalize_barrier,
    normalize_age,
    parse_form_string,
    is_scratched,
    extract_stats_rate,
    extract_going_suitability,
)
from track_condition_db import GOING_KEYWORDS
from pace_modeling import (
    classify_running_style,
    predict_race_tempo,
    calculate_pace_advantage,
    analyze_pace_map,
    RunningStyle,
    PaceScenario,
)
from advanced_features import parse_class_level

logger = logging.getLogger(__name__)

VERSION = "1.0"


GOING_ENUM = ("FIRM", "GOOD", "SOFT", "HEAVY", "SYNTHETIC", "UNKNOWN")

_CATEGORY_TO_ENUM = {
    "firm": "FIRM",
    "good": "GOOD",
    "soft": "SOFT",
    "heavy": "HEAVY",
    "synthetic": "SYNTHETIC",
}

_BAND_DEFAULTS: Dict[str, float] = {
    "FIRM": 1.5,
    "GOOD": 4.0,
    "SOFT": 6.5,
    "HEAVY": 9.0,
    "SYNTHETIC": 5.0,
    "UNKNOWN": 4.0,
}


def canonicalise_going(raw: Optional[str]) -> Dict[str, Any]:
    """
    Single authoritative going normaliser.

    Returns {"canonical": str, "rating_0_10": float, "raw": str}.
    """
    if not raw:
        return {"canonical": "UNKNOWN", "rating_0_10": 4.0, "raw": ""}

    text = str(raw).strip()
    text_lower = text.lower()

    num_match = re.search(r"(\d+)\s*$", text_lower)
    num = int(num_match.group(1)) if num_match else None

    text_no_num = re.sub(r"\d+", "", text_lower).strip()

    # Keyword match — priority order prevents "Good to Firm" → GOOD
    matched_category: Optional[str] = None
    for category in ("synthetic", "heavy", "soft", "firm", "good"):
        for keyword in GOING_KEYWORDS[category]:
            if keyword in text_no_num:
                matched_category = category
                break
        if matched_category:
            break

    if not matched_category:
        return {"canonical": "UNKNOWN", "rating_0_10": 4.0, "raw": text}

    canonical = _CATEGORY_TO_ENUM[matched_category]
    default_rating = _BAND_DEFAULTS[canonical]

    if num is not None and canonical != "SYNTHETIC":
        rating = float(max(0, min(10, num)))
    else:
        rating = default_rating

    return {"canonical": canonical, "rating_0_10": round(rating, 1), "raw": text}




def normalise_name(raw: Optional[str], name_type: str = "horse") -> Tuple[str, float]:
    """
    Conservative name normalisation.

    Returns (canonical_name, confidence).
    confidence: 1.0 unchanged, 0.95 case/whitespace, 0.90 unicode changes.
    """
    if not raw:
        return ("", 0.0)

    text = str(raw)
    stripped = text.strip()

    if not stripped:
        return ("", 0.0)

    if name_type == "track":
        canonical = normalize_track_name(stripped)
        conf = 1.0 if canonical == stripped else 0.95
        return (canonical, conf)

    nfc = unicodedata.normalize("NFC", stripped)
    nfc = nfc.replace("\u2018", "'").replace("\u2019", "'")
    nfc = nfc.replace("\u201c", '"').replace("\u201d", '"')
    nfc = re.sub(r"[\U0001F000-\U0001FFFF]", "", nfc)
    nfc = re.sub(r"\s+", " ", nfc).strip()

    # but Chris's -> Chris'S). Fix by lowering chars after apostrophes.
    canonical = nfc.title()
    canonical = re.sub(r"'S\b", "'s", canonical)
    canonical = re.sub(r"'T\b", "'t", canonical)

    if canonical == stripped:
        conf = 1.0
    elif canonical.lower() == stripped.lower():
        conf = 0.95
    else:
        conf = 0.90

    return (canonical, conf)



_QUALITY_TIERS = [
    (90, "group"),
    (70, "stakes"),
    (35, "benchmark"),
    (0, "maiden"),
]


def unify_class_level(race_class_raw: Optional[str], prize_money: Optional[float] = None) -> Dict[str, Any]:
    """Canonical class level using 0-100 scale from advanced_features.py."""
    raw = str(race_class_raw or "").strip()
    level = parse_class_level(raw, prize_money) if raw else 30

    quality_tier = "maiden"
    for threshold, tier in _QUALITY_TIERS:
        if level >= threshold:
            quality_tier = tier
            break

    label = raw.upper().strip() if raw else "UNKNOWN"

    return {
        "canonical_label": label,
        "level_0_100": level,
        "raw": raw,
        "quality_tier": quality_tier,
    }



CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"


def _flag(code: str, severity: str, message: str, runner: Optional[str] = None) -> Dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "runner": runner}


def validate_race(race: Dict[str, Any], runners: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate normalised race + runners. Returns list of flag dicts."""
    flags: List[Dict[str, Any]] = []

    dist = race.get("distance_m", 0)
    if dist < 400 or dist > 5000:
        flags.append(_flag("DISTANCE_IMPLAUSIBLE", CRITICAL,
                           f"Distance {dist}m outside 400-5000m range"))

    field_size = len(runners)
    if field_size < 2:
        flags.append(_flag("FIELD_TOO_SMALL", CRITICAL,
                           f"Only {field_size} runner(s) after scratching"))
    elif field_size > 30:
        flags.append(_flag("FIELD_TOO_LARGE", WARNING,
                           f"Unusually large field: {field_size} runners"))

    going = race.get("going", {})
    if going.get("canonical") == "UNKNOWN":
        flags.append(_flag("GOING_UNKNOWN", WARNING,
                           f"Could not determine going from '{going.get('raw', '')}'"))

    total_implied = 0.0
    odds_count = 0
    for r in runners:
        odds = r.get("market_odds")
        if odds and odds > 1:
            total_implied += 1.0 / odds
            odds_count += 1
    if odds_count >= 3:
        overround = total_implied
        if overround < 0.90 or overround > 1.60:
            flags.append(_flag("OVERROUND_EXTREME", WARNING,
                               f"Market overround {overround:.2f} outside normal range"))

    seen_names: Dict[str, int] = {}

    for r in runners:
        horse = r.get("horse", "")
        horse_key = horse.lower().strip()

        if not horse_key:
            flags.append(_flag("MISSING_HORSE_NAME", CRITICAL,
                               "Runner has no horse name", runner=horse or "(empty)"))

        if horse_key in seen_names:
            flags.append(_flag("DUPLICATE_RUNNER", CRITICAL,
                               f"Duplicate horse name '{horse}'", runner=horse))
        seen_names[horse_key] = seen_names.get(horse_key, 0) + 1

        barrier = r.get("barrier", 0)
        if barrier > field_size + 2 and barrier > 0:
            flags.append(_flag("BARRIER_OUT_OF_RANGE", WARNING,
                               f"Barrier {barrier} in {field_size}-horse field", runner=horse))
        if barrier == 0:
            flags.append(_flag("MISSING_BARRIER", INFO,
                               "Barrier is 0 (draw field present but zero)", runner=horse))

        odds = r.get("market_odds")
        if odds is not None:
            if odds <= 1.01 or odds > 500:
                flags.append(_flag("IMPOSSIBLE_ODDS", WARNING,
                                   f"Odds ${odds:.2f} outside valid range", runner=horse))
        else:
            flags.append(_flag("MISSING_ODDS", INFO, "No market odds available", runner=horse))

        weight = r.get("weight_kg", 0)
        if weight > 0 and (weight < 48 or weight > 70):
            flags.append(_flag("WEIGHT_IMPLAUSIBLE", WARNING,
                               f"Weight {weight}kg outside 48-70kg range", runner=horse))

        age = r.get("age_years", 0)
        if age > 0 and (age < 2 or age > 15):
            flags.append(_flag("AGE_IMPLAUSIBLE", WARNING,
                               f"Age {age} outside 2-15 range", runner=horse))

        if not r.get("jockey"):
            flags.append(_flag("MISSING_JOCKEY", INFO, "No jockey listed", runner=horse))

    return flags


def is_race_valid(flags: List[Dict]) -> bool:
    """True if no CRITICAL flags."""
    return not any(f["severity"] == CRITICAL for f in flags)



RUN_STYLE_ENUM = ("LEAD", "ON_PACE", "MIDFIELD", "BACKMARKER", "UNKNOWN")

_STYLE_MAP = {
    RunningStyle.LEADER: "LEAD",
    RunningStyle.ON_PACE: "ON_PACE",
    RunningStyle.MIDFIELD: "MIDFIELD",
    RunningStyle.OFF_PACE: "BACKMARKER",
    RunningStyle.BACKMARKER: "BACKMARKER",
}


def infer_run_style(runner: Dict[str, Any]) -> Dict[str, Any]:
    """
    Infer run_style bucket from pace_modeling.classify_running_style().
    Merges OFF_PACE → BACKMARKER for the 4-bucket enum.
    """
    style, confidence = classify_running_style(runner)
    bucket = _STYLE_MAP.get(style, "UNKNOWN")

    explicit = runner.get("running_style", "")
    if explicit:
        source = "explicit"
    elif runner.get("form") or runner.get("form_string"):
        source = "form_derived"
    else:
        source = "barrier_heuristic"

    return {"bucket": bucket, "confidence": round(confidence, 2), "source": source}




def build_pace_map(
    runners: List[Dict[str, Any]],
    race: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Build race-level pace map and per-runner pace features.

    Returns (pace_map_dict, per_runner_pace_features_list).
    per_runner_pace_features_list is parallel to runners list.
    """
    distance_m = race.get("distance_m", 1400)
    track = race.get("track", "")
    field_size = len(runners)

    scenario, analysis = predict_race_tempo(runners, distance_m)

    runner_styles: List[Tuple[str, RunningStyle, float]] = []
    lead_count = 0
    for r in runners:
        style, conf = classify_running_style(r)
        horse = r.get("horse", "Unknown")
        runner_styles.append((horse, style, conf))
        if style == RunningStyle.LEADER:
            lead_count += 1

    pace_groups: Dict[str, List[str]] = {
        "LEAD": [], "ON_PACE": [], "MIDFIELD": [], "BACKMARKER": [],
    }
    for horse, style, _ in runner_styles:
        bucket = _STYLE_MAP.get(style, "MIDFIELD")
        pace_groups[bucket].append(horse)

    pressure_map = {
        PaceScenario.VERY_FAST: "HIGH",
        PaceScenario.FAST: "HIGH",
        PaceScenario.MODERATE: "MEDIUM",
        PaceScenario.SLOW: "LOW",
        PaceScenario.VERY_SLOW: "LOW",
    }
    expected_pressure = pressure_map.get(scenario, "UNKNOWN")

    pace_map = {
        "predicted_tempo": scenario.value,
        "expected_pressure": expected_pressure,
        "likely_leaders": pace_groups["LEAD"],
        "likely_on_pace": pace_groups["ON_PACE"],
        "likely_backmarkers": pace_groups["BACKMARKER"],
        "pace_groups": pace_groups,
    }

    per_runner_pace: List[Dict[str, Any]] = []
    is_hot = scenario in (PaceScenario.FAST, PaceScenario.VERY_FAST)
    is_soft = scenario in (PaceScenario.SLOW, PaceScenario.VERY_SLOW)

    for i, r in enumerate(runners):
        horse, style, conf = runner_styles[i]
        barrier = r.get("barrier", 0)
        bucket = _STYLE_MAP.get(style, "MIDFIELD")

        adv = calculate_pace_advantage(style, scenario, barrier, field_size, track)

        if bucket == "LEAD":
            settling = max(1, min(3, barrier))
        elif bucket == "ON_PACE":
            settling = max(2, min(field_size // 3, barrier))
        elif bucket == "MIDFIELD":
            settling = field_size // 2
        else:
            settling = max(field_size - 3, field_size // 2)

        is_lone_speed = bucket == "LEAD" and lead_count == 1

        suits_tempo = (
            (bucket in ("BACKMARKER",) and is_hot) or
            (bucket in ("LEAD", "ON_PACE") and is_soft) or
            (bucket == "MIDFIELD" and scenario == PaceScenario.MODERATE)
        )

        risk_flags: List[str] = []
        if bucket == "LEAD" and is_hot:
            risk_flags.append("LEADS_INTO_HOT_PACE")
        if bucket == "LEAD" and lead_count == 1:
            risk_flags.append("LONE_LEADER")
        if barrier > field_size * 0.6 and bucket in ("LEAD", "ON_PACE"):
            risk_flags.append("WIDE_NO_COVER_RISK")
        if bucket == "BACKMARKER" and is_soft:
            risk_flags.append("NEEDS_TEMPO")
        if bucket == "ON_PACE" and is_hot and barrier > field_size * 0.5:
            risk_flags.append("MAP_TRAP_RISK")

        per_runner_pace.append({
            "settling_position": settling,
            "pace_advantage": adv,
            "is_lone_speed": is_lone_speed,
            "suits_tempo": suits_tempo,
            "map_risk_flags": risk_flags,
        })

    return pace_map, per_runner_pace



GOING_STATS_MAP = {
    "FIRM": "ground_firm_stats",
    "GOOD": "ground_good_stats",
    "SOFT": "ground_soft_stats",
    "HEAVY": "ground_heavy_stats",
    "SYNTHETIC": "ground_aw_stats",
}

DISTANCE_CATEGORIES = [
    (1200, "sprint"),
    (1600, "mile"),
    (2200, "middle"),
    (99999, "staying"),
]


def _distance_category(m: int) -> str:
    for threshold, cat in DISTANCE_CATEGORIES:
        if m <= threshold:
            return cat
    return "staying"


def engineer_features(
    runner: Dict[str, Any],
    all_runners: List[Dict[str, Any]],
    race: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive candidate features for downstream ML model."""
    field_size = len(all_runners) or 1
    barrier = runner.get("barrier", 0)
    weight = runner.get("weight_kg", 56.0)
    age = runner.get("age_years", 4)
    distance_m = race.get("distance_m", 1400)
    going_canonical = race.get("going", {}).get("canonical", "GOOD")
    run_bucket = runner.get("run_style", {}).get("bucket", "MIDFIELD")

    all_weights = [r.get("weight_kg", 56.0) for r in all_runners if r.get("weight_kg", 0) > 0]
    avg_weight = sum(all_weights) / len(all_weights) if all_weights else 56.0

    stats = runner.get("_stats", {})
    stats_field = GOING_STATS_MAP.get(going_canonical, "ground_good_stats")
    going_stats = stats.get(stats_field, {})
    going_rate = extract_stats_rate(going_stats)
    going_suitability = going_rate.get("win_rate", 20.0) / 100.0

    odds = runner.get("market_odds")
    implied_prob = (1.0 / odds) if odds and odds > 1 else 0.0
    total_implied = sum(
        (1.0 / r.get("market_odds", 999)) for r in all_runners
        if r.get("market_odds") and r.get("market_odds") > 1
    ) or 1.0
    field_share = implied_prob / total_implied if total_implied > 0 else 0.0

    form_parsed = runner.get("form_parsed", {})
    form_recency = form_parsed.get("decay_weighted_score", form_parsed.get("form_score", 5.0))

    race_dist_cat = _distance_category(distance_m)

    return {
        "barrier_field_ratio": round(barrier / field_size, 3) if field_size else 0,
        "weight_vs_field_avg": round(weight - avg_weight, 2),
        "is_wide_draw": barrier > field_size * 0.65,
        "is_favourable_barrier": barrier <= 4 and run_bucket in ("LEAD", "ON_PACE"),
        "going_suitability_proxy": round(going_suitability, 3),
        "distance_category": race_dist_cat,
        "distance_category_match": True,
        "class_movement_direction": "unknown",
        "implied_prob_vs_field_share": round(field_share, 4),
        "form_recency_score": round(form_recency, 2),
        "age_distance_interaction": round((1.0 / max(age, 2)) * (distance_m / 1600.0), 3),
    }


CANDIDATE_FEATURES_TO_TEST = [
    {
        "name": "jockey_track_specialisation",
        "definition": "Jockey win% at this specific track over last 12 months",
        "rationale": "Jockeys develop track-specific tactical knowledge, especially tight-turning tracks",
    },
    {
        "name": "wet_form_delta",
        "definition": "Difference in form_score on soft/heavy vs firm/good tracks",
        "rationale": "Some horses dramatically improve or regress on wet tracks — the delta is the signal",
    },
    {
        "name": "last_start_class_gap",
        "definition": "Numeric class level difference (0-100 scale) between last race and today's race",
        "rationale": "Dropping class is the strongest single predictor in racing; the magnitude matters",
    },
    {
        "name": "spell_return_profile",
        "definition": "Horse's historical first-up win rate after spells > 60 days",
        "rationale": "Some horses are lethal fresh, others need a run — this profile captures that",
    },
    {
        "name": "barrier_going_interaction",
        "definition": "barrier_field_ratio * going_rating / 10",
        "rationale": "Wide barriers are more penalising on heavy tracks where cover is harder to find",
    },
]




def llm_enrich(normalised_race: Dict[str, Any]) -> Dict[str, Any]:
    """
    Optional Groq-powered enrichment. One call per race.
    Corrects running styles, flags going suitability concerns.
    Returns empty dict on failure.
    """
    try:
        from llm_provider import get_provider
        provider = get_provider()
    except Exception:
        return {}

    race = normalised_race.get("race", {})
    runners = normalised_race.get("runners", [])

    lines = [
        f"Race: {race.get('track')} R{race.get('race_number')} — "
        f"{race.get('distance_m')}m, {race.get('going', {}).get('canonical')}, "
        f"{race.get('class', {}).get('canonical_label')}",
        f"Field ({len(runners)} runners):",
    ]
    for r in runners[:16]:
        lines.append(
            f"  #{r.get('number', '?')} {r.get('horse', '?')} "
            f"(B{r.get('barrier', '?')}, form:{r.get('form', '?')}, "
            f"style:{r.get('run_style', {}).get('bucket', '?')}, "
            f"going_suit:{r.get('derived_features', {}).get('going_suitability_proxy', '?')})"
        )

    prompt = "\n".join(lines) + """

Tasks:
1. If any horse's inferred running style is clearly wrong, correct it.
   Styles: LEAD, ON_PACE, MIDFIELD, BACKMARKER.
   Only correct if you are >80% confident.
2. If today's going notably suits or disadvantages any horse, note it in 5 words or less.
3. Is the pace scenario correct? Override only if clearly wrong.

Return JSON only:
{"style_corrections": {"HORSE_NAME": "BUCKET"}, "going_suitability_flags": {"HORSE_NAME": "brief note"}, "pace_scenario_override": null or "slow/moderate/fast", "reasoning": "one sentence"}"""

    system = (
        "You are an Australian horse racing pace analyst. "
        "Return ONLY valid JSON, no commentary."
    )

    try:
        result = provider.generate_json(prompt, system=system, temperature=0.2, max_tokens=400)
        if result:
            result["enrichment_model"] = getattr(provider, "model", "unknown")
            result["enrichment_timestamp"] = datetime.now(timezone.utc).isoformat()
        return result or {}
    except Exception as e:
        logger.warning(f"LLM enrichment failed: {e}")
        return {}




def _extract_odds(runner: Dict[str, Any]) -> Optional[float]:
    """Extract decimal odds from raw runner (mirrors pipeline extract_odds)."""
    odds_arr = runner.get("odds", [])
    if isinstance(odds_arr, list):
        for entry in odds_arr:
            if isinstance(entry, dict):
                for key in ("decimal", "win_odds", "odds"):
                    val = entry.get(key)
                    if val is not None:
                        try:
                            dec = float(str(val).replace("$", ""))
                            if 1.01 < dec < 500:
                                return dec
                        except (ValueError, TypeError):
                            pass
    for key in ("sp", "win_odds", "fixed_odds", "market_odds", "price"):
        val = runner.get(key)
        if val is not None:
            try:
                dec = float(str(val).replace("$", ""))
                if 1.01 < dec < 500:
                    return dec
            except (ValueError, TypeError):
                pass
    return None


def normalise_race(
    raw_race: Dict[str, Any],
    enable_llm: bool = False,
) -> Dict[str, Any]:
    """Main entry point. Takes raw racecard race dict, returns NormalisedRace JSON."""
    now = datetime.now(timezone.utc).isoformat()

    raw_track = raw_race.get("course") or raw_race.get("track") or ""
    track_canonical, _ = normalise_name(raw_track, "track")

    distance_raw = raw_race.get("distance", "")
    distance_m = normalize_distance(distance_raw)

    going = canonicalise_going(raw_race.get("going") or raw_race.get("track_condition"))

    race_class_raw = raw_race.get("class") or raw_race.get("raceClass") or raw_race.get("race_class") or ""
    prize_raw = raw_race.get("prize_total") or raw_race.get("prizeTotal") or raw_race.get("prize") or 0
    try:
        prize_money = float(prize_raw)
    except (ValueError, TypeError):
        prize_money = 0.0
    class_info = unify_class_level(race_class_raw, prize_money)

    race_number = int(raw_race.get("race_number", 0) or raw_race.get("raceNumber", 0) or 0)
    race_name = raw_race.get("race_name", "") or raw_race.get("raceName", "")
    race_date = raw_race.get("raceDate", "") or raw_race.get("race_date", "") or raw_race.get("date", "")
    off_time = raw_race.get("off_time", "") or raw_race.get("offTime", "") or ""

    race_dict = {
        "track": track_canonical,
        "track_raw": raw_track,
        "race_number": race_number,
        "race_name": race_name,
        "distance_m": distance_m,
        "going": going,
        "class": class_info,
        "prize_money": prize_money,
        "race_date": race_date,
        "off_time": off_time,
    }

    raw_runners = raw_race.get("runners", [])
    active_runners: List[Dict[str, Any]] = []

    for raw_r in raw_runners:
        if is_scratched(raw_r):
            continue

        horse_name, horse_conf = normalise_name(raw_r.get("horse") or raw_r.get("name") or raw_r.get("horseName"))
        jockey_name, _ = normalise_name(raw_r.get("jockey") or raw_r.get("jockeyName"), "jockey")
        trainer_name, _ = normalise_name(raw_r.get("trainer") or raw_r.get("trainerName"), "trainer")

        barrier = normalize_barrier(raw_r.get("barrier") or raw_r.get("draw"))
        weight_kg = normalize_weight(raw_r.get("weight"))
        age_years = normalize_age(raw_r.get("age"))

        sex_raw = str(raw_r.get("sex", "") or "").upper().strip()
        sex = sex_raw if sex_raw in ("G", "M", "F", "C", "H", "R") else "UNKNOWN"

        form_str = raw_r.get("form") or raw_r.get("form_string") or raw_r.get("recent_form") or ""
        form_parsed = parse_form_string(form_str)

        market_odds = _extract_odds(raw_r)
        implied_prob = (1.0 / market_odds) if market_odds and market_odds > 1 else 0.0

        number = 0
        try:
            number = int(raw_r.get("number") or raw_r.get("cloth_number") or raw_r.get("saddleCloth") or 0)
        except (ValueError, TypeError):
            pass

        runner_dict: Dict[str, Any] = {
            "horse": horse_name,
            "horse_raw": raw_r.get("horse") or raw_r.get("name") or "",
            "horse_confidence": horse_conf,
            "number": number,
            "barrier": barrier,
            "jockey": jockey_name,
            "jockey_raw": raw_r.get("jockey", ""),
            "trainer": trainer_name,
            "trainer_raw": raw_r.get("trainer", ""),
            "weight_kg": weight_kg,
            "age_years": age_years,
            "sex": sex,
            "form": form_str,
            "form_parsed": form_parsed,
            "market_odds": market_odds,
            "implied_prob": round(implied_prob, 4),
            "_stats": raw_r.get("stats", {}),
        }

        runner_dict["run_style"] = infer_run_style(raw_r)

        active_runners.append(runner_dict)

    race_dict["field_size"] = len(active_runners)

    flags = validate_race(race_dict, active_runners)
    valid = is_race_valid(flags)

    pace_map, per_runner_pace = build_pace_map(active_runners, race_dict)

    for i, r in enumerate(active_runners):
        r["pace_features"] = per_runner_pace[i] if i < len(per_runner_pace) else {}

    for r in active_runners:
        r["derived_features"] = engineer_features(r, active_runners, race_dict)

    for r in active_runners:
        horse = r.get("horse", "")
        r["runner_flags"] = [f["code"] for f in flags if f.get("runner") == horse]

    dist_cat = _distance_category(distance_m)
    field_size = len(active_runners)
    race_features = {
        "distance_category": dist_cat,
        "field_size_category": "small" if field_size <= 8 else "large" if field_size >= 14 else "medium",
        "is_sprint": dist_cat == "sprint",
        "is_staying": dist_cat == "staying",
        "going_speed_factor": 1.0 - (going["rating_0_10"] - 4.0) * 0.03,  # slower on heavy
        "class_quality_tier": class_info["quality_tier"],
    }

    for r in active_runners:
        r.pop("_stats", None)

    llm_enrichment = None
    if enable_llm:
        result_so_far = {
            "race": race_dict,
            "runners": active_runners,
            "pace_map": pace_map,
        }
        llm_enrichment = llm_enrich(result_so_far)
        if llm_enrichment:
            corrections = llm_enrichment.get("style_corrections", {})
            for r in active_runners:
                corrected = corrections.get(r["horse"])
                if corrected and corrected in RUN_STYLE_ENUM:
                    r["run_style"]["bucket"] = corrected
                    r["run_style"]["source"] = "llm_corrected"

    return {
        "meta": {
            "normaliser_version": VERSION,
            "normalised_at": now,
            "llm_enriched": llm_enrichment is not None and bool(llm_enrichment),
        },
        "race": race_dict,
        "validation": {
            "is_valid": valid,
            "flags": flags,
        },
        "pace_map": pace_map,
        "race_features": race_features,
        "runners": active_runners,
        "candidate_features_to_test": CANDIDATE_FEATURES_TO_TEST,
        "llm_enrichment": llm_enrichment,
    }


def normalise_races(raw_races: List[Dict[str, Any]], enable_llm: bool = False) -> List[Dict[str, Any]]:
    """Batch normalise multiple races."""
    return [normalise_race(r, enable_llm=enable_llm) for r in raw_races]




def _self_test():
    """Run self-tests with synthetic data."""
    print("=" * 60)
    print("Race Normaliser — Self-Test")
    print("=" * 60)
    errors = 0

    going_tests = [
        ("Good 4", "GOOD", 4.0),
        ("Soft 7", "SOFT", 7.0),
        ("Heavy 9", "HEAVY", 9.0),
        ("Heavy", "HEAVY", 9.0),
        ("Firm 1", "FIRM", 1.0),
        ("Yielding", "SOFT", 6.5),
        ("Dead 5", "GOOD", 5.0),
        ("Synthetic", "SYNTHETIC", 5.0),
        ("Polytrack", "SYNTHETIC", 5.0),
        ("All Weather", "SYNTHETIC", 5.0),
        ("Boggy", "HEAVY", 9.0),
        ("", "UNKNOWN", 4.0),
        (None, "UNKNOWN", 4.0),
        ("Good to Firm", "FIRM", 1.5),
    ]
    print("\n[Going Canonicalisation]")
    for raw, expected_canon, expected_rating in going_tests:
        result = canonicalise_going(raw)
        ok = result["canonical"] == expected_canon
        status = "PASS" if ok else "FAIL"
        if not ok:
            errors += 1
        print(f"  {status}: '{raw}' -> {result['canonical']} (rating {result['rating_0_10']}) "
              f"{'' if ok else f'expected {expected_canon}'}")

    print("\n[Name Normalisation]")
    name_tests = [
        ("WINX", "horse", "Winx"),
        ("  james mcdonald  ", "jockey", "James Mcdonald"),
        ("Chris\u2019s Pride", "horse", "Chris's Pride"),
        ("", "horse", ""),
    ]
    for raw, ntype, expected in name_tests:
        canon, conf = normalise_name(raw, ntype)
        ok = canon == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            errors += 1
        print(f"  {status}: '{raw}' ({ntype}) -> '{canon}' (conf={conf}) "
              f"{'' if ok else f'expected {expected}'}")

    print("\n[Class Level]")
    class_tests = [
        ("BM65", 58, "benchmark"),
        ("MDN", 30, "maiden"),
        ("G1", 100, "group"),
        ("LISTED", 85, "stakes"),
    ]
    for raw, expected_level, expected_tier in class_tests:
        result = unify_class_level(raw)
        ok = result["level_0_100"] == expected_level and result["quality_tier"] == expected_tier
        status = "PASS" if ok else "FAIL"
        if not ok:
            errors += 1
        print(f"  {status}: '{raw}' -> level={result['level_0_100']}, tier={result['quality_tier']} "
              f"{'' if ok else f'expected {expected_level}/{expected_tier}'}")

    print("\n[Full Race Normalisation]")
    test_race = {
        "course": "Tab Randwick",
        "race_number": 5,
        "race_name": "Test Stakes",
        "distance": "1400m",
        "going": "Soft 6",
        "class": "BM72",
        "prize_total": 75000,
        "raceDate": "2026-03-04",
        "runners": [
            {"horse": "Leader Boy", "draw": 2, "form": "11213", "jockey": "J McDonald",
             "trainer": "C Waller", "weight": "58kg", "age": "5yo", "sex": "G",
             "odds": [{"win_odds": "3.50"}], "running_style": "front runner"},
            {"horse": "On Pace Girl", "draw": 5, "form": "23121", "jockey": "H Bowman",
             "trainer": "G Waterhouse", "weight": "55.5kg", "age": "4yo", "sex": "M",
             "odds": [{"win_odds": "6.00"}]},
            {"horse": "Midfield Star", "draw": 8, "form": "43524", "jockey": "T Berry",
             "trainer": "J Cummings", "weight": "57kg", "age": "6yo", "sex": "G",
             "odds": [{"win_odds": "8.00"}]},
            {"horse": "Backmarker Dan", "draw": 12, "form": "76531", "jockey": "N Rawiller",
             "trainer": "P Payne", "weight": "54kg", "age": "4yo", "sex": "G",
             "odds": [{"win_odds": "12.00"}], "running_style": "backmarker"},
            {"horse": "Scratched One", "draw": 3, "form": "11111", "jockey": "Test",
             "trainer": "Test", "weight": "56kg", "age": "3yo", "scratched": True},
        ],
    }

    result = normalise_race(test_race, enable_llm=False)

    assert result["meta"]["normaliser_version"] == VERSION
    assert result["race"]["track"] == "Randwick"
    assert result["race"]["distance_m"] == 1400
    assert result["race"]["going"]["canonical"] == "SOFT"
    assert result["race"]["going"]["rating_0_10"] == 6.0
    assert result["race"]["class"]["level_0_100"] == 64
    assert result["race"]["field_size"] == 4
    assert len(result["runners"]) == 4
    assert result["validation"]["is_valid"] is True

    r0 = result["runners"][0]
    assert r0["horse"] == "Leader Boy"
    assert r0["barrier"] == 2
    assert r0["weight_kg"] == 58.0
    assert r0["run_style"]["bucket"] == "LEAD"
    assert r0["market_odds"] == 3.50

    pm = result["pace_map"]
    assert "Leader Boy" in pm["likely_leaders"]
    assert pm["expected_pressure"] in ("LOW", "MEDIUM", "HIGH", "UNKNOWN")

    df = r0["derived_features"]
    assert "barrier_field_ratio" in df
    assert "form_recency_score" in df

    print("  PASS: Full race normalisation structure OK")
    print(f"  Runners: {[r['horse'] for r in result['runners']]}")
    print(f"  Going: {result['race']['going']}")
    print(f"  Pace: tempo={pm['predicted_tempo']}, pressure={pm['expected_pressure']}")
    print(f"  Leaders: {pm['likely_leaders']}")
    print(f"  Validation flags: {len(result['validation']['flags'])}")
    for f in result["validation"]["flags"]:
        print(f"    {f['severity']}: {f['code']} — {f['message']}")

    print("\n[Validation Edge Cases]")

    dup_race = {
        "course": "Flemington", "race_number": 1, "distance": "1200m",
        "going": "Good 3", "class": "MDN",
        "runners": [
            {"horse": "Duplicate Horse", "draw": 1, "form": "321", "weight": "56kg", "age": "3"},
            {"horse": "Duplicate Horse", "draw": 5, "form": "543", "weight": "56kg", "age": "3"},
            {"horse": "Normal Horse", "draw": 3, "form": "123", "weight": "56kg", "age": "3"},
        ],
    }
    dup_result = normalise_race(dup_race)
    has_dup_flag = any(f["code"] == "DUPLICATE_RUNNER" for f in dup_result["validation"]["flags"])
    assert has_dup_flag, "Should flag duplicate runner"
    assert not dup_result["validation"]["is_valid"], "Should be invalid with duplicate"
    print("  PASS: Duplicate runner flagged as CRITICAL")

    barrier_race = {
        "course": "Caulfield", "race_number": 2, "distance": "1600m",
        "going": "Good", "class": "BM70",
        "runners": [
            {"horse": "Horse A", "draw": 1, "form": "1", "weight": "56kg", "age": "4"},
            {"horse": "Horse B", "draw": 2, "form": "2", "weight": "56kg", "age": "4"},
            {"horse": "Horse C", "draw": 20, "form": "3", "weight": "56kg", "age": "4"},
        ],
    }
    barrier_result = normalise_race(barrier_race)
    has_barrier_flag = any(f["code"] == "BARRIER_OUT_OF_RANGE" for f in barrier_result["validation"]["flags"])
    assert has_barrier_flag, "Should flag barrier 20 in 3-horse field"
    print("  PASS: Barrier out of range flagged")

    print(f"\n{'=' * 60}")
    if errors == 0:
        print("All self-tests passed!")
    else:
        print(f"{errors} test(s) FAILED")
    print("=" * 60)

    return errors == 0



if __name__ == "__main__":
    if "--test" in sys.argv:
        success = _self_test()
        sys.exit(0 if success else 1)
    elif len(sys.argv) > 1:
        racecard_path = Path(sys.argv[1])
        if not racecard_path.exists():
            print(f"File not found: {racecard_path}", file=sys.stderr)
            sys.exit(1)
        with open(racecard_path) as f:
            racecard = json.load(f)
        all_results = []
        for meet in racecard:
            for race in meet.get("races", []):
                race["course"] = meet.get("course", "")
                result = normalise_race(race)
                all_results.append(result)
        print(json.dumps(all_results, indent=2, default=str))
    else:
        print("Usage:")
        print("  py race_normaliser.py --test")
        print("  py race_normaliser.py <racecard.json>")
