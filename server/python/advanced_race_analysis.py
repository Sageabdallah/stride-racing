"""Full-field 4-phase horse race analysis via Groq LLM with structured output."""

import json
import logging
from typing import Dict, Any, List, Optional, Literal
from dataclasses import dataclass, field, asdict
from enum import Enum

from llm_provider import get_provider, LLMProviderError

logger = logging.getLogger(__name__)



class RaceCategory(str, Enum):
    CONTENDER = "Contender"
    PLACE_HOPE = "Place Hope"
    LUCKY_TO_PLACE = "Lucky to Place"
    OUTCLASSED = "Outclassed"


class PaceScenario(str, Enum):
    HOT_PACE = "hot_pace"
    SOFT_LEAD = "soft_lead"
    HONEST_TEMPO = "honest_tempo"
    UNKNOWN = "unknown"


class MarketAssessment(str, Enum):
    OVERBET = "Overbet"
    VALUE = "Value"
    CORRECTLY_PRICED = "Correctly Priced"


@dataclass
class PhaseAnalysis:
    """Individual phase analysis for a horse."""
    phase_name: str
    reasoning: str
    key_factors: List[str] = field(default_factory=list)


@dataclass
class HorseAnalysis:
    """Complete analysis for a single horse."""
    horse_name: str
    number: int
    barrier: int
    
    phase_1_profile: PhaseAnalysis = field(default_factory=lambda: PhaseAnalysis("", ""))
    phase_2_pace: PhaseAnalysis = field(default_factory=lambda: PhaseAnalysis("", ""))
    phase_3_class: PhaseAnalysis = field(default_factory=lambda: PhaseAnalysis("", ""))
    phase_4_comparative: PhaseAnalysis = field(default_factory=lambda: PhaseAnalysis("", ""))
    
    train_of_thought: str = ""
    
    category: str = ""
    assessed_win_prob: float = 0.0
    assessed_place_prob: float = 0.0
    predicted_finish_position: int = 0
    confidence_pct: float = 0.0
    
    market_odds: float = 0.0
    market_assessment: str = ""
    value_edge_pct: float = 0.0
    
    can_win: bool = False
    place_potential: bool = False
    cannot_win_reason: str = ""
    upset_scenario: str = ""
    
    comparative_advantage: str = ""
    key_rivals: List[str] = field(default_factory=list)
    beats_whom: List[str] = field(default_factory=list)
    loses_to_whom: List[str] = field(default_factory=list)


@dataclass
class RaceAnalysisResult:
    """Complete race analysis result."""
    track: str
    race_number: int
    race_name: str
    distance: str
    going: str
    race_class: str
    field_size: int
    
    pace_scenario: str = ""
    pace_description: str = ""
    speed_horses: List[str] = field(default_factory=list)
    stalkers: List[str] = field(default_factory=list)
    closers: List[str] = field(default_factory=list)
    
    horses: List[HorseAnalysis] = field(default_factory=list)
    
    predicted_winner: Optional[HorseAnalysis] = None
    predicted_places: List[HorseAnalysis] = field(default_factory=list)
    
    race_summary: str = ""
    key_dynamics: str = ""
    betting_recommendation: str = ""
    
    analysis_timestamp: str = ""
    model_used: str = ""



SYSTEM_PROMPT_4PHASE = """You are an elite Australian horse racing analyst with 25+ years of experience. 
You think like a professional handicapper, analyzing every race through a sophisticated 4-phase logical framework.

Your task: Analyze EVERY horse in the race through all 4 phases, then determine the winner through comparative analysis.

=== PHASE 1: PROFILE & POSITIONING ===
Analyze where this horse will settle in the run based on:
- Barrier draw (inside=favorable for speed, outside=need good gate speed)
- Early speed profile (leader/on-pace/stalker/midfield/backmarker)
- Jockey tactics and trainer patterns
- Track bias (rail position, surface conditions)

Output format for Phase 1:
"Based on [barrier X], [early/mid/late speed profile], and [jockey tactics], this horse will likely settle [position 1st-5th/midfield/back] because [specific reasoning about gate speed and racing pattern]. The track bias [if rail good/fair] favors/disfavors this position."

=== PHASE 2: PACE SCENARIO FIT ===
Map each horse to the likely pace scenario:
- Identify speed horses who will push forward
- Identify stalkers who benefit from genuine tempo
- Identify closers who need a hot pace to pick up tiring leaders
- Assess what happens if pace collapses

Output format for Phase 2:
"In a race with [Speed Horse A] and [Speed Horse B] likely to push forward, this horse's [sprinting stamina/closing kick] means it will [benefit from hot pace/be compromised by lack of tempo]. If the pace collapses, this horse [will pick them up/will be caught flat]."

=== PHASE 3: CLASS & CONDITION ASSESSMENT ===
Evaluate class movement and fitness:
- Moving up/down in grade from last start
- Form cycle (improving/fresh/declining)
- Weight change impact
- Distance suitability
- Recent performance metrics

Output format for Phase 3:
"Moving [up/down] in grade from [last race class] to [current class]. Form cycle shows [improving/fresh/declining - evidence from sectionals, margins, or trials]. Weight [increase/decrease] of [X]kg [helps/hinders] given [distance/track conditions]."

=== PHASE 4: COMPARATIVE FINISHING LOGIC (The "Why") ===
Compare against specific rivals:
- Relative to [Horse Y] and [Horse Z], this horse lacks/exceeds [sustained speed/tactical versatility]
- Therefore, in this specific race dynamic: [specific finishing position prediction with % confidence]
- Be specific: "Will finish ahead of X because... but behind Y because..."

Output format for Phase 4:
"Relative to [Horse Y] and [Horse Z], this horse lacks/exceeds [sustained speed/tactical versatility]. Therefore, in this specific race dynamic: [specific finishing position prediction with % confidence]."

=== WINNER DETERMINATION ===
After analyzing ALL horses through Phases 1-4, crown ONE winner by comparing all Phase 4 analyses:

"Analyzing the field collectively, [Horse X] presents the strongest win case because [comparative advantage], while [Horse Y] and [Horse Z] are place chances due to [secondary reasoning]. [Horse A] cannot win due to [fatal flaw: wrong pace setup, class gap, etc.]."

=== SOPHISTICATED REASONING CATEGORIES ===
Classify each horse as ONE of:
- "Contender" (>20% win chance, legitimate winning chance)
- "Place Hope" (won't win but 2nd/3rd likely)
- "Lucky to Place" (needs chaos to run top 3)
- "Outclassed" (no place hope - explain specific fatal flaw)

=== MARKET INTELLIGENCE ===
Compare your assessment vs market odds:
- Calculate implied market probability (100/odds)
- Assess if horse is "Overbet", "Value", or "Correctly Priced"
- Provide value edge percentage

=== OUTPUT REQUIREMENTS ===
Return ONLY valid JSON in this exact structure:
{
  "pace_scenario": "hot_pace|soft_lead|honest_tempo|unknown",
  "pace_description": "Detailed 2-3 sentence pace narrative",
  "speed_horses": ["Horse A", "Horse B"],
  "stalkers": ["Horse C"],
  "closers": ["Horse D", "Horse E"],
  "horses": [
    {
      "horse_name": "Name",
      "number": 1,
      "phase_1": "Phase 1 reasoning...",
      "phase_2": "Phase 2 reasoning...",
      "phase_3": "Phase 3 reasoning...",
      "phase_4": "Phase 4 reasoning...",
      "train_of_thought": "Combined narrative flowing through all 4 phases",
      "category": "Contender|Place Hope|Lucky to Place|Outclassed",
      "assessed_win_prob": 25.5,
      "assessed_place_prob": 65.0,
      "predicted_finish_position": 1,
      "confidence_pct": 75,
      "market_assessment": "Value|Overbet|Correctly Priced",
      "value_edge_pct": 5.2,
      "can_win": true,
      "place_potential": true,
      "cannot_win_reason": "",
      "upset_scenario": "Would need [specific conditions] to win",
      "comparative_advantage": "Beats X because... loses to Y because...",
      "key_rivals": ["Rival A", "Rival B"],
      "beats_whom": ["Weaker Horse C"],
      "loses_to_whom": ["Stronger Horse D"]
    }
  ],
  "predicted_winner": "Horse Name",
  "predicted_places": ["2nd Horse", "3rd Horse"],
  "race_summary": "Overall race narrative",
  "key_dynamics": "Critical deciding factors",
  "betting_recommendation": "Value betting strategy"
}

=== CONSTRAINTS (MUST ENFORCE) ===
1. ONLY 1 horse can have "predicted_finish_position": 1 (the winner)
2. Maximum 3 horses can have "place_potential": true
3. If "can_win": false, MUST provide specific "cannot_win_reason" (fatal flaw)
4. "Outclassed" horses MUST explain why they can't place
5. Be specific in comparisons - name actual rival horses, not generic references
6. All probability percentages must sum to reasonable race total (~100% for win, ~300% for place)
7. Market assessments must be based on actual odds provided

=== ANALYST PERSONA ===
Think like a professional punter at the track:
- Use Australian racing terminology
- Reference specific track characteristics
- Consider pace dynamics deeply
- Respect the mathematics of probability
- Be decisive - pick ONE winner, not "could be any of three"
- Acknowledge uncertainty but make clear predictions"""



def preprocess_race_data(race_data: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich race data with computed fields for Groq analysis."""
    enriched = dict(race_data)
    
    runners = enriched.get("runners", enriched.get("full_field", []))
    if not runners:
        runners = enriched.get("top_picks", [])
    
    processed_runners = []
    for runner in runners:
        proc = dict(runner)
        
        proc["horse_name"] = proc.get("horse", proc.get("name", "Unknown"))
        proc["number"] = proc.get("number", proc.get("saddle_number", 0))
        proc["barrier"] = proc.get("barrier", proc.get("draw", 0))
        
        odds = proc.get("odds", 0)
        if isinstance(odds, list) and odds:
            odds = odds[0].get("win_odds", 0) if isinstance(odds[0], dict) else odds[0]
        if not odds:
            odds = proc.get("market_odds", proc.get("win_odds", 0))
        proc["market_odds"] = float(odds) if odds else 0
        proc["implied_prob"] = round(100 / float(odds), 1) if odds and float(odds) > 1 else 0
        
        proc["model_win_pct"] = proc.get("win_pct", proc.get("winPercentage", 0))
        
        style = proc.get("running_style", "unknown").lower()
        if "leader" in style or "speed" in style:
            proc["pace_profile"] = "speed"
        elif "stalker" in style or "on_pace" in style:
            proc["pace_profile"] = "stalker"
        elif "midfield" in style:
            proc["pace_profile"] = "midfield"
        elif "back" in style or "closer" in style:
            proc["pace_profile"] = "closer"
        else:
            proc["pace_profile"] = "unknown"
        
        form = proc.get("form", "")
        if form:
            recent = form.replace("-", "").replace("x", "").replace("0", "")[:3]
            if any(c in "123" for c in recent):
                proc["form_trend"] = "in_form"
            elif any(c in "45" for c in recent):
                proc["form_trend"] = "consistent"
            else:
                proc["form_trend"] = "out_of_form"
        else:
            proc["form_trend"] = "unknown"
        
        mc_data = proc.get("_mc_data", {})
        proc["is_class_drop"] = mc_data.get("isClassDrop", False)
        proc["is_class_rise"] = mc_data.get("isClassRise", False)
        proc["is_first_up"] = mc_data.get("isFirstUp", False)
        
        processed_runners.append(proc)
    
    enriched["processed_runners"] = processed_runners
    return enriched


def build_analysis_prompt(race_data: Dict[str, Any]) -> str:
    """Build the detailed prompt for Groq analysis."""
    
    track = race_data.get("track", "Unknown")
    race_number = race_data.get("race_number", 0)
    race_name = race_data.get("race_name", "Unknown")
    distance = race_data.get("distance", "Unknown")
    going = race_data.get("going", "Unknown")
    race_class = race_data.get("race_class", race_data.get("class", "Unknown"))
    runners = race_data.get("processed_runners", [])
    
    runner_lines = []
    for r in runners:
        name = r.get("horse_name", "Unknown")
        num = r.get("number", 0)
        barrier = r.get("barrier", 0)
        odds = r.get("market_odds", 0)
        win_pct = r.get("model_win_pct", 0)
        jockey = r.get("jockey", "TBA")
        trainer = r.get("trainer", "Unknown")
        weight = r.get("weight", "")
        form = r.get("form", "-")
        pace = r.get("pace_profile", "unknown")
        
        line_parts = [
            f"#{num}. {name}",
            f"Barrier {barrier}",
            f"J: {jockey}",
            f"T: {trainer}",
        ]
        
        if weight:
            line_parts.append(f"{weight}kg")
        if form:
            line_parts.append(f"Form: {form}")
        if odds:
            line_parts.append(f"Market: ${odds}")
        if win_pct:
            line_parts.append(f"Model Win%: {win_pct}%")
        if pace != "unknown":
            line_parts.append(f"Pace: {pace}")
        
        mc = r.get("_mc_data", {})
        if mc.get("isEliteJockey"):
            line_parts.append("[ELITE JOCKEY]")
        if mc.get("isEliteTrainer"):
            line_parts.append("[ELITE TRAINER]")
        if mc.get("isFirstUp"):
            line_parts.append("[FIRST-UP]")
        if mc.get("isCourseDistanceWinner"):
            line_parts.append("[C&D WINNER]")
        
        runner_lines.append(" | ".join(line_parts))
    
    runner_text = "\n".join(runner_lines)
    
    prompt = f"""Analyze this Australian thoroughbred race through your 4-phase framework.

RACE DETAILS:
Track: {track}
Race: {race_number} - {race_name}
Distance: {distance}
Going: {going}
Class: {race_class}
Field Size: {len(runners)} runners

FULL FIELD:
{runner_text}

INSTRUCTIONS:
1. Analyze EVERY horse through all 4 phases
2. Identify the pace scenario and map each horse to it
3. Classify each horse: Contender/Place Hope/Lucky to Place/Outclassed
4. Crown ONE definitive winner through comparative analysis
5. Identify place chances (max 3)
6. Assess market value for each runner
7. Return ONLY the JSON structure specified in your system prompt

Think deeply about pace dynamics and how they affect each runner's chances. Be decisive in your winner selection."""

    return prompt



def validate_and_enforce_constraints(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforce logical constraints on the analysis result:
    - Only 1 predicted winner
    - Max 3 place potentials
    - Outclassed horses must have cannot_win_reason
    """
    horses = result.get("horses", [])
    
    winners = [h for h in horses if h.get("predicted_finish_position") == 1]
    if len(winners) > 1:
        sorted_by_conf = sorted(winners, key=lambda x: x.get("confidence_pct", 0), reverse=True)
        for h in sorted_by_conf[1:]:
            h["predicted_finish_position"] = 2
            h["can_win"] = False
            h["cannot_win_reason"] = "Multiple horses predicted to win - adjusted to place chance"
    
    place_hopes = [h for h in horses if h.get("place_potential")]
    if len(place_hopes) > 3:
        sorted_by_place = sorted(place_hopes, key=lambda x: x.get("assessed_place_prob", 0), reverse=True)
        for h in sorted_by_place[3:]:
            h["place_potential"] = False
    
    for h in horses:
        if h.get("category") == "Outclassed" and not h.get("cannot_win_reason"):
            h["cannot_win_reason"] = "Outclassed in this company - lacks the class/form to be competitive"
    
    total_win_prob = sum(h.get("assessed_win_prob", 0) for h in horses)
    if total_win_prob < 80 or total_win_prob > 130:
        factor = 100 / total_win_prob if total_win_prob > 0 else 1
        for h in horses:
            h["assessed_win_prob"] = round(h.get("assessed_win_prob", 0) * factor, 1)
    
    return result


def parse_horse_analysis(horse_data: Dict[str, Any]) -> HorseAnalysis:
    """Parse raw horse analysis data into structured object."""
    
    phase_1 = PhaseAnalysis(
        phase_name="Profile & Positioning",
        reasoning=horse_data.get("phase_1", ""),
        key_factors=[]
    )
    phase_2 = PhaseAnalysis(
        phase_name="Pace Scenario Fit",
        reasoning=horse_data.get("phase_2", ""),
        key_factors=[]
    )
    phase_3 = PhaseAnalysis(
        phase_name="Class & Condition",
        reasoning=horse_data.get("phase_3", ""),
        key_factors=[]
    )
    phase_4 = PhaseAnalysis(
        phase_name="Comparative Logic",
        reasoning=horse_data.get("phase_4", ""),
        key_factors=[]
    )
    
    return HorseAnalysis(
        horse_name=horse_data.get("horse_name", "Unknown"),
        number=horse_data.get("number", 0),
        barrier=horse_data.get("barrier", 0),
        phase_1_profile=phase_1,
        phase_2_pace=phase_2,
        phase_3_class=phase_3,
        phase_4_comparative=phase_4,
        train_of_thought=horse_data.get("train_of_thought", ""),
        category=horse_data.get("category", ""),
        assessed_win_prob=horse_data.get("assessed_win_prob", 0),
        assessed_place_prob=horse_data.get("assessed_place_prob", 0),
        predicted_finish_position=horse_data.get("predicted_finish_position", 0),
        confidence_pct=horse_data.get("confidence_pct", 0),
        market_odds=horse_data.get("market_odds", 0),
        market_assessment=horse_data.get("market_assessment", ""),
        value_edge_pct=horse_data.get("value_edge_pct", 0),
        can_win=horse_data.get("can_win", False),
        place_potential=horse_data.get("place_potential", False),
        cannot_win_reason=horse_data.get("cannot_win_reason", ""),
        upset_scenario=horse_data.get("upset_scenario", ""),
        comparative_advantage=horse_data.get("comparative_advantage", ""),
        key_rivals=horse_data.get("key_rivals", []),
        beats_whom=horse_data.get("beats_whom", []),
        loses_to_whom=horse_data.get("loses_to_whom", [])
    )


def parse_analysis_result(raw_result: Dict[str, Any], original_race_data: Dict[str, Any]) -> RaceAnalysisResult:
    """Parse the Groq response into structured RaceAnalysisResult."""
    
    horses = []
    predicted_winner = None
    predicted_places = []
    
    for h_data in raw_result.get("horses", []):
        horse = parse_horse_analysis(h_data)
        horses.append(horse)
        
        if horse.predicted_finish_position == 1:
            predicted_winner = horse
        elif horse.predicted_finish_position in [2, 3]:
            predicted_places.append(horse)
    
    predicted_places.sort(key=lambda x: x.predicted_finish_position)
    
    return RaceAnalysisResult(
        track=original_race_data.get("track", ""),
        race_number=original_race_data.get("race_number", 0),
        race_name=original_race_data.get("race_name", ""),
        distance=original_race_data.get("distance", ""),
        going=original_race_data.get("going", ""),
        race_class=original_race_data.get("race_class", ""),
        field_size=len(horses),
        pace_scenario=raw_result.get("pace_scenario", ""),
        pace_description=raw_result.get("pace_description", ""),
        speed_horses=raw_result.get("speed_horses", []),
        stalkers=raw_result.get("stalkers", []),
        closers=raw_result.get("closers", []),
        horses=horses,
        predicted_winner=predicted_winner,
        predicted_places=predicted_places,
        race_summary=raw_result.get("race_summary", ""),
        key_dynamics=raw_result.get("key_dynamics", ""),
        betting_recommendation=raw_result.get("betting_recommendation", "")
    )



def analyze_race_full_field(race_data: Dict[str, Any]) -> Optional[RaceAnalysisResult]:
    """Perform full-field 4-phase race analysis via Groq API."""
    try:
        llm = get_provider()
    except LLMProviderError as e:
        logger.warning(f"LLM not available for advanced analysis: {e}")
        return None
    
    enriched_data = preprocess_race_data(race_data)
    
    prompt = build_analysis_prompt(enriched_data)
    
    logger.info(f"Running 4-phase analysis for {enriched_data.get('track')} R{enriched_data.get('race_number')}")
    
    try:
        raw_result = llm.generate_json(
            prompt,
            system=SYSTEM_PROMPT_4PHASE,
            temperature=0.4,  # Slightly higher for more creative reasoning
            max_tokens=4000   # Need more tokens for full field analysis
        )
    except LLMProviderError as e:
        logger.error(f"Groq analysis failed: {e}")
        return None
    
    if not raw_result:
        logger.warning("Empty result from Groq analysis")
        return None
    
    validated_result = validate_and_enforce_constraints(raw_result)
    
    result = parse_analysis_result(validated_result, enriched_data)
    
    logger.info(f"Analysis complete. Winner: {result.predicted_winner.horse_name if result.predicted_winner else 'None'}")
    
    return result


def analyze_race_to_dict(race_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convenience function that returns analysis as a dictionary."""
    result = analyze_race_full_field(race_data)
    if result is None:
        return None
    
    def convert_to_dict(obj):
        if isinstance(obj, (HorseAnalysis, PhaseAnalysis, RaceAnalysisResult)):
            d = asdict(obj)
            return {k: v for k, v in d.items() if v is not None and v != ""}
        return obj
    
    return {
        "track": result.track,
        "race_number": result.race_number,
        "race_name": result.race_name,
        "distance": result.distance,
        "going": result.going,
        "race_class": result.race_class,
        "field_size": result.field_size,
        "pace_scenario": result.pace_scenario,
        "pace_description": result.pace_description,
        "speed_horses": result.speed_horses,
        "stalkers": result.stalkers,
        "closers": result.closers,
        "horses": [
            {
                "horse_name": h.horse_name,
                "number": h.number,
                "barrier": h.barrier,
                "phase_1_profile": asdict(h.phase_1_profile),
                "phase_2_pace": asdict(h.phase_2_pace),
                "phase_3_class": asdict(h.phase_3_class),
                "phase_4_comparative": asdict(h.phase_4_comparative),
                "train_of_thought": h.train_of_thought,
                "category": h.category,
                "assessed_win_prob": h.assessed_win_prob,
                "assessed_place_prob": h.assessed_place_prob,
                "predicted_finish_position": h.predicted_finish_position,
                "confidence_pct": h.confidence_pct,
                "market_odds": h.market_odds,
                "market_assessment": h.market_assessment,
                "value_edge_pct": h.value_edge_pct,
                "can_win": h.can_win,
                "place_potential": h.place_potential,
                "cannot_win_reason": h.cannot_win_reason,
                "upset_scenario": h.upset_scenario,
                "comparative_advantage": h.comparative_advantage,
                "key_rivals": h.key_rivals,
                "beats_whom": h.beats_whom,
                "loses_to_whom": h.loses_to_whom
            }
            for h in result.horses
        ],
        "predicted_winner": {
            "horse_name": result.predicted_winner.horse_name,
            "number": result.predicted_winner.number,
            "barrier": result.predicted_winner.barrier,
            "train_of_thought": result.predicted_winner.train_of_thought,
            "assessed_win_prob": result.predicted_winner.assessed_win_prob,
            "confidence_pct": result.predicted_winner.confidence_pct
        } if result.predicted_winner else None,
        "predicted_places": [
            {
                "horse_name": h.horse_name,
                "number": h.number,
                "predicted_position": h.predicted_finish_position,
                "train_of_thought": h.train_of_thought
            }
            for h in result.predicted_places
        ],
        "race_summary": result.race_summary,
        "key_dynamics": result.key_dynamics,
        "betting_recommendation": result.betting_recommendation
    }



def main():
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if "--test" in sys.argv:
        test_race = {
            "track": "Flemington",
            "race_number": 7,
            "race_name": "Group 1 Test Stakes",
            "distance": "1600m",
            "going": "Good",
            "race_class": "G1 WFA",
            "runners": [
                {
                    "horse": "Speed Demon",
                    "number": 1,
                    "barrier": 3,
                    "jockey": "J. Bowman",
                    "trainer": "C. Waller",
                    "weight": "57kg",
                    "form": "112",
                    "odds": 2.5,
                    "win_pct": 35.0,
                    "running_style": "leader"
                },
                {
                    "horse": "Midfield Master",
                    "number": 2,
                    "barrier": 8,
                    "jockey": "H. McEvoy",
                    "trainer": "T. Gollan",
                    "weight": "57kg",
                    "form": "231",
                    "odds": 4.0,
                    "win_pct": 22.0,
                    "running_style": "midfield"
                },
                {
                    "horse": "Late Charger",
                    "number": 3,
                    "barrier": 12,
                    "jockey": "D. Oliver",
                    "trainer": "L. Freedman",
                    "weight": "57kg",
                    "form": "445",
                    "odds": 6.5,
                    "win_pct": 12.0,
                    "running_style": "backmarker"
                }
            ]
        }
        
        print("Testing advanced race analysis...", file=sys.stderr)
        result = analyze_race_to_dict(test_race)
        
        if result:
            print(json.dumps(result, indent=2, default=str))
            print("\n✓ Test passed!", file=sys.stderr)
        else:
            print("\n✗ Test failed - no result returned", file=sys.stderr)
            sys.exit(1)
    
    elif "--analyze-race" in sys.argv:
        race_data_json = os.environ.get('RACE_DATA')
        if not race_data_json:
            print(json.dumps({"error": "No RACE_DATA environment variable"}))
            sys.exit(1)
        
        try:
            race_data = json.loads(race_data_json)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON in RACE_DATA: {e}"}))
            sys.exit(1)
        
        result = analyze_race_to_dict(race_data)
        
        if result:
            print(json.dumps(result, default=str))
        else:
            print(json.dumps({"error": "Analysis failed"}))
            sys.exit(1)
    
    else:
        print("Usage: python advanced_race_analysis.py --test | --analyze-race", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
