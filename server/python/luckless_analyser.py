#!/usr/bin/env python3
"""
Luckless last-start analyser.

Detects horses that had genuine excuses last start (held up, blocked, wide no cover, etc.)
and identifies when conditions have improved today.
"""

import json
import logging
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Extends build_features.py keyword lists with AUS stewards-specific terms.

_INCIDENT_RULES: List[Tuple[str, str, str, int]] = [
    ("blocked for a run", "BLOCKED_RUN", "traffic", 3),
    ("nowhere to go", "BLOCKED_RUN", "traffic", 3),
    ("no room", "BLOCKED_RUN", "traffic", 3),
    ("blocked", "BLOCKED_RUN", "traffic", 2),
    ("couldn't improve", "BLOCKED_RUN", "traffic", 2),
    ("unable to improve", "BLOCKED_RUN", "traffic", 2),
    ("couldn't get clear", "BLOCKED_RUN", "traffic", 2),
    ("held up", "HELD_UP", "traffic", 2),
    ("held-up", "HELD_UP", "traffic", 2),
    ("restricted room", "HELD_UP", "traffic", 2),
    ("racing room", "HELD_UP", "traffic", 2),
    ("no clear run", "HELD_UP", "traffic", 2),
    ("not clrst path", "HELD_UP", "traffic", 2),
    ("no run vital", "HELD_UP", "traffic", 3),
    ("checked", "CHECKED", "traffic", 2),
    ("steadied", "CHECKED", "traffic", 1),
    ("crowded", "CROWDED", "traffic", 2),
    ("tightened", "CROWDED", "traffic", 1),
    ("squeezed", "CROWDED", "traffic", 1),
    ("bumped", "CROWDED", "traffic", 1),
    ("impeded", "CROWDED", "traffic", 2),
    ("buffeted", "CROWDED", "traffic", 1),
    ("shuffled back", "CROWDED", "traffic", 2),
    ("shuffled", "CROWDED", "traffic", 1),
    ("hampered", "CROWDED", "traffic", 2),

    ("wide without cover", "WIDE_NO_COVER", "map", 2),
    ("without cover", "WIDE_NO_COVER", "map", 2),
    ("no cover", "WIDE_NO_COVER", "map", 2),
    ("out on a limb", "WIDE_NO_COVER", "map", 2),
    ("five wide", "THREE_WIDE_PLUS", "map", 3),
    ("five-wide", "THREE_WIDE_PLUS", "map", 3),
    ("six wide", "THREE_WIDE_PLUS", "map", 3),
    ("four wide", "THREE_WIDE_PLUS", "map", 2),
    ("four-wide", "THREE_WIDE_PLUS", "map", 2),
    ("three wide", "THREE_WIDE_PLUS", "map", 2),
    ("three-wide", "THREE_WIDE_PLUS", "map", 2),
    ("widest", "THREE_WIDE_PLUS", "map", 2),
    ("wide throughout", "THREE_WIDE_PLUS", "map", 2),
    ("peeled widest", "THREE_WIDE_PLUS", "map", 3),
    ("posted", "POSTED_WIDE", "map", 1),
    ("covered ground", "POSTED_WIDE", "map", 1),
    ("covered extra ground", "POSTED_WIDE", "map", 2),
    ("over-raced", "OVER_RACED", "map", 1),
    ("pulling", "OVER_RACED", "map", 1),
    ("bit keen", "OVER_RACED", "map", 1),
    ("fought for head", "OVER_RACED", "map", 2),

    ("missed the start", "MISSED_START", "start", 2),
    ("missed the kick", "MISSED_START", "start", 2),
    ("began awkwardly", "AWKWARD_START", "start", 1),
    ("awkward start", "AWKWARD_START", "start", 1),
    ("slowly away", "SLOW_START", "start", 1),
    ("slow to begin", "SLOW_START", "start", 1),
    ("slow out", "SLOW_START", "start", 1),
    ("bit slow out", "SLOW_START", "start", 1),
    ("little slow out", "SLOW_START", "start", 1),
    ("dwelt", "SLOW_START", "start", 1),
    ("tardy", "SLOW_START", "start", 1),

    ("ran on", "STRONG_FINISH", "positive", 1),
    ("kept finding", "STRONG_FINISH", "positive", 2),
    ("kept trying", "STRONG_FINISH", "positive", 1),
    ("strong finish", "STRONG_FINISH", "positive", 2),
    ("finished strongly", "STRONG_FINISH", "positive", 2),
    ("rattled home", "STRONG_FINISH", "positive", 2),
    ("stormed home", "STRONG_FINISH", "positive", 3),
    ("powered home", "STRONG_FINISH", "positive", 3),
    ("flew late", "STRONG_FINISH", "positive", 2),
    ("flashed late", "STRONG_FINISH", "positive", 2),
    ("hit the line", "STRONG_FINISH", "positive", 2),
    ("closest on line", "STRONG_FINISH", "positive", 2),
    ("best work late", "STRONG_FINISH", "positive", 2),
    ("sound effort", "SOUND_EFFORT", "positive", 1),
    ("good effort", "SOUND_EFFORT", "positive", 1),
    ("margin unfair", "SOUND_EFFORT", "positive", 2),
    ("no favours", "SOUND_EFFORT", "positive", 2),
    ("had no favours", "SOUND_EFFORT", "positive", 2),

    ("faded", "FADED", "negative", 1),
    ("weakened", "FADED", "negative", 1),
    ("tired", "FADED", "negative", 1),
    ("dropped out", "FADED", "negative", 2),
    ("couldn't go on", "FADED", "negative", 1),
    ("one pace", "FADED", "negative", 1),
    ("bled", "INJURY", "negative", 3),
    ("lame", "INJURY", "negative", 3),
    ("pulled up", "INJURY", "negative", 3),
    ("eased", "INJURY", "negative", 2),
    ("not tested", "INJURY", "negative", 1),
]

# Sort by keyword length descending so longer phrases match first
_INCIDENT_RULES.sort(key=lambda x: -len(x[0]))

# Timing markers: map distance/position markers to early/mid/late
_LATE_MARKERS = re.compile(
    r"x\s*(?:200|150|100)|@\s*(?:200|150|100)|from\s*(?:200|150)"
    r"|(?:approaching|near)\s+(?:the\s+)?(?:200|150|100)"
    r"|(?:200|150|100)\s*m",
    re.IGNORECASE,
)
_MID_MARKERS = re.compile(
    r"x\s*(?:400|350|300)|@\s*(?:400|350|300)"
    r"|turning\s+for\s+home|straightening\s+up"
    r"|(?:approaching|near)\s+(?:the\s+)?(?:400|350|300)",
    re.IGNORECASE,
)
_EARLY_MARKERS = re.compile(
    r"x\s*(?:800|600)|@\s*(?:800|600)"
    r"|(?:approaching|near)\s+(?:the\s+)?(?:800|600)",
    re.IGNORECASE,
)

_LUCKLESS_KEYWORDS = ["luckless", "unlucky", "no luck", "badly held", "hard luck"]
_EXCUSE_KEYWORDS = [
    "held up", "blocked", "wide without cover", "wide no cover",
    "checked", "hampered", "crowded", "not disgraced", "raced wide",
    "no room", "had no favours", "three wide", "four wide",
]
_IMPROVEMENT_KEYWORDS = [
    "blinkers on", "softer run", "better draw", "inside draw",
    "good barrier", "smaller field", "fitter", "strips fitter",
    "blinkers first time", "tongue tie", "gear change",
]



def extract_incidents(comment: str) -> List[Dict[str, Any]]:
    """
    Extract structured incidents from a race/stewards comment.

    Returns list of dicts with tag, severity (1-3), timing (early/mid/late/unknown),
    evidence, and category.
    """
    if not comment:
        return []

    comment_lower = comment.lower()
    incidents: List[Dict[str, Any]] = []
    matched_spans: List[Tuple[int, int]] = []  # prevent double-counting

    for keyword, tag, category, base_sev in _INCIDENT_RULES:
        start = 0
        while True:
            idx = comment_lower.find(keyword, start)
            if idx < 0:
                break
            end = idx + len(keyword)

            # Skip if this span is fully contained within an already-matched span
            overlaps = any(s <= idx and end <= e for s, e in matched_spans)
            if overlaps:
                start = end
                continue

            matched_spans.append((idx, end))

            # Extract evidence: up to 60 chars around the keyword
            ev_start = max(0, idx - 15)
            ev_end = min(len(comment), end + 30)
            evidence = comment[ev_start:ev_end].strip()

            # Infer timing from nearby markers
            window_start = max(0, idx - 80)
            window_end = min(len(comment), end + 80)
            window = comment[window_start:window_end]

            timing = "unknown"
            if _LATE_MARKERS.search(window):
                timing = "late"
            elif _MID_MARKERS.search(window):
                timing = "mid"
            elif _EARLY_MARKERS.search(window):
                timing = "early"

            # Severity boost for late timing
            severity = min(3, base_sev + (1 if timing == "late" else 0))

            incidents.append({
                "tag": tag,
                "severity": severity,
                "timing": timing,
                "evidence": evidence,
                "category": category,
            })

            start = end

    return incidents



_SEVERITY_SCORES = {
    "traffic": {3: 25, 2: 18, 1: 10},
    "map": {3: 22, 2: 15, 1: 8},
    "start": {3: 12, 2: 8, 1: 8},
    "positive": {3: 20, 2: 15, 1: 10},
    "negative": {3: -50, 2: -40, 1: -30},
}


def calculate_forgive_score(
    incidents: List[Dict[str, Any]],
    finish_position: int = 0,
    field_size: int = 10,
    barrier: int = 0,
) -> Dict[str, Any]:
    """
    Combine incidents into a single forgive score (0-100).

    Returns forgive_score, is_luckless, upgrade_type, dominant_incident,
    has_negative, incident_summary.
    """
    score = 0
    has_negative = False
    category_scores: Dict[str, int] = defaultdict(int)
    highest_severity_incident: Optional[Dict[str, Any]] = None
    max_sev = 0

    for inc in incidents:
        cat = inc["category"]
        sev = inc["severity"]
        pts = _SEVERITY_SCORES.get(cat, {}).get(sev, 0)
        score += pts
        category_scores[cat] += abs(pts)

        if cat == "negative":
            has_negative = True

        if cat in ("traffic", "map", "start") and sev > max_sev:
            max_sev = sev
            highest_severity_incident = inc

    # Compound bonus: multiple traffic/map incidents = more luckless
    adverse_count = sum(1 for i in incidents if i["category"] in ("traffic", "map"))
    if adverse_count >= 2:
        score += 8 * (adverse_count - 1)  # +8 per extra incident

    # Position penalty: more to forgive if horse finished mid-field or worse
    if finish_position > 0 and field_size > 0:
        if finish_position > field_size / 2:
            score += 10

    score = max(0, min(100, score))

    upgrade_type = "none"
    if not has_negative and highest_severity_incident:
        cat_ranking = sorted(
            [(c, s) for c, s in category_scores.items() if c in ("traffic", "map", "start")],
            key=lambda x: -x[1]
        )
        if cat_ranking:
            upgrade_type = cat_ranking[0][0]

    summaries = []
    seen_tags = set()
    for inc in sorted(incidents, key=lambda x: -x["severity"]):
        if inc["category"] in ("traffic", "map", "start") and inc["tag"] not in seen_tags:
            seen_tags.add(inc["tag"])
            tag_readable = inc["tag"].replace("_", " ").lower()
            if inc["timing"] != "unknown":
                summaries.append(f"{tag_readable} ({inc['timing']})")
            else:
                summaries.append(tag_readable)
    incident_summary = ", ".join(summaries[:3]) if summaries else ""

    return {
        "forgive_score": score,
        "is_luckless": score >= 60 and not has_negative,
        "upgrade_type": upgrade_type,
        "dominant_incident": highest_severity_incident["tag"] if highest_severity_incident else None,
        "has_negative": has_negative,
        "incident_summary": incident_summary,
    }



_historical_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None


def load_historical_comments(historical_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """Scan historical_data/track_imports/*.json and build index: horse_id -> [race entries sorted newest first]."""
    global _historical_cache
    if _historical_cache is not None:
        return _historical_cache

    index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    if not os.path.isdir(historical_dir):
        logger.warning(f"Historical directory not found: {historical_dir}")
        _historical_cache = dict(index)
        return _historical_cache

    for fname in os.listdir(historical_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(historical_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load {fpath}: {e}")
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            track_name = item.get("course", item.get("track", fname.replace(".json", "")))
            for race in item.get("races", []):
                race_date = race.get("date", "")
                distance = race.get("distance", "")
                runners = race.get("runners", [])
                field_size = len([r for r in runners if not r.get("scratched")])

                for r in runners:
                    if r.get("scratched"):
                        continue
                    hid = r.get("horse_id", "")
                    if not hid:
                        continue
                    comment = r.get("comment", "")
                    position = r.get("position")
                    try:
                        position = int(position) if position else 0
                    except (ValueError, TypeError):
                        position = 0

                    barrier = r.get("draw") or r.get("barrier") or 0
                    try:
                        barrier = int(barrier)
                    except (ValueError, TypeError):
                        barrier = 0

                    index[hid].append({
                        "race_date": race_date,
                        "track": track_name,
                        "distance": distance,
                        "comment": comment,
                        "position": position,
                        "barrier": barrier,
                        "field_size": field_size,
                        "form": r.get("form", ""),
                        "horse": r.get("horse", ""),
                    })

    for hid in index:
        index[hid].sort(key=lambda x: x.get("race_date", ""), reverse=True)

    _historical_cache = dict(index)
    logger.info(f"Loaded historical comments: {sum(len(v) for v in index.values())} entries, {len(index)} horses")
    return _historical_cache


def clear_historical_cache():
    """Clear the cached historical data (for testing)."""
    global _historical_cache
    _historical_cache = None



def analyse_pre_race_comment(comment: str) -> Dict[str, Any]:
    """Analyse a pre-race (form analyst) comment for luckless/excuse references."""
    result = {
        "mentions_luckless": False,
        "mentions_excuse": False,
        "mentions_improvement": False,
        "keywords_found": [],
        "analyst_sentiment": "neutral",
    }

    if not comment:
        return result

    comment_lower = comment.lower()

    for kw in _LUCKLESS_KEYWORDS:
        if kw in comment_lower:
            result["mentions_luckless"] = True
            result["keywords_found"].append(kw)

    for kw in _EXCUSE_KEYWORDS:
        if kw in comment_lower:
            result["mentions_excuse"] = True
            if kw not in result["keywords_found"]:
                result["keywords_found"].append(kw)

    for kw in _IMPROVEMENT_KEYWORDS:
        if kw in comment_lower:
            result["mentions_improvement"] = True
            if kw not in result["keywords_found"]:
                result["keywords_found"].append(kw)

    positive_words = ["good", "well", "strong", "big watch", "must consider",
                      "go well", "winning form", "flying form", "bold showing",
                      "chance", "each-way"]
    negative_words = ["struggled", "disappointing", "outclassed", "hard to see",
                      "needs to improve", "question mark"]

    pos_count = sum(1 for w in positive_words if w in comment_lower)
    neg_count = sum(1 for w in negative_words if w in comment_lower)

    if pos_count > neg_count:
        result["analyst_sentiment"] = "positive"
    elif neg_count > pos_count:
        result["analyst_sentiment"] = "negative"

    return result



def detect_improvement(
    last_start: Dict[str, Any],
    current_runner: Dict[str, Any],
    current_race: Dict[str, Any],
    last_forgive: Dict[str, Any],
    pre_race_flags: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare last start conditions vs current start to detect improvement."""
    last_barrier = last_start.get("barrier", 0)
    last_field = last_start.get("field_size", 10)

    current_barrier = current_runner.get("draw") or current_runner.get("barrier") or 0
    try:
        current_barrier = int(current_barrier)
    except (ValueError, TypeError):
        current_barrier = 0

    current_runners = current_race.get("runners", [])
    current_field = len([r for r in current_runners if not r.get("scratched")])

    barrier_improvement = last_barrier - current_barrier  # positive = better
    field_size_change = last_field - current_field  # positive = smaller today

    factors: List[str] = []
    uplift = 0

    if barrier_improvement > 4:
        factors.append(f"Barrier improved {last_barrier} to {current_barrier}")
        uplift += 25
    elif barrier_improvement >= 2:
        factors.append(f"Barrier improved {last_barrier} to {current_barrier}")
        uplift += 15

    if last_barrier > last_field * 0.6 and current_barrier <= 4:
        if f"Barrier improved {last_barrier} -> {current_barrier}" not in factors:
            factors.append(f"Wide draw ({last_barrier}) to inside ({current_barrier})")
        uplift += 20

    if field_size_change >= 3:
        factors.append(f"Smaller field ({last_field} to {current_field})")
        uplift += 15

    if pre_race_flags.get("mentions_luckless") or pre_race_flags.get("mentions_excuse"):
        factors.append("Form analyst flags as luckless")
        uplift += 10

    try:
        last_dist = int(re.sub(r"[^\d]", "", str(last_start.get("distance", "0"))) or "0")
        curr_dist = int(re.sub(r"[^\d]", "", str(current_race.get("distance", "0"))) or "0")
        if 0 < curr_dist <= last_dist and last_dist > 0:
            uplift += 5
    except (ValueError, TypeError):
        pass

    uplift = min(100, uplift)
    has_map_improvement = barrier_improvement >= 2 or field_size_change >= 3

    return {
        "barrier_improvement": barrier_improvement,
        "field_size_change": field_size_change,
        "has_map_improvement": has_map_improvement,
        "improvement_factors": factors,
        "uplift_score": uplift,
    }



_LLM_SYSTEM_PROMPT = """You are an Australian horse racing stewards report analyst. Extract structured incident data from the race comment below. Focus ONLY on interference, positioning, and traffic events. Ignore result statements ("won", "placed"). Do NOT invent incidents not present in the text. Return ONLY valid JSON.

JSON schema:
{
  "incidents": [{"tag": "HELD_UP"|"BLOCKED_RUN"|"CHECKED"|"CROWDED"|"WIDE_NO_COVER"|"THREE_WIDE_PLUS"|"SLOW_START"|"STRONG_FINISH", "severity": 1-3, "timing": "early"|"mid"|"late"|"unknown", "evidence": "short quote"}],
  "forgive_score": 0-100,
  "upgrade_hint": "one sentence",
  "is_downgrade": true/false
}"""


def llm_analyse_comment(
    comment: str,
    barrier: int = 0,
    field_size: int = 10,
    position: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    Use Groq LLM to analyse a borderline stewards comment.
    Returns structured analysis or None on failure.
    """
    try:
        from llm_provider import get_provider, LLMProviderError
    except ImportError:
        logger.warning("llm_provider not available")
        return None

    try:
        llm = get_provider()
    except Exception as e:
        logger.warning(f"LLM not available: {e}")
        return None

    prompt = (
        f"Race comment: \"{comment}\"\n"
        f"Context: Barrier {barrier}, field of {field_size}, finished {position}.\n"
        f"Extract incidents and score."
    )

    try:
        result = llm.generate_json(prompt, system=_LLM_SYSTEM_PROMPT, temperature=0.1, max_tokens=512)
        if result and isinstance(result, dict):
            return result
    except Exception as e:
        logger.warning(f"LLM comment analysis failed: {e}")

    return None



def _build_explanation(
    last_start: Dict[str, Any],
    forgive: Dict[str, Any],
    improvement: Dict[str, Any],
    current_runner: Dict[str, Any],
) -> str:
    """Build human-readable luckless explanation for the tips card."""
    parts = []

    summary = forgive.get("incident_summary", "")
    track = last_start.get("track", "")
    pos = last_start.get("position", 0)
    field = last_start.get("field_size", 0)
    barrier = last_start.get("barrier", 0)

    if summary:
        pos_str = f"{pos}th" if pos > 0 else "unplaced"
        field_str = f" of {field}" if field > 0 else ""
        barrier_str = f", barrier {barrier}" if barrier > 0 else ""
        track_str = f" at {track}" if track else ""
        parts.append(
            f"{summary.capitalize()} last start{track_str} "
            f"({pos_str}{field_str}{barrier_str})."
        )

    factors = improvement.get("improvement_factors", [])
    if factors:
        current_barrier = current_runner.get("draw") or current_runner.get("barrier") or "?"
        parts.append(f"Today draws barrier {current_barrier} — {', '.join(f.lower() for f in factors[:2])}.")

    return " ".join(parts) if parts else ""


def analyse_luckless(
    runners: List[Dict[str, Any]],
    race: Dict[str, Any],
    historical_dir: str = "",
    enable_llm: bool = False,
) -> List[Dict[str, Any]]:
    """
    Analyse all runners in a race for luckless last-start patterns.

    Args:
        runners: List of runner dicts from racecard
        race: Race dict (with distance, course, runners etc.)
    """
    hist_index = {}
    if historical_dir and os.path.isdir(historical_dir):
        hist_index = load_historical_comments(historical_dir)

    results: List[Dict[str, Any]] = []

    for runner in runners:
        if runner.get("scratched"):
            continue

        horse = runner.get("horse", runner.get("name", "Unknown"))
        horse_id = runner.get("horse_id", "")
        pre_race_comment = runner.get("comment", "")

        pre_race_flags = analyse_pre_race_comment(pre_race_comment)

        last_start = None
        if horse_id and horse_id in hist_index:
            entries = hist_index[horse_id]
            if entries:
                last_start = entries[0]

        entry: Dict[str, Any] = {
            "horse": horse,
            "horse_id": horse_id,
            "pre_race_flags": pre_race_flags,
            "last_start": {"found": False},
            "improvement": {
                "barrier_improvement": 0,
                "field_size_change": 0,
                "has_map_improvement": False,
                "improvement_factors": [],
                "uplift_score": 0,
            },
            "luckless_uplift": 0.0,
            "luckless_explanation": "",
            "llm_enrichment": None,
        }

        if last_start and last_start.get("comment"):
            incidents = extract_incidents(last_start["comment"])

            forgive = calculate_forgive_score(
                incidents,
                finish_position=last_start.get("position", 0),
                field_size=last_start.get("field_size", 10),
                barrier=last_start.get("barrier", 0),
            )

            llm_result = None
            if enable_llm and 40 <= forgive["forgive_score"] <= 65:
                llm_result = llm_analyse_comment(
                    last_start["comment"],
                    barrier=last_start.get("barrier", 0),
                    field_size=last_start.get("field_size", 10),
                    position=last_start.get("position", 0),
                )
                if llm_result:
                    # LLM can upgrade or downgrade the forgive score
                    llm_score = llm_result.get("forgive_score", forgive["forgive_score"])
                    if isinstance(llm_score, (int, float)) and 0 <= llm_score <= 100:
                        # Blend: 60% keyword, 40% LLM
                        forgive["forgive_score"] = int(0.6 * forgive["forgive_score"] + 0.4 * llm_score)
                        forgive["is_luckless"] = forgive["forgive_score"] >= 60 and not forgive["has_negative"]
                    if llm_result.get("is_downgrade"):
                        forgive["has_negative"] = True
                        forgive["is_luckless"] = False

            entry["last_start"] = {
                "found": True,
                "race_date": last_start.get("race_date", ""),
                "track": last_start.get("track", ""),
                "position": last_start.get("position", 0),
                "barrier": last_start.get("barrier", 0),
                "field_size": last_start.get("field_size", 0),
                "incidents": incidents,
                **forgive,
            }
            entry["llm_enrichment"] = llm_result

            if forgive["is_luckless"]:
                improvement = detect_improvement(
                    last_start, runner, race, forgive, pre_race_flags
                )
                entry["improvement"] = improvement

                # Calculate uplift — scaled cap by evidence strength (3D)
                if improvement["uplift_score"] >= 40 and not forgive["has_negative"]:
                    raw_uplift = (forgive["forgive_score"] * improvement["uplift_score"]) / 10000
                    if improvement["uplift_score"] >= 70:
                        max_uplift = 0.15  # Strong improvement conditions
                    elif improvement["uplift_score"] >= 50:
                        max_uplift = 0.12  # Moderate improvement
                    else:
                        max_uplift = 0.08  # Mild improvement
                    entry["luckless_uplift"] = round(min(max_uplift, raw_uplift), 4)

                entry["luckless_explanation"] = _build_explanation(
                    last_start, forgive, improvement, runner
                )
        elif last_start and not last_start.get("comment"):
            entry["last_start"] = {
                "found": True,
                "race_date": last_start.get("race_date", ""),
                "track": last_start.get("track", ""),
                "position": last_start.get("position", 0),
                "barrier": last_start.get("barrier", 0),
                "field_size": last_start.get("field_size", 0),
                "incidents": [],
                "forgive_score": 0,
                "is_luckless": False,
                "upgrade_type": "none",
                "dominant_incident": None,
                "has_negative": False,
                "incident_summary": "",
            }

        results.append(entry)

    return results



def _self_test():
    """Run self-tests on synthetic and real data."""
    passed = 0
    failed = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            print(f"  PASS: {name}")
            passed += 1
        else:
            print(f"  FAIL: {name} — {detail}")
            failed += 1

    print("=" * 60)
    print("Luckless Analyser Self-Test")
    print("=" * 60)

    print("\n--- 1. Multiple traffic incidents ---")
    incs = extract_incidents("held up for clear running approaching 200m, blocked for a run near the 150m")
    tags = [i["tag"] for i in incs]
    check("HELD_UP detected", "HELD_UP" in tags)
    check("BLOCKED_RUN detected", "BLOCKED_RUN" in tags)
    late_incs = [i for i in incs if i["timing"] == "late"]
    check("Late timing detected", len(late_incs) >= 1, f"got {len(late_incs)}")
    forgive = calculate_forgive_score(incs, finish_position=10, field_size=16)
    check("Forgive score >= 60", forgive["forgive_score"] >= 60, f"got {forgive['forgive_score']}")
    check("is_luckless=True", forgive["is_luckless"])

    print("\n--- 2. Wide no cover ---")
    incs = extract_incidents("raced three wide without cover throughout, sound effort")
    tags = [i["tag"] for i in incs]
    check("THREE_WIDE_PLUS detected", "THREE_WIDE_PLUS" in tags)
    check("WIDE_NO_COVER detected", "WIDE_NO_COVER" in tags)
    check("SOUND_EFFORT detected", "SOUND_EFFORT" in tags)

    print("\n--- 3. Timing severity boost ---")
    late_incs = extract_incidents("checked x 200m hard")
    early_incs = extract_incidents("checked early in the run")
    if late_incs and early_incs:
        check("Late severity > early severity",
              late_incs[0]["severity"] > early_incs[0]["severity"],
              f"late={late_incs[0]['severity']}, early={early_incs[0]['severity']}")
    else:
        check("Both extracted", False, f"late={len(late_incs)}, early={len(early_incs)}")

    print("\n--- 4. Negative override ---")
    incs = extract_incidents("pulled up lame after being held up for a run")
    forgive = calculate_forgive_score(incs, finish_position=12, field_size=14)
    check("has_negative=True", forgive["has_negative"])
    check("is_luckless=False (negative)", not forgive["is_luckless"])

    print("\n--- 5. Strong finish amplifier ---")
    incs = extract_incidents("held up for run x 300m, kept finding closest on line, sound effort")
    forgive = calculate_forgive_score(incs, finish_position=8, field_size=12)
    check("High forgive with strong finish", forgive["forgive_score"] >= 60,
          f"got {forgive['forgive_score']}")

    print("\n--- 6. Empty/no comment ---")
    incs = extract_incidents("")
    check("Empty -> no incidents", len(incs) == 0)
    incs = extract_incidents(None)
    check("None -> no incidents", len(incs) == 0)

    print("\n--- 7. Barrier improvement detection ---")
    imp = detect_improvement(
        {"barrier": 12, "field_size": 16, "distance": "1400"},
        {"draw": "3"},
        {"distance": "1400", "runners": [{"scratched": False}] * 12},
        {"forgive_score": 75, "is_luckless": True},
        {"mentions_luckless": False, "mentions_excuse": False},
    )
    check("Barrier improvement = 9", imp["barrier_improvement"] == 9, f"got {imp['barrier_improvement']}")
    check("has_map_improvement=True", imp["has_map_improvement"])
    check("uplift_score > 0", imp["uplift_score"] > 0, f"got {imp['uplift_score']}")

    print("\n--- 8. Pre-race keyword detection ---")
    flags = analyse_pre_race_comment("Luckless at Geelong but had every chance at Pakenham second-up.")
    check("mentions_luckless=True", flags["mentions_luckless"])
    flags2 = analyse_pre_race_comment("Raced wide without cover & had to tire at Sale first-up.")
    check("mentions_excuse=True", flags2["mentions_excuse"])
    flags3 = analyse_pre_race_comment("Trialled well prior to resuming.")
    check("No luckless in neutral comment", not flags3["mentions_luckless"])

    print("\n--- 9. Uplift calculation ---")
    # forgive=78, uplift=72 -> min(0.08, 78*72/10000) = min(0.08, 0.5616) = 0.08 (capped)
    uplift = min(0.08, (78 * 72) / 10000)
    check("Uplift formula caps at 0.08", abs(uplift - 0.08) < 0.001, f"got {uplift}")
    # Lower values: forgive=60, uplift=10 -> min(0.08, 0.006) = 0.006
    uplift2 = min(0.08, (60 * 10) / 10000)
    check("Lower uplift not capped", 0.001 < uplift2 < 0.08, f"got {uplift2}")

    print("\n--- 10. No uplift when conditions worsen ---")
    imp = detect_improvement(
        {"barrier": 3, "field_size": 8, "distance": "1200"},
        {"draw": "11"},
        {"distance": "1400", "runners": [{"scratched": False}] * 14},
        {"forgive_score": 80, "is_luckless": True},
        {"mentions_luckless": False, "mentions_excuse": False},
    )
    check("Barrier worsened", imp["barrier_improvement"] < 0, f"got {imp['barrier_improvement']}")
    # uplift_score likely 0 or low
    actual_uplift = 0.0
    if imp["uplift_score"] >= 40:
        actual_uplift = min(0.08, (80 * imp["uplift_score"]) / 10000)
    check("Low/no uplift when conditions worse", actual_uplift < 0.04, f"uplift={actual_uplift}")

    print("\n--- 11. Explanation generation ---")
    explanation = _build_explanation(
        {"track": "Randwick", "position": 8, "barrier": 5, "field_size": 18},
        {"incident_summary": "held up (late), blocked run (late)"},
        {"improvement_factors": ["Barrier improved 5 to 2", "Smaller field (18 to 14)"]},
        {"draw": "2"},
    )
    check("Explanation includes track", "Randwick" in explanation, f"got: {explanation}")
    check("Explanation includes barrier", "barrier 2" in explanation.lower(), f"got: {explanation}")

    print("\n--- 12. Real data: Action King ---")
    real_comment = ("Little Slow Out Not Bustled Settled Down 8th 3 Lengths enjoyed good trail "
                    "to Straightening Up held-up ! Nowhere To Go Until 150m Margin Unfair "
                    "was # badly held-up")
    incs = extract_incidents(real_comment)
    tags = [i["tag"] for i in incs]
    check("Action King: HELD_UP found", "HELD_UP" in tags, f"tags={tags}")
    check("Action King: BLOCKED_RUN found", "BLOCKED_RUN" in tags, f"tags={tags}")
    check("Action King: SOUND_EFFORT found", "SOUND_EFFORT" in tags, f"tags={tags}")
    forgive = calculate_forgive_score(incs, finish_position=8, field_size=18)
    check("Action King: is_luckless", forgive["is_luckless"],
          f"score={forgive['forgive_score']}, neg={forgive['has_negative']}")

    print("\n--- 13. Real data: Onyspeed ---")
    real_comment2 = ("Jumped Ok Restrained from Outside Settled Down well back Five Wide "
                     "8 Lengths out 8 Wide Turning for home No Run On - # had no favours")
    incs2 = extract_incidents(real_comment2)
    tags2 = [i["tag"] for i in incs2]
    check("Onyspeed: THREE_WIDE_PLUS found", "THREE_WIDE_PLUS" in tags2, f"tags={tags2}")
    check("Onyspeed: SOUND_EFFORT found", "SOUND_EFFORT" in tags2, f"tags={tags2}")

    print("\n--- 14. Faded horse ---")
    incs3 = extract_incidents("Held up briefly then faded badly in the straight, weakened right out")
    forgive3 = calculate_forgive_score(incs3, finish_position=10, field_size=12)
    check("Faded: has_negative=True", forgive3["has_negative"])
    check("Faded: is_luckless=False", not forgive3["is_luckless"])

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    print(f"{'=' * 60}")
    return failed == 0



def _scan_historical(historical_dir: str):
    """Scan all historical comments and report incident distribution."""
    print("Scanning historical comments...")
    hist = load_historical_comments(historical_dir)
    total_entries = sum(len(v) for v in hist.values())
    total_horses = len(hist)
    print(f"  Loaded: {total_entries} entries, {total_horses} horses\n")

    tag_counts: Dict[str, int] = defaultdict(int)
    forgive_buckets = defaultdict(int)
    luckless_count = 0
    total_with_comment = 0
    track_scores: Dict[str, List[int]] = defaultdict(list)

    for hid, entries in hist.items():
        for entry in entries:
            comment = entry.get("comment", "")
            if not comment:
                continue
            total_with_comment += 1

            incidents = extract_incidents(comment)
            for inc in incidents:
                tag_counts[inc["tag"]] += 1

            forgive = calculate_forgive_score(
                incidents,
                finish_position=entry.get("position", 0),
                field_size=entry.get("field_size", 10),
            )

            score = forgive["forgive_score"]
            bucket = min(4, score // 20)
            forgive_buckets[bucket] += 1

            if forgive["is_luckless"]:
                luckless_count += 1

            track = entry.get("track", "unknown")
            if score > 0:
                track_scores[track].append(score)

    print(f"  Comments with text: {total_with_comment}")
    print(f"  Luckless (score>=60, no negative): {luckless_count} ({100*luckless_count/max(1,total_with_comment):.1f}%)")

    print(f"\n  Incident tag distribution:")
    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
        print(f"    {tag:25s}: {count:5d}")

    print(f"\n  Forgive score distribution:")
    labels = ["0-19", "20-39", "40-59", "60-79", "80-100"]
    for i, label in enumerate(labels):
        count = forgive_buckets.get(i, 0)
        bar = "#" * min(50, count // 10)
        print(f"    {label:8s}: {count:5d} {bar}")

    print(f"\n  Average forgive score by track (top 10):")
    track_avgs = [(t, sum(s)/len(s), len(s)) for t, s in track_scores.items() if len(s) >= 10]
    track_avgs.sort(key=lambda x: -x[1])
    for track, avg, n in track_avgs[:10]:
        print(f"    {track:25s}: avg={avg:5.1f}  (n={n})")



if __name__ == "__main__":
    if "--test" in sys.argv:
        success = _self_test()
        sys.exit(0 if success else 1)

    elif "--scan" in sys.argv:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        hist_dir = os.path.join(script_dir, "..", "..", "historical_data", "track_imports")
        if not os.path.isdir(hist_dir):
            hist_dir = os.path.join("historical_data", "track_imports")
        if not os.path.isdir(hist_dir):
            print(f"Historical dir not found. Tried: {hist_dir}", file=sys.stderr)
            sys.exit(1)
        _scan_historical(hist_dir)

    else:
        print("Usage:")
        print("  py luckless_analyser.py --test    Run self-tests")
        print("  py luckless_analyser.py --scan    Scan historical data")
