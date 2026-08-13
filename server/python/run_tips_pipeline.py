#!/usr/bin/env python3
"""
Race Tips Pipeline — Standalone orchestrator for /tips skill.

Runs Monte Carlo + ML ensemble on each race from a racecard JSON file,
applies market-anchored calibration, enriches with database lookups
(franking, sectionals, strike rates), and outputs a structured tips JSON.

Usage:
    py run_tips_pipeline.py <DATE> [track1 track2 ...]
    py run_tips_pipeline.py 2026-03-01
    py run_tips_pipeline.py 2026-03-01 randwick flemington
    py run_tips_pipeline.py 2026-03-01 Flemington --output-suffix proof --skip-db-store

Output:
    - JSON to stdout (for skill consumption)
    - Saved to racecards/tips_<DATE>.json
"""

import argparse
import json
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from datetime import datetime
from pathlib import Path

from tips_day_aggregates import (
    best_bet_sort_score,
    build_convergence_summary,
    build_selection_contract,
    build_summary,
    collect_day_picks,
    merge_tips_into_canonical,
    select_bankers,
    select_best_bets,
    select_value_plays,
)
from validate_tips import validate_file as validate_tips_file
from market_prob import renormalise_probs, true_market_prob_pct

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RACECARDS_DIR = PROJECT_ROOT / "racecards"
MC_API_SCRIPT = SCRIPT_DIR / "mc_api.py"
_MC_API_MODULE = None

logger = logging.getLogger(__name__)


def _load_env_vars():
    """Load ALL vars from .env into os.environ (not just DATABASE_URL)."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"')
                if key not in os.environ:
                    os.environ[key] = val


def _load_llm_modules():
    """Lazy-import LLM modules to avoid import errors when LLM is disabled."""
    try:
        _load_env_vars()
        from llm_form_analysis import analyse_race_field, apply_pre_mc_adjustments
        from llm_post_scorer import score_race_horses, generate_rich_insight, generate_brief_assessments
        from llm_provider import get_provider
        return {
            "analyse_race_field": analyse_race_field,
            "apply_pre_mc_adjustments": apply_pre_mc_adjustments,
            "score_race_horses": score_race_horses,
            "generate_rich_insight": generate_rich_insight,
            "generate_brief_assessments": generate_brief_assessments,
            "get_provider": get_provider,
        }
    except Exception as e:
        print(f"  [LLM] Not available: {e}", file=sys.stderr)
        return None


def _race_seed(date_str, track, race_num):
    """Deterministic per-race MC seed from race identity.

    Same card + same code => identical draws, so a re-run reproduces its own
    tips exactly and a bad tip can be diagnosed after the fact. It also makes
    comparing two code versions on one card common-random-numbers by
    construction — with the old `int(time.time()) % 100000` seed every re-run
    was a different tips file, and a model delta had to be dug out from under
    ~1pp of resampling noise per runner.

    Hashing the race identity keeps races decorrelated from each other on the
    same card. Set STRIDE_MC_SEED_SALT to any new value when independent
    draws are wanted on purpose (e.g. measuring the MC's own sampling error).
    """
    key = (f"{os.environ.get('STRIDE_MC_SEED_SALT', '')}"
           f"|{date_str}|{track}|{race_num}")
    return zlib.crc32(key.encode("utf-8")) % 100000


def _configure_mc_runtime_flags():
    """
    Keep the strongest MC/sectional/pace stack on, but default the two
    highest-cost low-signal DB extras off for full-card generation unless
    the caller explicitly opts back in.
    """
    defaults = {
        "MC_ENABLE_SECTIONAL_FRANKING": "false",
        "MC_ENABLE_JOCKEY_EFFICIENCY": "false",
    }
    for key, value in defaults.items():
        if key not in os.environ:
            os.environ[key] = value

    print(
        "  [MC FLAGS] "
        f"sectional_franking={os.environ.get('MC_ENABLE_SECTIONAL_FRANKING')} "
        f"jockey_efficiency={os.environ.get('MC_ENABLE_JOCKEY_EFFICIENCY')}",
        file=sys.stderr,
    )


INTELLIGENCE_DIR = SCRIPT_DIR / "intelligence"


def load_intelligence_files():
    """
    Load all STRIDE intelligence JSON files.
    Returns dict with parsed data + name-based reverse lookups for
    horse_id-keyed files (form_franking, prep_cycles).
    """
    files = {
        "barrier_map": "barrier_map.json",
        "flemington_straight": "flemington_straight.json",
        "class_distance_patterns": "class_distance_patterns.json",
        "form_franking": "form_franking.json",
        "prep_cycles": "prep_cycles.json",
        "sectional_trends": "sectional_trends.json",
        "trainer_patterns": "trainer_patterns.json",
        "market_overlays": "market_overlays.json",
        "track_distance_profiles": "track_distance_profiles.json",
    }
    intel = {}
    for key, fname in files.items():
        fpath = INTELLIGENCE_DIR / fname
        if fpath.exists():
            try:
                with open(fpath, "r") as f:
                    intel[key] = json.load(f)
            except Exception:
                intel[key] = {}
        else:
            intel[key] = {}

    intel["_franking_by_name"] = {}
    franking_horses = intel.get("form_franking", {}).get("horses", [])
    if isinstance(franking_horses, dict):
        iterable = franking_horses.values()
    elif isinstance(franking_horses, list):
        iterable = franking_horses
    else:
        iterable = []
    for v in iterable:
        name = (v.get("horse_name") or "").strip().lower()
        if name:
            intel["_franking_by_name"][name] = v

    intel["_prep_by_name"] = {}
    prep_raw = intel.get("prep_cycles", {})
    # prep_cycles.json may be {"horses": [...]} or a flat dict {horse_id: {...}}
    prep_horses = prep_raw.get("horses") if isinstance(prep_raw.get("horses"), (dict, list)) else None
    if prep_horses is None:
        # Flat dict keyed by horse_id — use values directly (skip non-dict meta keys)
        iterable = [v for v in prep_raw.values() if isinstance(v, dict) and "horse_name" in v]
    elif isinstance(prep_horses, dict):
        iterable = prep_horses.values()
    elif isinstance(prep_horses, list):
        iterable = prep_horses
    else:
        iterable = []
    for v in iterable:
        name = (v.get("horse_name") or "").strip().lower()
        if name:
            intel["_prep_by_name"][name] = v

    counts = {
        "barrier_map": len(intel.get("barrier_map", {})),
        "form_franking": len(intel.get("_franking_by_name", {})),
        "prep_cycles": len(intel.get("_prep_by_name", {})),
        "sectional_trends": len(intel.get("sectional_trends", {})),
        "trainer_patterns": len(intel.get("trainer_patterns", {})),
        "market_overlays": len(intel.get("market_overlays", {})),
    }
    parts = [f"{k}: {v}" for k, v in counts.items()]
    print(f"  [INTEL] Loaded: {' | '.join(parts)}", file=sys.stderr)

    critical = ["barrier_map", "form_franking", "prep_cycles"]
    empty = [k for k in critical if counts.get(k, 0) == 0]
    if empty:
        print(f"  [INTEL WARNING] Critical files have 0 records: {', '.join(empty)}", file=sys.stderr)

    return intel


def enrich_horse_with_intelligence(h, track, distance_m, going, race_class, intelligence):
    """Attach intelligence signals to a horse dict as h['_intelligence']."""
    horse_name = (h.get("horse") or "").strip()
    horse_lower = horse_name.lower()
    trainer = (h.get("trainer") or "").strip()
    barrier = str(h.get("barrier") or h.get("draw") or "")

    intel = {}

    dist_cat = "sprint" if distance_m < 1200 else "mile" if distance_m <= 1600 else "staying"
    going_lower = (going or "good").lower()
    if "heavy" in going_lower:
        going_key = "heavy"
    elif "soft" in going_lower:
        going_key = "soft"
    elif "firm" in going_lower:
        going_key = "firm"
    else:
        going_key = "good"

    track_barriers = (intelligence.get("barrier_map", {})
                      .get(track, {}).get(dist_cat, {}).get(going_key, {})
                      .get("barriers", {}))
    if barrier and barrier in track_barriers:
        b_data = track_barriers[barrier]
        intel["barrier_edge"] = {
            "win_pct": b_data.get("win_pct", 0),
            "edge_vs_random": b_data.get("edge_vs_random", 0),
            "confidence": b_data.get("confidence", "LOW"),
            "going_uncertain": bool(b_data.get("going_uncertain", False)),
        }

    if "flemington" in track.lower() and distance_m <= 1200:
        flem = intelligence.get("flemington_straight", {}).get("distances", {})
        dist_key = str(distance_m)
        if dist_key in flem and going_key in flem[dist_key]:
            b_edge = flem[dist_key][going_key].get("barrier_edges", {}).get(barrier, {})
            if b_edge:
                intel["flemington_straight"] = b_edge

    franking = intelligence.get("_franking_by_name", {}).get(horse_lower)
    if franking:
        intel["franking"] = {
            "form_franking_score": franking.get("franking_score", 0) or 0,
            "franking_confidence": franking.get("franking_confidence", 0) or 0,
            "classification": franking.get("classification"),
            "franking_weight": franking.get("franking_weight", 0) or 0,
            "anti_franked": franking.get("anti_franked", False),
            "form_quality_trend": franking.get("form_quality_trend"),
            "graph_franking_score": franking.get("graph_franking_score", 0),
            "graph_franking_depth": franking.get("graph_franking_depth", 0),
            "graph_franking_independence": franking.get("graph_franking_independence", 0),
            "pagerank_authority": franking.get("pagerank_authority", 0.5),
            "community_strength": franking.get("community_strength", 0.5),
            "form_stability": franking.get("form_stability", 0.5),
            "bridge_score": franking.get("bridge_score", 0),
            "market_validated_franking": franking.get("market_validated_franking", False),
        }
        fc = franking.get("franking_class", {})
        if fc:
            intel["franking_class"] = {
                "reference_class": fc.get("reference_class", "Unknown"),
                "reference_class_weight": fc.get("reference_class_weight", 1.0),
                "group_listed_in_chain": fc.get("group_listed_in_chain", False),
                "recursive_class_avg": fc.get("recursive_class_avg", 1.0),
            }

    prep = intelligence.get("_prep_by_name", {}).get(horse_lower)
    if prep:
        runs_this_prep = prep.get("runs_this_prep") or prep.get("projected_run_number_today") or 1
        peak_run = prep.get("peak_run_number") or prep.get("historical_peak_run_number")
        is_at_peak = prep.get("is_at_peak_run", False) or (peak_run is not None and runs_this_prep == peak_run)
        trajectory = prep.get("trajectory") or prep.get("prep_trend")
        intel["prep_cycle"] = {
            "projected_run_number": runs_this_prep,
            "completed_runs_this_prep": prep.get("runs_this_prep", 0),
            "prep_trend": trajectory,
            "first_up_today": prep.get("first_up_today", runs_this_prep == 1),
            "second_up_today": prep.get("second_up_today", runs_this_prep == 2),
            "quick_backup": prep.get("back_up", False) or prep.get("quick_backup_today", False),
            "is_at_peak_run": is_at_peak,
            "historical_peak_run_number": peak_run,
            "days_since_last_run": prep.get("days_since_last_run"),
            "completed_preparations": prep.get("completed_preparations", 0),
            "campaign_tempo": prep.get("campaign_tempo"),
            "weight_penalty": prep.get("weight_penalty", 0),
        }

    sect = intelligence.get("sectional_trends", {}).get(horse_name)
    if sect:
        intel["sectional_trend"] = {
            "improving": sect.get("sectional_trend_improving", False),
            "fastest_closer_unplaced": sect.get("fastest_closer_while_unplaced", False),
            "pb_while_unplaced": sect.get("pb_while_unplaced", False),
        }

    tp = intelligence.get("trainer_patterns", {}).get(trainer)
    if tp:
        jockey = (h.get("jockey") or "").strip()
        synergy = tp.get("jockey_synergy", {}).get(jockey, {})
        intel["trainer"] = {
            "overall_win_pct": tp.get("overall_win_pct"),
            "first_up_win_pct": tp.get("first_up_win_pct"),
        }
        if synergy:
            intel["trainer"]["jockey_synergy_edge"] = synergy.get("synergy_edge", 0)
            intel["trainer"]["jockey_synergy_win_pct"] = synergy.get("win_pct", 0)

    overlays = intelligence.get("market_overlays", {}).get(track, {}).get(going_key, {})
    odds = h.get("marketOdds") or 0
    if odds > 0 and overlays:
        if odds < 4:
            tier = "short"
        elif odds < 10:
            tier = "mid"
        elif odds < 20:
            tier = "long"
        else:
            tier = "outsider"
        overlay_data = overlays.get(tier, {})
        if overlay_data.get("overlay"):
            intel["market_overlay"] = {
                "tier": tier,
                "market_edge": overlay_data.get("market_edge", 0),
            }

    td_profiles = intelligence.get("track_distance_profiles", {})
    if td_profiles:
        try:
            from track_profiler import lookup_profile as _td_lookup
            barrier_val = h.get("barrier") or h.get("draw")
            try:
                barrier_val = int(barrier_val) if barrier_val else None
            except (ValueError, TypeError):
                barrier_val = None
            style_val = h.get("runningStyle") or h.get("running_style")
            td_feat = _td_lookup(td_profiles, track, distance_m,
                                 barrier=barrier_val, running_style=style_val)
            h.update(td_feat)
            intel["track_distance_profile"] = {
                "pace_bias": td_feat.get("td_pace_bias"),
                "upset_rate": td_feat.get("td_upset_rate"),
                "barrier_style_edge": td_feat.get("td_barrier_style_edge"),
                "closing_speed_bias": td_feat.get("td_closing_speed_bias"),
            }
        except Exception:
            pass

    if intel:
        h["_intelligence"] = intel


def get_db_url():
    """Load DATABASE_URL from .env file."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                val = line.split("=", 1)[1].strip()
                if val.startswith('"') and val.endswith('"'):
                    val = val[1:-1]
                return val
    return os.environ.get("DATABASE_URL", "")


def db_connect(max_retries=3):
    """Connect to PostgreSQL with exponential backoff."""
    import psycopg2
    last_err = None
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                get_db_url(),
                connect_timeout=5,
            )
            try:
                cur = conn.cursor()
                cur.execute("SET statement_timeout TO 15000")
                cur.close()
            except Exception:
                pass
            return conn
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  [DB] Connection attempt {attempt+1}/{max_retries} failed: {e} — retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise ConnectionError(f"DB connection failed after {max_retries} attempts: {last_err}")


BASE_ITERATIONS = 5000

def get_iterations(field_size):
    if field_size <= 10:
        return BASE_ITERATIONS
    if field_size <= 14:
        return 3000
    return 2000


def run_mc_simulation(race_input):
    """Run mc_api in-process so model/cache state survives across the card."""
    global _MC_API_MODULE
    try:
        if _MC_API_MODULE is None:
            spec = importlib.util.spec_from_file_location("wizbet_mc_api_runtime", MC_API_SCRIPT)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            _MC_API_MODULE = module

        race_data = {
            "track": race_input.get("track", ""),
            "course": race_input.get("track", ""),
            "race_number": race_input.get("race", race_input.get("raceNumber", 1)),
            "distance": race_input.get("distance", "1400m"),
            "going": race_input.get("going", "Good"),
            "race_name": race_input.get("race_name", ""),
            "race_id": race_input.get("race_id", ""),
            "date": race_input.get("date", race_input.get("raceDate", "")),
            "raceClass": race_input.get("raceClass", race_input.get("race_class", "")),
            "raceDate": race_input.get("raceDate", race_input.get("date", "")),
            "offTime": race_input.get("offTime", race_input.get("off_time", "")),
        }
        return _MC_API_MODULE.run_simulation(
            race_data,
            race_input.get("runners", []),
            mc_sims=race_input.get("mc_sims", race_input.get("iterations", 10000)),
            seed=race_input.get("seed", 42),
        )
    except Exception as e:
        print(f"  [MC ERROR] {e}", file=sys.stderr)
        return None


def _flag_enabled(name):
    """Env-flag convention: default OFF unless explicitly true/1/yes."""
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


def calculate_overround(runners):
    total_implied = 0
    valid = 0
    from market_prob import reference_price
    for r in runners:
        # Fair-prob inputs use the reference price (median across books,
        # task 10); the execution side (extract_odds) takes the best price.
        odds = reference_price(r.get("odds")) or extract_odds(r)
        if odds and odds > 1:
            total_implied += 100 / odds
            valid += 1
    if valid < 2 or total_implied <= 0:
        return 1.0
    return total_implied / 100


def extract_odds(runner):
    """Extract decimal odds from runner object (matches pipeline.ts logic)."""
    odds_arr = runner.get("odds", [])
    if isinstance(odds_arr, list):
        # Task 10 price-taken policy: the price we quote and settle at is the
        # BEST fixed price across bookmakers, not the first one in the array.
        best = 0.0
        for entry in odds_arr:
            if isinstance(entry, dict):
                for key in ("decimal", "win_odds", "odds"):
                    val = entry.get(key)
                    if val is not None:
                        try:
                            dec = float(str(val).replace("$", ""))
                            if dec > 1:
                                best = max(best, dec)
                        except (ValueError, TypeError):
                            pass
                        break
            elif isinstance(entry, (int, float)) and entry > 1:
                best = max(best, float(entry))
        if best > 1:
            return best
    for key in ("sp", "win_odds", "fixed_odds"):
        val = runner.get(key)
        if val is not None:
            try:
                dec = float(str(val).replace("$", ""))
                if dec > 1:
                    return dec
            except (ValueError, TypeError):
                pass
    return None


# --- Book coherence -------------------------------------------------------
# A race whose implied win-price book cannot be a market must be visible
# before money is assigned to it. On 2026-08-08 three races carried books of
# 232-312% (bot orders parked at $1.12-$1.16 on unformed runners) and one ran
# at 83% (4 of 7 runners priced); the pipeline de-vigged the impossible sums
# into plausible-looking trueMarketProbs and staked 31u into them (settled
# -7.50u, vs +12.90u across the coherent races). Thresholds are provisional
# until the unconditional [BOOK] detect logs accumulate; the measured card
# was bimodal — 40 coherent races 0.83-1.50, 4 corrupt 2.31-3.12, an 82pp
# void between — so the ceiling sits in empty space today.
BOOK_MIN_PRICED = 4
BOOK_SUM_CEILING = 1.60
BOOK_SUM_FLOOR = 0.90
BOOK_TOP2_CEILING = 1.10
BOOK_TOP2_MIN_FIELD = 8
BOOK_COVERAGE_FLOOR = 0.70


def assess_book_coherence(runners):
    """Judge whether a race's price book could be a real win market.

    Price selection mirrors calculate_overround exactly (reference price,
    falling back to the execution price) so the verdict is about the same
    number the pipeline divides by. `runners` is the post-scratching field,
    so coverage is priced-vs-declared-active, never priced-vs-priced.

    Reasons are classed: corruption (book_over_ceiling, top2_impossible —
    prices that cannot coexist) vs coverage (unpriceable, book_under_floor,
    coverage_thin — the market model is blind to part of the field). The
    split is load-bearing: replayed on tips_2026-08-05, coverage arms flag
    20 of 31 races and withhold 0u (unpriced races stake nothing anyway)
    while exactly one race is genuine corruption — a run-level rule that
    counts both classes together aborts on coverage.

    Named blind spots (the proxy test this check would still pass with),
    all measured on the 2026-08-08 card:
    1. NOT an EV guard: of 38u negative-EV stakes, only 7u sat inside the
       refused races; 31u was negative EV inside coherent 105-149% books.
    2. A book wrong by a common factor sums normally and passes (prices
       could shift 17-48% with nothing firing) — the capture-side
       unformed-book guard in betfair_odds_snapshot.py owns that failure,
       as does the fetch_from_db any-snapshot_kind staleness path.
    3. A lone parked short in a long field can hide inside a normal total:
       the top-2 arm catches pairs and extreme singles, not every single.
    4. Floor and coverage arms are independent ORs: Eagle Farm R3 (73.3%
       priced, book 143.5%) implies a true book near 196% — divisor 1.36x
       too small — and passes. A joint book/coverage arm was measured and
       rejected: it destroys the 82pp bimodal gap that makes the ceiling
       non-fitted, and loses the Morphettville R1 under-round catch.
    5. Deliberately no per-runner implied-probability cap: $1.10 implies
       90.9% and is a real price; the pressure to keep raising such a cap
       is how a check gets weakened until it passes everything. Only the
       field is asked to be possible.
    The detect logs exist to tune all of this before enforcement tightens.
    """
    from market_prob import reference_price
    probs = []
    for r in runners:
        odds = reference_price(r.get("odds")) or extract_odds(r)
        if odds and odds > 1:
            probs.append(1.0 / odds)
    probs.sort(reverse=True)
    n_active = len(runners)
    n_priced = len(probs)
    book = sum(probs)
    coverage = (n_priced / n_active) if n_active else 0.0
    top2 = sum(probs[:2])

    reasons = []
    if n_priced < BOOK_MIN_PRICED:
        reasons.append("unpriceable")
    else:
        if book > BOOK_SUM_CEILING:
            reasons.append("book_over_ceiling")
        if book < BOOK_SUM_FLOOR:
            reasons.append("book_under_floor")
        if n_active >= BOOK_TOP2_MIN_FIELD and top2 > BOOK_TOP2_CEILING:
            reasons.append("top2_impossible")
        if coverage < BOOK_COVERAGE_FLOOR:
            reasons.append("coverage_thin")
    corrupt = any(r in ("book_over_ceiling", "top2_impossible")
                  for r in reasons)
    return {
        "n_active": n_active,
        "n_priced": n_priced,
        "book": round(book, 4),
        "coverage": round(coverage, 4),
        "top2": round(top2, 4),
        "incoherent": bool(reasons),
        "corrupt": corrupt,
        "reasons": reasons,
    }


def calculate_intelligence_adjustment(h):
    """
    Extract intelligence signals and compute a composite adjustment.
    Returns a multiplier (0.8 to 1.3) and a flat bonus (-3 to +4).
    """
    intel = h.get("_intelligence", {})
    if not intel:
        return 1.0, 0.0

    multiplier = 1.0
    bonus = 0.0

    # franking_score is 0-100 scale; franking_confidence is 0-1
    franking = intel.get("franking", {})
    franking_score = franking.get("form_franking_score", 0) or 0
    franking_conf = franking.get("franking_confidence", 0) or 0
    franking_weight = franking.get("franking_weight", 0) or 0
    fc = intel.get("franking_class", {})
    ref_class_weight = fc.get("reference_class_weight", 1.0)
    has_group_chain = fc.get("group_listed_in_chain", False)

    if franking_score >= 60 and franking_conf >= 0.3:
        base = 2.0 + (ref_class_weight - 1.0)
        if has_group_chain:
            base += 0.5
        bonus += min(base, 3.5)
    elif franking_score >= 52 and franking_conf >= 0.15:
        bonus += 1.0
    elif franking_score < 40 and franking_conf >= 0.15:
        bonus -= 1.0
    if franking.get("anti_franked"):
        bonus -= 0.5

    pagerank = franking.get("pagerank_authority", 0.5) or 0.5
    if pagerank >= 0.8:
        bonus += 0.5
    graph_depth = franking.get("graph_franking_depth", 0) or 0
    graph_independence = franking.get("graph_franking_independence", 0) or 0
    if graph_depth >= 3 and graph_independence >= 3:
        bonus += 0.5
    form_stab = franking.get("form_stability", 0.5) or 0.5
    if form_stab >= 0.8:
        bonus += 0.3
    elif form_stab < 0.3:
        bonus -= 0.3
    if franking.get("market_validated_franking"):
        bonus += 0.3

    prep = intel.get("prep_cycle", {})
    if prep.get("is_at_peak_run"):
        bonus += 1.5
    trend = (prep.get("prep_trend") or "").lower()
    if trend == "improving":
        bonus += 1.0
    elif trend == "flattening_or_regressing":
        bonus -= 0.5
    # Second-up after a spell tends to be peak
    if prep.get("second_up_today"):
        bonus += 0.5
    # Quick backup = horse is in good form, trainer is pushing
    if prep.get("quick_backup"):
        bonus += 0.5
    tempo = (prep.get("campaign_tempo") or "")
    if tempo == "FRAGILE":
        multiplier *= 0.90
    wp = prep.get("weight_penalty", 0) or 0
    if wp < 0:
        bonus += wp * 0.5

    barrier = intel.get("barrier_edge", {})
    barrier_edge = barrier.get("edge_vs_random", 0) or 0
    confidence = (barrier.get("confidence") or "").upper()
    if confidence in ("HIGH", "MEDIUM") and barrier_edge > 3:
        bonus += min(barrier_edge * 0.3, 1.5)
    elif barrier_edge < -3:
        bonus -= min(abs(barrier_edge) * 0.2, 1.0)

    sect = intel.get("sectional_trend", {})
    if sect.get("fastest_closer_unplaced") or sect.get("pb_while_unplaced"):
        bonus += 1.0
    if sect.get("improving"):
        bonus += 0.5

    trainer = intel.get("trainer", {})
    synergy_edge = trainer.get("jockey_synergy_edge", 0) or 0
    if synergy_edge > 5:
        bonus += 0.5

    multiplier = max(0.80, min(1.30, multiplier))
    bonus = max(-3.0, min(4.0, bonus))

    return multiplier, bonus


def calibrate_and_score(horses, overround, race_class=""):
    """
    Apply market-anchored calibration and composite selection score.
    Detects flat MC output and adjusts scoring accordingly.
    """
    _calibrator = None
    try:
        from calibration_model import ProbabilityCalibrator
        _cal = ProbabilityCalibrator()
        if _cal.load():
            _calibrator = _cal
    except Exception:
        pass

    # Optional conditional-logit calibration (STRIDE_CL_BLEND=true).
    # The fit consumes prediction_audit probabilities, which mc_api logs at
    # exactly this stage (pre-isotonic, pre-ML-blend, pre-market-anchor), so
    # the transform is applied HERE to the incoming MC winPercentage — train
    # and serve stay on the same quantity (docs/12-hit-rate-research.md §4b).
    # It replaces the early isotonic stage when active; the downstream ML
    # blend and market anchor run unchanged, so prices remain market-
    # tethered even for a beta=0 fit. Default: off, byte-identical.
    _cl_blend = None
    if os.environ.get("STRIDE_CL_BLEND", "false").strip().lower() in ("true", "1", "yes"):
        try:
            from conditional_logit import ConditionalLogitBlend
            _cl = ConditionalLogitBlend()
            if _cl.load() and getattr(_cl, "stage", "mc") == "mc":
                _cl_blend = _cl
                print(f"    [CL_BLEND] Active at MC stage (alpha={_cl.alpha:.2f} model, beta={_cl.beta:.2f} market)", file=sys.stderr)
            elif _cl.is_fitted:
                print(f"    [CL_BLEND] Fitted artifact is stage='{getattr(_cl, 'stage', '?')}' — MC-stage hook refuses it", file=sys.stderr)
            else:
                print("    [CL_BLEND] Enabled but no fitted models/conditional_logit.json — using default stages", file=sys.stderr)
        except Exception as _cl_err:
            print(f"    [CL_BLEND] Unavailable ({_cl_err}) — using default stages", file=sys.stderr)

    if _cl_blend is not None:
        _quoted = []
        for h in horses:
            try:
                _o = float(h.get("marketOdds") or 0)
            except (TypeError, ValueError):
                _o = 0.0
            if _o > 1 and h.get("winPercentage", 0) > 0:
                _quoted.append((h, _o))
        if len(_quoted) >= 2:
            try:
                _imp = [1.0 / o for _, o in _quoted]
                _imp_sum = sum(_imp)
                _blended = _cl_blend.transform_race(
                    [h["winPercentage"] / 100.0 for h, _ in _quoted],
                    [x / _imp_sum for x in _imp],
                )
                for (h, _o), _p in zip(_quoted, _blended):
                    h["_mcWinBeforeCl"] = h["winPercentage"]
                    h["winPercentage"] = round(float(_p) * 100.0, 2)
                    h["_cl_blended"] = True
                # CL supersedes the early isotonic correction — never both.
                _calibrator = None
            except Exception as _cl_terr:
                print(f"    [CL_BLEND] transform failed (non-fatal): {_cl_terr}", file=sys.stderr)

    # Preserve the upstream MC engine's own ranking so the wrapper can refine it
    # rather than accidentally replacing it with a market-following score.
    upstream_scores = []
    for h in horses:
        try:
            upstream = float(h.get("selectionScore", 0) or 0)
        except (TypeError, ValueError):
            upstream = 0.0
        h["_mcSelectionScoreRaw"] = upstream
        upstream_scores.append(upstream)

    max_upstream_score = max(upstream_scores) if upstream_scores else 0.0
    for h in horses:
        upstream = h.get("_mcSelectionScoreRaw", 0.0)
        if max_upstream_score > 0:
            h["_mcSelectionScoreNorm"] = round((upstream / max_upstream_score) * 12.0, 3)
        else:
            h["_mcSelectionScoreNorm"] = 0.0

    for h in horses:
        raw = h.get("winPercentage", 0)
        try:
            odds = float(h.get("marketOdds") or 0)
        except (TypeError, ValueError):
            odds = 0.0

        if _calibrator and raw > 0:
            raw_corrected = _calibrator.calibrate([raw / 100.0])[0] * 100.0
            h["_rawBeforeIsotonic"] = raw
            raw = round(float(raw_corrected), 2)
            h["winPercentage"] = raw

        # Blend MC prob with ML ensemble if available (1B)
        # Favorites: trust MC more (ML weight 20% for odds <= 3, 40% elsewhere)
        ml_prob = h.get("mlPredictedProb", 0)
        if ml_prob > 0 and raw > 0:
            ml_w = 0.20 if (odds and odds <= 3) else 0.40
            raw = round((1 - ml_w) * raw + ml_w * ml_prob, 2)

        h["rawModelProb"] = raw

        if odds and odds > 1:
            # Shared overround-corrected market probability (task 05) —
            # identical to the legacy (100 / odds) / overround formula.
            true_market = true_market_prob_pct(odds, overround)

            # Model weight by odds band — trust model MORE at short prices
            # Kelly audit: $1-3 horses win 41%, model predicts 17% after blend.
            # Higher mw lets raw model signal through to edge calculation.
            if odds <= 3:
                mw = 0.80
            elif odds <= 6:
                mw = 0.70
            elif odds <= 10:
                mw = 0.50
            elif odds <= 15:
                mw = 0.45
            elif odds <= 30:
                mw = 0.40
            else:
                mw = 0.30

            calibrated = mw * raw + (1 - mw) * true_market
            h["winPercentage"] = round(calibrated, 2)
            h["trueMarketProb"] = round(true_market, 2)
            h["fairOdds"] = round(100.0 / true_market, 2) if true_market > 0 else None
            # Edge from calibrated (not raw) — prevents false positives from flat MC
            h["modelEdge"] = round(calibrated - true_market, 2)
        else:
            h["trueMarketProb"] = 0
            h["fairOdds"] = None
            h["modelEdge"] = 0

    # Task 05: per-race renormalisation after the mw market anchor
    # (STRIDE_RENORMALISE_FIELD, default OFF — legacy byte-identical when
    # off). The anchor leaves published winPercentage values that do not sum
    # to 100 within a race, so edge/EV are compared on inconsistent scales.
    # When on, edge and EV consume the renormalised probability and tier
    # transitions are logged, never silently dropped.
    if _flag_enabled("STRIDE_RENORMALISE_FIELD"):
        _renormalise_field(horses)

    raw_probs = [h.get("rawModelProb", 0) for h in horses]
    mc_spread = (max(raw_probs) - min(raw_probs)) if len(raw_probs) > 1 else 0
    mc_is_flat = mc_spread < 6.0
    if mc_is_flat and len(horses) > 1:
        print(f"    [MC_FLAT] Spread {mc_spread:.1f}pp — preserving MC spine and reducing wrapper overfit", file=sys.stderr)

    for h in horses:
        raw = h.get("rawModelProb", 0)
        calib = h["winPercentage"]
        edge = h.get("modelEdge", 0)
        try:
            odds = float(h.get("marketOdds") or 0)
        except (TypeError, ValueError):
            odds = 0.0
        mc_score_norm = h.get("_mcSelectionScoreNorm", 0.0)

        fitness = h.get("fitnessReadinessScore", 50)
        fitness_mult = 0.95 + (fitness / 100.0) * 0.10
        bias_pts = h.get("trackBiasPoints", 50)
        bias_mult = 0.95 + (bias_pts / 100.0) * 0.10
        jockey_mult = h.get("jockey_momentum_adjustment", 1.0)
        jockey_mult = max(0.85, min(1.20, jockey_mult))

        context_mult = fitness_mult * bias_mult * jockey_mult
        adjusted_raw = raw * context_mult
        adjusted_calib = calib * context_mult
        h["rawModelProb"] = round(adjusted_raw, 2)

        # Scoring: two independent signals (audit fix #2).
        # adjusted_calib = best probability estimate (model blended with market)
        # edge_term = model's disagreement with market (= where value lives)
        # Old formula triple-counted raw via raw + calib + edge; this counts
        # the calibrated estimate once plus edge once.
        edge_term = max(-10, min(10, edge))
        if mc_is_flat:
            prob_score = 0.80 * adjusted_calib + 0.20 * edge_term
        else:
            prob_score = 0.70 * adjusted_calib + 0.30 * edge_term

        # Odds-band multiplier removed from scoring (audit fix #1).
        # Scoring now ranks by model signal only; odds-based adjustment
        # is deferred to stake sizing (Kelly).
        base_score = prob_score

        # Intelligence signal adjustment — form franking, prep cycle, barrier edge, etc.
        intel_mult, intel_bonus = calculate_intelligence_adjustment(h)
        base_score = base_score * intel_mult + intel_bonus
        if intel_bonus != 0 or intel_mult != 1.0:
            h["_intel_mult"] = intel_mult
            h["_intel_bonus"] = round(intel_bonus, 2)
            print(f"    [INTEL] {h.get('horse','?')} mult={intel_mult:.2f} bonus={intel_bonus:+.1f}", file=sys.stderr)

        # Sectional bonus — uncapped (2E), use 65% credit with soft ceiling
        sect_enhanced = h.get("sectionalMcEnhanced", False)
        sect_win = h.get("sectionalMcWinProb")
        blended_base = h.get("blendedBaseProb")
        if sect_enhanced and sect_win is not None and blended_base is not None:
            sect_delta = sect_win - blended_base
            if sect_delta > 1.5:
                sect_bonus = sect_delta * 0.65
                if sect_bonus > 6.0:
                    sect_bonus = 6.0 + (sect_bonus - 6.0) * 0.3
                base_score += sect_bonus
                h["_sectionalScoreBonus"] = round(sect_bonus, 2)

        if calib < 5:
            base_score *= 0.30
        elif calib < 8:
            base_score *= 0.55

        if mc_is_flat:
            final_score = 0.65 * mc_score_norm + 0.35 * base_score
        else:
            final_score = 0.50 * mc_score_norm + 0.50 * base_score

        h["selectionScore"] = round(final_score, 2)
        from prediction_stages import record_stage
        _stages = dict(h.get("predictionStages") or {})
        record_stage(_stages, "wrapper_model_blend", h["rawModelProb"] / 100.0)
        record_stage(_stages, "market_context_probability", h["winPercentage"] / 100.0)
        record_stage(_stages, "selection_score", h["selectionScore"])
        h["predictionStages"] = _stages

    # Gradient flat MC penalty (2A) — based on confidence gap, not binary
    if mc_is_flat:
        sorted_probs = sorted([h.get("rawModelProb", 0) for h in horses], reverse=True)
        confidence_gap = sorted_probs[0] - sorted_probs[1] if len(sorted_probs) > 1 else 0

        if confidence_gap < 1.5:
            penalty_mult = 0.30
        elif confidence_gap < 3.0:
            penalty_mult = 0.60
        elif confidence_gap < 5.0:
            penalty_mult = 0.85
        else:
            penalty_mult = 1.00

        for h in horses:
            h["selectionScore"] = round(h.get("selectionScore", 0) * penalty_mult, 2)
            h["_mc_flat_penalty"] = penalty_mult
        print(f"    [MC_FLAT] gap={confidence_gap:.1f}pp, penalty={penalty_mult:.2f}", file=sys.stderr)

    return horses


def _renormalise_field(horses):
    """Per-race normalisation: winPercentage_i /= sum(field) (task 05).

    Runs after the mw market anchor, behind STRIDE_RENORMALISE_FIELD. Edge
    (modelEdge) and EV (compute_confidence, downstream) consume the
    renormalised probability; trueMarketProb is unchanged. Runners whose
    confidence tier would change are logged via a structured
    [RENORM_TIER_TRANSITION] line — never silently dropped (guardrail).
    """
    new_probs = renormalise_probs([h.get("winPercentage", 0) for h in horses])
    if sum(new_probs) <= 0:
        return
    transitions = 0
    for h, new_win in zip(horses, new_probs):
        pre_win = h.get("winPercentage", 0)
        pre_edge = h.get("modelEdge", 0)
        true_market = h.get("trueMarketProb", 0) or 0
        new_edge = round(new_win - true_market, 2) if true_market > 0 else pre_edge
        pre_tier = compute_confidence({**h, "winPercentage": pre_win, "modelEdge": pre_edge})
        post_tier = compute_confidence({**h, "winPercentage": new_win, "modelEdge": new_edge})
        h["_winPctBeforeRenorm"] = pre_win
        h["winPercentage"] = new_win
        h["modelEdge"] = new_edge
        if pre_tier != post_tier:
            transitions += 1
            print("    [RENORM_TIER_TRANSITION] " + json.dumps({
                "horse": h.get("horse", "?"),
                "tier_before": pre_tier,
                "tier_after": post_tier,
                "win_pct_before": pre_win,
                "win_pct_after": new_win,
                "edge_before": pre_edge,
                "edge_after": new_edge,
            }), file=sys.stderr)
    field_sum = sum(h.get("winPercentage", 0) for h in horses)
    print(f"    [RENORM] field sum -> {field_sum:.6f} "
          f"({transitions} tier transition(s))", file=sys.stderr)


def apply_safety_filters(scored_horses, race_class=""):
    """
    Apply graduated favourite preference, class-aware odds caps, and longshot safety.
    Returns top 3 horses sorted by adjusted selection score.
    """
    if not scored_horses:
        return []

    rc_upper = race_class.upper() if race_class else ""
    is_group1 = "GROUP 1" in rc_upper or "G1" in rc_upper
    is_group2 = "GROUP 2" in rc_upper or "G2" in rc_upper
    is_group3 = "GROUP 3" in rc_upper or "G3" in rc_upper
    is_listed = "LISTED" in rc_upper
    is_black_type = is_group1 or is_group2 or is_group3 or is_listed

    if is_group1:
        max_tip_odds = 25.0
    elif is_group2 or is_group3:
        max_tip_odds = 25.0
    elif is_listed:
        max_tip_odds = 30.0
    else:
        max_tip_odds = 30.0

    by_odds = sorted(scored_horses, key=lambda x: (x.get("marketOdds") or 999))
    fav = by_odds[0] if by_odds else None
    fav_name = (fav.get("horse") or "").lower() if fav else ""
    fav_odds = (fav.get("marketOdds") or 999) if fav else 999

    for h in scored_horses:
        h_odds = h.get("marketOdds") or 0
        raw = h.get("rawModelProb", 0)
        edge = h.get("modelEdge", 0)

        if edge >= 3 and raw >= 15:
            conv_bonus = 3.0
        elif edge >= 2 and raw >= 12:
            conv_bonus = 2.0
        elif edge >= 1 and raw >= 10:
            conv_bonus = 1.0
        else:
            conv_bonus = 0.0

        if conv_bonus > 0:
            h["selectionScore"] = round(h.get("selectionScore", 0) + conv_bonus, 2)
            print(f"    [CONVICTION] {h.get('horse','?')} (${h_odds}) +{conv_bonus:.1f} (edge={edge:+.1f}%, raw={raw:.1f}%)", file=sys.stderr)

    sorted_h = sorted(scored_horses, key=lambda x: x.get("selectionScore", 0), reverse=True)

    top_score = sorted_h[0].get("selectionScore", 0) if sorted_h else 0
    second_score = sorted_h[1].get("selectionScore", 0) if len(sorted_h) > 1 else 0

    filtered = []
    for h in sorted_h:
        h_odds = h.get("marketOdds") or 0
        h_raw = h.get("rawModelProb", 0)
        h_calib = h.get("winPercentage", 0)
        h_name = (h.get("horse") or "").lower()
        h_edge = h.get("modelEdge", 0)
        h_score = h.get("selectionScore", 0)

        # If the model has a clearly dominant overlay on top, let it through even
        # when the race-class odds cap would otherwise nudge the pipeline back to
        # the market favourite.
        if (
            h_score == top_score
            and top_score - second_score >= 1.5
            and h_odds <= 30
            and (h_edge > 1 or h_raw >= 10 or h_calib >= 10)
        ):
            filtered.append(h)
            continue

        # STRIDE: favourite only passes when the model supports it with positive edge
        # (no unconditional favourite bias — "do not tip the market favourite
        #  simply because it is the shortest price")
        if h_name == fav_name and fav_odds <= 20 and h_edge > 0:
            filtered.append(h)
            continue

        # LLM-backed pick in flat MC race — let it through (shown as LOW confidence)
        if h.get("_llm_top_pick"):
            filtered.append(h)
            continue

        # Banker override (4B) — dominant horses bypass all filters
        if h.get("banker_flag") and h.get("banker_score", 0) >= 70:
            filtered.append(h)
            print(f"    [BANKER_PASS] {h.get('horse','?')} (${h_odds}, banker_score={h.get('banker_score',0)})", file=sys.stderr)
            continue

        # Distance range filter (4A) — skip if horse never won at this distance range
        _max_wd = h.get("_max_win_dist")
        _min_wd = h.get("_min_win_dist")
        _race_d = h.get("_distance_m", 0)
        if _max_wd and _min_wd and _race_d:
            try:
                _max_d = int(str(_max_wd).replace("m", ""))
                _min_d = int(str(_min_wd).replace("m", ""))
                if _race_d > _max_d + 200 or _race_d < _min_d - 200:
                    print(f"    [DIST_RANGE] {h.get('horse','?')} never won {_min_d}-{_max_d}m, race is {_race_d}m", file=sys.stderr)
                    continue
            except (ValueError, TypeError):
                pass

        if h_odds > max_tip_odds:
            if h_odds <= 30 and h_edge > 1 and h_raw >= 8:
                filtered.append(h)
                print(f"    [CLASS_OVERRIDE] {h.get('horse','?')} at ${h_odds} kept on edge/raw merit", file=sys.stderr)
                continue
            print(f"    [CLASS_FILTER] Blocked {h.get('horse','?')} at ${h_odds} (max ${max_tip_odds} for {rc_upper or 'this class'})", file=sys.stderr)
            continue

        # Edge-based longshot filters (2C) — block only when NO edge AND low probability

        if h_odds >= 15 and h_edge > 2 and h_raw >= 8:
            filtered.append(h)
            continue

        if h_odds >= 30 and h_raw < 10 and h_edge <= 0:
            print(f"    [LONGSHOT_FILTER] Blocked {h.get('horse','?')} at ${h_odds} (raw={h_raw:.1f}%, edge={h_edge:+.1f}%)", file=sys.stderr)
            continue

        if h_odds >= 20 and h_edge <= 0 and h_raw < 8:
            print(f"    [LONGSHOT_FILTER] Blocked {h.get('horse','?')} at ${h_odds} (raw={h_raw:.1f}%, edge={h_edge:+.1f}%)", file=sys.stderr)
            continue

        if is_black_type and h_odds >= 10 and h_calib < 10 and h_edge <= 0:
            print(f"    [BLACK_TYPE_FILTER] Blocked {h.get('horse','?')} at ${h_odds} (calib={h_calib:.1f}%, edge={h_edge:+.1f}%)", file=sys.stderr)
            continue

        filtered.append(h)

    if not filtered:
        filtered = sorted(scored_horses, key=lambda x: (x.get("marketOdds") or 999))[:3]

    return filtered[:3]


# Confidence & staking — v2 odds-band-normalized EV + edge composite
# Replaces v1 heuristic gates that produced anti-correlation with EV.
# Phase A finding: v1 "high" tier had mean EV +0.036 (50% positive),
# while "low" had mean EV +0.152 — confidence was inversely correlated
# with value because raw-prob gates favoured short-priced horses the
# market had already priced correctly.
# Phase A research: 2026-04-14, n=330 horses across 3 race dates.
PACE_CLARITY_FILTER_ENABLED = True

def compute_confidence(h, pace_clarity=None, ev_mode=None):
    calib = h.get("winPercentage", 0) / 100.0
    odds = h.get("marketOdds") or 999
    edge = h.get("modelEdge", 0)
    true_mkt = h.get("trueMarketProb", 0) / 100.0

    # Which price the gate measures EV against (STRIDE_EV_GATE_AT_PRICE,
    # default OFF — legacy tiers are byte-identical when off).
    #
    # "devigged" (legacy): EV against trueMarketProb, which has the overround
    # removed. Because modelEdge = calibrated - true_market, calib/true_mkt - 1
    # is identically edge/true_mkt — so `ev > 0.0` is algebraically the same
    # test as `edge > 0.0` and contributes nothing. The HIGH gate below
    # therefore reduces to `edge > 1.0pp` at every price band.
    #
    # "at_price": EV against the quoted price the bet actually settles at.
    # Break-even needs edge > true_mkt * (overround - 1) — in a 118% book that
    # is 6.1pp at $2.50 and 3.0pp at $5.00, against a gate asking for 1.0pp.
    # The legacy form cannot see the vig it is being charged, so it rates
    # structurally losing bets HIGH at short prices.
    mode = ev_mode or ("at_price" if _flag_enabled("STRIDE_EV_GATE_AT_PRICE") else "devigged")
    if mode == "at_price":
        ev = (calib * odds) - 1.0 if 1.0 < odds < 999 else -999.0
    else:
        ev = (calib / true_mkt) - 1.0 if true_mkt > 0 else -999.0

    # Absolute longshot block — calibration unreliable above $30
    if odds > 30:
        return "low"

    # EV-based tiers — uniform across all price bands
    # HIGH  = genuinely +EV on the chosen price basis + 1pp edge guard against noise
    # MED   = positive model edge, EV near break-even (uncertain calibration tier)
    # LOW   = no model edge or negative EV
    # The MED branch must use the same price basis as HIGH, or the fix is
    # cosmetic: under flat_staking_enabled() (default on) high and medium both
    # stake 1u, so demoting a negative-EV bet from high to medium does not stop
    # it being backed. In "devigged" mode `ev > 0.0` and `edge > 0.0` are
    # algebraically the same test, but modelEdge is rounded to 2dp at
    # assignment, so the branches stay written out per mode to keep the legacy
    # path byte-identical rather than merely equivalent.
    if ev > 0.0 and edge > 1.0:
        result = "high"
    elif (ev > 0.0 if mode == "at_price" else edge > 0.0):
        result = "medium"
    else:
        result = "low"

    # Pace clarity filter: cap confidence in ambiguous pace scenarios
    if PACE_CLARITY_FILTER_ENABLED and pace_clarity is not None:
        if pace_clarity < 0.35 and result == "high":
            result = "medium"

    return result


def _log_ev_gate_transitions(horses, pace_clarity=None):
    """Report what the at-price EV gate changed, when it is switched on.

    Prints the denominator as well as the transition count on purpose: "0 of 0"
    and "0 of 3" are different claims, and only the second one means the gate
    ran and had nothing to change. A bare transition count would pass equally
    well when the gate is wired to nothing.
    """
    if not _flag_enabled("STRIDE_EV_GATE_AT_PRICE"):
        return
    changed = 0
    for h in horses:
        legacy = compute_confidence(h, pace_clarity=pace_clarity, ev_mode="devigged")
        current = h.get("confidence")
        if legacy != current:
            changed += 1
            print("    [EV_GATE_TRANSITION] " + json.dumps({
                "horse": h.get("horse", "?"),
                "tier_devigged": legacy,
                "tier_at_price": current,
                "market_odds": h.get("marketOdds"),
                "win_pct": h.get("winPercentage"),
                "edge": h.get("modelEdge"),
                "true_market_prob": h.get("trueMarketProb"),
            }), file=sys.stderr)
    print(f"    [EV_GATE] at-price EV: {changed} of {len(horses)} tip(s) changed tier",
          file=sys.stderr)


def compute_race_predictability(runners, race_class, distance_m, going):
    """
    Race-level predictability assessment (selectivity signal, docs/12 §5).
    Uses ONLY pre-race information — the current market and card facts, no
    historical lookups — so it is leak-free by construction. Deterministic
    heuristic when no fitted meta-model exists. Returns the predictability
    dict ({predictability_score, category, confidence_modifier, ...}) or
    None when unavailable.
    """
    try:
        from predictability_meta_model import get_predictability_model
        odds_list = [o for o in (extract_odds(r) for r in runners) if o and o > 1]
        if len(odds_list) < 2:
            return None
        race_info = {
            "n_runners": len(runners),
            "race_class": race_class or "",
            "distance_m": int(distance_m or 1400),
            "going": going or "",
            "favourite_odds": min(odds_list),
            "odds_list": odds_list,
        }
        return get_predictability_model().predict_predictability(race_info)
    except Exception:
        return None


def flat_staking_enabled() -> bool:
    """Task 06: flat 1u until net ROI's CI excludes zero. Default on (risk
    cut, not an experiment); STRIDE_FLAT_STAKING=false restores the 2u ladder."""
    return os.environ.get("STRIDE_FLAT_STAKING", "true").strip().lower() in ("true", "1", "yes")


def compute_staking(h):
    """Staking: flat 1u/1u/0u under STRIDE_FLAT_STAKING (default), else the
    legacy EV-mapped ladder (high=2u, medium=1u, low=skip)."""
    conf = h.get("confidence", "low")

    if flat_staking_enabled():
        return "1u" if conf in ("high", "medium") else "0u"
    if conf == "high":
        return "2u"
    if conf == "medium":
        return "1u"
    return "0u"


def fetch_prior_sectionals(horse_names, race_date):
    """Batch-fetch the latest PRIOR-start sectional features for a field
    (serve-time liveness plumbing, STRIDE_SERVE_LIVE_FEATURES).

    One query per race. Pre-race only: st.race_date < race_date (the same
    as-of rule as the training view's LATERAL in refresh_training_view_v2).
    Returns {lower(horse_name): {z_200m: ..., z_400m: ..., z_600m: ...,
    z_800m: ..., lambda_decay: ..., svi: ..., rsi: ...,
    trip_cost_seconds: ...}} with FEATURE_COLUMNS names (the view's prior_*
    prefix maps away per retrain_v2.VIEW_TO_FEATURE_MAP). Horses with no
    prior sectional row are simply absent — the builder serves NaN (never 0)
    for them.
    """
    from serve_features import SECTIONAL_LIVE_FEATURES
    out = {}
    names = sorted({(n or "").strip().lower() for n in horse_names if (n or "").strip()})
    if not names:
        return out
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ON (LOWER(horse_name))
                LOWER(horse_name), z_200m, z_400m, z_600m, z_800m,
                lambda_decay, svi, rsi, trip_cost_seconds
            FROM sectional_times
            WHERE LOWER(horse_name) = ANY(%s)
              AND race_date < %s
              AND (lambda_decay IS NOT NULL OR z_200m IS NOT NULL)
            ORDER BY LOWER(horse_name), race_date DESC
        """, (names, race_date))
        for row in cur.fetchall():
            out[row[0]] = {k: v for k, v in zip(SECTIONAL_LIVE_FEATURES, row[1:])}
    finally:
        conn.close()
    return out


def _write_serve_liveness_shadow(race_date, track, race_num, entries):
    """Append one race's shadow-liveness comparison to
    logs/serve_liveness_shadow_<date>.json (STRIDE_SERVE_LIVE_FEATURES_SHADOW),
    mirrored to the durable evidence store (S3) when one is configured —
    gate 3 counts from the store, and ephemeral Fargate disks don't count.

    Each entry: horse, legacy_prob_pct (published), live_prob_pct (would-be),
    delta_pp, plus field ranks under each variant. tier_change = the would-be
    top-3 in/out flip (the tiers the ML prob feeds). Never raises.
    """
    try:
        legacy_rank = {e["horse"]: i + 1 for i, e in enumerate(
            sorted(entries, key=lambda x: -x["legacy_prob_pct"]))}
        live_rank = {e["horse"]: i + 1 for i, e in enumerate(
            sorted(entries, key=lambda x: -x["live_prob_pct"]))}
        for e in entries:
            e["delta_pp"] = round(e["live_prob_pct"] - e["legacy_prob_pct"], 2)
            e["legacy_rank"] = legacy_rank[e["horse"]]
            e["live_rank"] = live_rank[e["horse"]]
            e["tier_change"] = (legacy_rank[e["horse"]] <= 3) != (live_rank[e["horse"]] <= 3)
        from evidence_store import fetch_evidence, put_evidence
        fname = f"serve_liveness_shadow_{race_date}.json"
        # Seed from the store so a fresh container appending to a date an
        # earlier run started never clobbers the earlier races.
        blocks = []
        try:
            prior = fetch_evidence(fname)
            if prior:
                blocks = json.loads(prior)
        except Exception as _fetch_err:
            # Registered dirty-day marker (shadow-flip-criteria.md #2).
            print(f"    [FEATURES] shadow log write failed (store fetch): "
                  f"{_fetch_err}", file=sys.stderr)
            blocks = []
        if not isinstance(blocks, list):
            blocks = []
        blocks.append({"track": track, "race_number": race_num,
                       "runners": entries})
        out = put_evidence(fname, json.dumps(blocks, indent=2, default=str))
        if out.get("s3_error"):
            print(f"    [FEATURES] shadow log write failed (s3): "
                  f"{out['s3_error']}", file=sys.stderr)
    except Exception as e:
        print(f"    [FEATURES] shadow log write failed (non-fatal): {e}", file=sys.stderr)


def enrich_with_db(horses, track, race_date, all_field_names=None):
    """Look up franking, prior sectionals, strike rates, and H2H for top horses."""
    try:
        conn = db_connect()
        cur = conn.cursor()
    except Exception as e:
        print(f"  [DB] Connection failed: {e}", file=sys.stderr)
        return horses

    if all_field_names is None:
        all_field_names = [h.get("horse", "") for h in horses if h.get("horse")]

    for h in horses:
        name = h.get("horse", "")
        if not name:
            continue
        factors = []

        try:
            cur.execute("""
                SELECT z_200m, z_400m, z_600m, z_800m, lambda_decay, svi
                FROM sectional_times
                WHERE LOWER(horse_name) = LOWER(%s)
                  AND race_date < %s
                  AND z_200m IS NOT NULL
                ORDER BY race_date DESC
                LIMIT 1
            """, (name, race_date))
            row = cur.fetchone()
            if row:
                z200, z400, z600, z800, lam, svi = row
                h["prior_z200"] = z200
                h["prior_z400"] = z400
                if z200 is not None and z200 > 1.0:
                    factors.append(f"Strong last 200m (z=+{z200:.1f})")
                elif z200 is not None and z200 < -1.0:
                    factors.append(f"Weak last 200m (z={z200:.1f})")
                if z600 is not None and z600 > 1.0:
                    factors.append(f"Strong mid-race speed (z_600=+{z600:.1f})")
                if svi is not None and svi > 1.05:
                    factors.append("Superior sustained velocity")
                if lam is not None and lam < 0.00015:
                    factors.append("Elite velocity maintenance")
        except Exception:
            pass

        try:
            cur.execute("""
                SELECT global_elo, franking_score, franking_confidence, anti_franked,
                       field_strength_avg, form_quality_trend, best_adjusted_margin, collateral_advantage
                FROM franking_scores
                WHERE LOWER(horse_name) = LOWER(%s)
                LIMIT 1
            """, (name,))
            row = cur.fetchone()
            if row:
                elo, fscore, fconf, anti, fsa, fqt, bam, ca = row
                h["frankingElo"] = elo
                h["frankingScore"] = fscore
                h["frankingConfidence"] = fconf
                h["isAntiFranked"] = bool(anti)
                h["fieldStrengthAvg"] = fsa
                h["formQualityTrend"] = fqt
                h["bestAdjustedMargin"] = bam
                h["collateralAdvantage"] = ca
                if elo and elo > 1600:
                    factors.append(f"Strong form franking (ELO {elo:.0f})")
                elif elo and elo > 1500:
                    factors.append(f"Solid form lines (ELO {elo:.0f})")
                if anti:
                    factors.append("CAUTION: Anti-franked form")
                if fsa and fsa > 1550:
                    factors.append(f"Raced against strong fields (avg ELO {fsa:.0f})")
                if fqt and str(fqt).lower() in ("improving", "up"):
                    factors.append("Form quality trending up")
        except Exception:
            pass

        try:
            dist_m = h.get("_distance_m", 0)
            dist_lo = dist_m - 100 if dist_m else 0
            dist_hi = dist_m + 100 if dist_m else 0
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE position = 1) AS wins,
                    COUNT(*) AS runs
                FROM race_results_history
                WHERE LOWER(horse_name) = LOWER(%s)
                  AND LOWER(track) = LOWER(%s)
                  AND distance_m BETWEEN %s AND %s
            """, (name, track, dist_lo, dist_hi))
            row = cur.fetchone()
            if row and row[1] and row[1] >= 2:
                wins, runs = row
                if wins and wins > 0:
                    factors.append(f"C&D winner {wins}/{runs}")
                else:
                    factors.append(f"C&D record 0/{runs}")
        except Exception:
            pass

        try:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE position = 1) AS wins,
                    COUNT(*) AS runs
                FROM race_results_history
                WHERE LOWER(horse_name) = LOWER(%s)
                  AND LOWER(track) = LOWER(%s)
            """, (name, track))
            row = cur.fetchone()
            if row and row[1] and row[1] >= 3:
                wins, runs = row
                rate = (wins / runs * 100) if runs else 0
                if rate >= 25:
                    factors.append(f"Course specialist ({wins}/{runs}, {rate:.0f}%)")
        except Exception:
            pass

        try:
            cur.execute("""
                SELECT track, race_date, distance_m, race_class, class_level,
                       position, margin_lengths, sp_odds, going, field_size,
                       race_name, jockey, barrier, weight_kg
                FROM race_results_history
                WHERE LOWER(horse_name) = LOWER(%s) AND race_date < %s
                ORDER BY race_date DESC LIMIT 5
            """, (name, race_date))
            rows = cur.fetchall()
            h["recent_runs"] = [
                {"track": r[0], "date": str(r[1]), "distance_m": r[2], "race_class": r[3],
                 "class_level": r[4], "position": r[5], "margin": r[6], "sp": r[7],
                 "going": r[8], "field_size": r[9], "race_name": r[10],
                 "jockey": r[11], "barrier": r[12], "weight": r[13]}
                for r in rows
            ]
        except Exception:
            pass

        try:
            opponents = [n for n in all_field_names if n.lower() != name.lower()]
            if opponents:
                placeholders = ",".join(["%s"] * len(opponents))
                cur.execute(f"""
                    SELECT r2.horse_name AS opponent,
                           COUNT(*) FILTER (WHERE r1.actual_position < r2.actual_position) AS wins,
                           COUNT(*) FILTER (WHERE r1.actual_position > r2.actual_position) AS losses,
                           COUNT(*) AS meetings
                    FROM training_data r1
                    JOIN training_data r2 ON r1.race_id = r2.race_id
                    WHERE LOWER(r1.horse_name) = LOWER(%s)
                      AND LOWER(r2.horse_name) IN ({placeholders})
                      AND r1.actual_position IS NOT NULL
                      AND r2.actual_position IS NOT NULL
                    GROUP BY r2.horse_name
                    HAVING COUNT(*) >= 1
                """, [name] + [o.lower() for o in opponents])
                h2h_results = []
                for row in cur.fetchall():
                    opp, wins, losses, meets = row
                    h2h_results.append({"opponent": opp, "wins": wins, "losses": losses, "meetings": meets})
                    if wins > losses and meets >= 2:
                        factors.append(f"Beaten {opp} in {wins}/{meets} meetings")
                    elif losses > wins and meets >= 2:
                        factors.append(f"{opp} has won {losses}/{meets} against this horse")
                h["h2h_field"] = h2h_results
        except Exception:
            pass

        if h.get("isClassDrop"):
            factors.append("Class drop")
        if h.get("isClassRise"):
            factors.append("Class rise")
        if h.get("isFirstUp"):
            factors.append("First-up")
        if h.get("isSecondUp"):
            factors.append("Second-up")
        if h.get("isImproving"):
            factors.append("Improving form")
        if h.get("isEliteJockey"):
            jockey = h.get("jockey", "")
            factors.append(f"Elite jockey ({jockey})" if jockey else "Elite jockey")
        if h.get("isEliteTrainer"):
            factors.append("Elite trainer")
        if h.get("isSteamMove"):
            factors.append("Market support (steam)")
        if h.get("isBlinkersFirstTime") or h.get("hasBlinkersFirstTime"):
            factors.append("Blinkers first time")
        if h.get("trackBiasPoints", 0) >= 25:
            factors.append(f"Strong track bias fit ({h['trackBiasPoints']}pts)")
        if h.get("fitnessData") and h["fitnessData"].get("isAtPeakRun"):
            label = h["fitnessData"].get("runLabel", "")
            factors.append(f"Peak fitness ({label})" if label else "Peak fitness")
        if h.get("barrierAdvantage", 0) > 0.15:
            factors.append(f"Favourable barrier ({h.get('barrier', '?')})")

        h["key_factors"] = factors

    # Compute collateral advantage: each horse's ELO vs field average ELO.
    # The DB stores 0.0 because it's computed at batch time without field context.
    field_elos = [h.get("frankingElo") for h in horses if h.get("frankingElo")]
    if field_elos:
        avg_elo = sum(field_elos) / len(field_elos)
        for h in horses:
            elo = h.get("frankingElo")
            if elo:
                h["collateralAdvantage"] = round(elo - avg_elo, 2)

    try:
        cur.close()
        conn.close()
    except Exception:
        pass

    return horses


def _safe_json(val):
    """Safely serialize a value to JSON string, or return None."""
    if val is None:
        return None
    try:
        return json.dumps(val, default=str)
    except Exception:
        return None


def launch_late_odds_watcher(date_str):
    """ROI task 04 (G1): start the day's T-5min late-odds watcher, detached.

    The tips run is the one step every real invocation path shares — the
    /stride-full runbook and the Node scheduler both reach run_tips_pipeline
    without ever running run_full_pipeline.py, so a watcher wired only into
    that orchestrator leaves the odds clock dead under automation. Launching
    here (fire-and-forget, start_new_session) covers every path;
    capture_late_odds deduplicates via a per-date pidfile, so a second launch
    from run_full_pipeline is a no-op. Gated by the same
    STRIDE_ODDS_SNAPSHOT_WRITE switch as the tip_time hook
    (STRIDE_SKIP_ODDS_WATCH=true also skips), and a launch failure must never
    break tipping.
    """
    try:
        if os.environ.get("STRIDE_SKIP_ODDS_WATCH", "false").strip().lower() in ("true", "1", "yes"):
            print("  [LATE_ODDS] watcher skipped (STRIDE_SKIP_ODDS_WATCH)", file=sys.stderr)
            return
        from odds_snapshots import snapshot_write_enabled
        if not snapshot_write_enabled():
            print("  [LATE_ODDS] watcher skipped (STRIDE_ODDS_SNAPSHOT_WRITE off)", file=sys.stderr)
            return
        script_dir = Path(__file__).resolve().parent
        log_path = script_dir / "logs" / f"late_odds_{date_str}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as logf:
            proc = subprocess.Popen(
                [sys.executable, str(script_dir / "capture_late_odds.py"),
                 "--watch", "--date", date_str],
                cwd=str(script_dir), stdout=logf, stderr=logf,
                start_new_session=True)
        print(f"  [LATE_ODDS] watcher launched (pid {proc.pid}) -> {log_path.name}",
              file=sys.stderr)
    except Exception as e:  # a monitoring miss must never fail tipping
        print(f"  [LATE_ODDS] watcher launch failed (non-fatal): {e}", file=sys.stderr)


def persist_selection_ledger(race_tips, date_str):
    """Write one selection-ledger row per race — bet or refused (roi-roadmap 01).

    Measurement only: a no-op unless STRIDE_LEDGER_WRITE=true (default off),
    and every failure is logged and swallowed — this must never delay or break
    the tips pipeline. Refused rows carry the would-be price so EV-gate
    quality becomes measurable; they are staked 0u and can never book P&L.
    """
    try:
        from selection_ledger import _stride_flag, build_ledger_row, persist_rows
    except Exception as _imp_err:
        print(f"  [LEDGER] selection_ledger unavailable: {_imp_err}", file=sys.stderr)
        return
    if not _stride_flag("STRIDE_LEDGER_WRITE"):
        return

    rows = []
    for race in race_tips:
        if race.get("error"):
            continue
        race_key = {"race_date": date_str, "track": race.get("track"),
                    "race_number": race.get("race_number")}
        bet_pick = race.get("bet_pick")
        if isinstance(bet_pick, dict) and bet_pick.get("should_bet"):
            rows.append(build_ledger_row(bet_pick, race_key, refused=False))
            continue
        leader = race.get("refused_bet_pick") or race.get("raw_model_leader")
        if not isinstance(leader, dict) or not leader.get("horse"):
            continue
        refused_pick = dict(leader)
        refused_pick["should_bet"] = False
        refused_pick["staking"] = "0u"  # a refused row must never book P&L
        refused_pick["selection_origin"] = (
            leader.get("selection_origin") or "ev_gate_refused")
        rows.append(build_ledger_row(refused_pick, race_key, refused=True))
        for _pick in race.get("top_picks") or []:
            if not _pick.get("_crowd_promotion_blocked"):
                continue
            _shadow = dict(_pick)
            _shadow["should_bet"] = False
            _shadow["staking"] = "0u"
            _shadow["selection_origin"] = "crowd_promotion_blocked"
            rows.append(build_ledger_row(_shadow, race_key, refused=True))

    if not rows:
        return
    try:
        conn = db_connect()
    except Exception as _db_err:
        print(f"  [LEDGER] DB unavailable — {len(rows)} row(s) not written: {_db_err}",
              file=sys.stderr)
        return
    try:
        persist_rows(conn, rows)
    except Exception as _write_err:
        print(f"  [LEDGER] write failed (pipeline unaffected): {_write_err}",
              file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def store_selections_in_db(race_tips, date_str):
    """Insert top picks into the selections table with full enrichment data."""
    try:
        conn = db_connect()
        cur = conn.cursor()
    except Exception as e:
        print(f"  [DB] Cannot store selections: {e}", file=sys.stderr)
        return

    tracks_in_batch = list(set(r["track"] for r in race_tips))
    try:
        for t in tracks_in_batch:
            cur.execute(
                "UPDATE selections SET is_active = false WHERE race_date = %s AND track = %s",
                (date_str, t),
            )
    except Exception as deact_err:
        # Inserting anyway would leave the previous picks live alongside the
        # new ones (duplicate active selections for the same date+track), so
        # the store aborts loudly and leaves the DB in its previous state.
        print(
            f"  [DB] Selection store ABORTED for {date_str}: could not "
            f"deactivate previous selections for {tracks_in_batch}: {deact_err}",
            file=sys.stderr,
        )
        try:
            conn.rollback()
            cur.close()
            conn.close()
        except Exception:
            pass
        return

    inserted = 0
    for race in race_tips:
        track = race["track"]
        race_num = race["race_number"]
        race_name = race.get("race_name", "")
        distance = race.get("distance", "")
        going = race.get("going", "")

        # ``bet_status`` is the race-level source of truth after crowd/risk
        # reconciliation.  In particular, never resurrect a vetoed bet from
        # the pre-gate ``primary_pick`` compatibility alias.
        stored_picks = []
        if race.get("bet_status") != "NO_BET":
            explicit_bet_pick = race.get("bet_pick")
            if isinstance(explicit_bet_pick, dict) and explicit_bet_pick.get("should_bet"):
                stored_picks = [explicit_bet_pick]
            else:
                fallback_primary = race.get("primary_pick")
                if isinstance(fallback_primary, dict) and fallback_primary.get("should_bet"):
                    stored_picks = [fallback_primary]

        for pick in stored_picks:
            edge = pick.get("edge_pct", 0)
            value_rating = (
                "Excellent" if edge > 3 else
                "Good" if edge > 1.5 else
                "Fair" if edge > 0 else
                "Poor"
            )

            mc = pick.get("_mc_data", {})
            fitness = mc.get("fitnessData") or {}
            class_mv = mc.get("classMovement") or {}
            barrier_b = mc.get("barrierBias") or {}
            h2h = mc.get("headToHead") or {}
            intel_franking = (mc.get("_intelligence") or {}).get("franking") or {}
            tb_breakdown = mc.get("trackBiasBreakdown") or {}
            tb_descs = mc.get("trackBiasDescriptions") or {}
            pace_scenario = mc.get("paceScenarioDistribution") or mc.get("paceScenario")
            ml_breakdown = mc.get("mlAdjustmentBreakdown")

            mc_explanations = mc.get("enhancedExplanations", [])
            if isinstance(mc_explanations, list):
                all_explanations = mc_explanations + pick.get("key_factors", [])
            else:
                all_explanations = pick.get("key_factors", [])
            seen = set()
            unique_explanations = []
            for ex in all_explanations:
                if ex and ex not in seen:
                    seen.add(ex)
                    unique_explanations.append(ex)

            try:
                cur.execute("""
                    INSERT INTO selections (
                        track, race_number, race_name, race_date, distance,
                        horse_name, horse_number, barrier, jockey, trainer, form,
                        win_percentage, place_percentage, model_probability,
                        market_odds, expected_value, edge,
                        kelly_stake, confidence, value_rating,
                        is_active, running_style,
                        enhanced_explanations,
                        -- Franking fields
                        franking_elo, franking_score, franking_confidence,
                        is_anti_franked, field_strength_avg, form_quality_trend,
                        best_adjusted_margin, collateral_advantage,
                        -- Graph franking fields
                        pagerank_authority, community_strength, form_stability,
                        graph_franking_score, graph_franking_depth,
                        graph_franking_independence,
                        market_validated_franking, bridge_score,
                        -- Fitness fields
                        fitness_runs_this_prep, fitness_run_label, fitness_is_at_peak_run,
                        fitness_readiness_score, fitness_prep_trajectory,
                        fitness_description, fitness_data_json,
                        -- Recalibration fields
                        recalibration_applied, raw_win_prob, calibrated_win_prob,
                        recalibration_shift,
                        -- Sectional MC fields
                        sectional_mc_enhanced, sectional_mc_win_prob,
                        sectional_edge_multiplier, sectional_edge_insight,
                        -- Enhanced feature fields
                        enhanced_factor,
                        class_movement_factor, class_movement_desc,
                        barrier_bias_factor, barrier_bias_desc,
                        head_to_head_factor, head_to_head_desc,
                        -- Track bias fields
                        track_bias_points, track_bias_fit, track_bias_summary,
                        track_bias_barrier_pts, track_bias_pace_pts,
                        track_bias_jockey_pts, track_bias_trainer_pts,
                        -- ML model fields
                        ml_model_active, ml_adjustment_breakdown,
                        -- Market/speed fields
                        speed_rating, pace_score, expected_pace_advantage,
                        -- Form detail fields
                        weighted_form_score, ground_suitability,
                        distance_strike_rate, course_strike_rate,
                        is_course_distance_winner, is_first_up, is_second_up,
                        days_since_run,
                        -- Pace scenario
                        pace_scenario_json,
                        -- Stability
                        stability_score, ci_lower, ci_upper,
                        expected_position,
                        -- AI reasoning fields
                        ai_insight, ai_insight_generated_at,
                        ai_score, ai_reasoning_json, llm_provider,
                        -- Luckless fields
                        luckless_flag, luckless_score, luckless_uplift,
                        luckless_explanation, luckless_json,
                        -- Consensus convergence fields
                        consensus_score, consensus_mention_count,
                        consensus_bucket_diversity,
                        market_signal_score, market_signal_category,
                        convergence_score, convergence_tier,
                        -- Consensus V2 fields
                        selection_score_raw, consensus_vote_pct,
                        consensus_injection, consensus_injection_reason,
                        market_injection, market_injection_reason,
                        convergence_gate,
                        market_confidence_score, market_confidence_label,
                        market_confidence_colour,
                        tipsters_polled, independent_source_rate
                    ) VALUES (
                        %(v01)s, %(v02)s, %(v03)s, %(v04)s, %(v05)s,
                        %(v06)s, %(v07)s, %(v08)s, %(v09)s, %(v10)s, %(v11)s,
                        %(v12)s, %(v13)s, %(v14)s,
                        %(v15)s, %(v16)s, %(v17)s,
                        %(v18)s, %(v19)s, %(v20)s,
                        %(v21)s, %(v22)s,
                        %(v23)s,
                        %(v24)s, %(v25)s, %(v26)s,
                        %(v27)s, %(v28)s, %(v29)s,
                        %(v30)s, %(v31)s,
                        %(vg1)s, %(vg2)s, %(vg3)s,
                        %(vg4)s, %(vg5)s,
                        %(vg6)s,
                        %(vg7)s, %(vg8)s,
                        %(v32)s, %(v33)s, %(v34)s,
                        %(v35)s, %(v36)s,
                        %(v37)s, %(v38)s,
                        %(v39)s, %(v40)s, %(v41)s,
                        %(v42)s,
                        %(v43)s, %(v44)s,
                        %(v45)s, %(v46)s,
                        %(v47)s,
                        %(v48)s, %(v49)s,
                        %(v50)s, %(v51)s,
                        %(v52)s, %(v53)s,
                        %(v54)s, %(v55)s, %(v56)s,
                        %(v57)s, %(v58)s,
                        %(v59)s, %(v60)s,
                        %(v61)s, %(v62)s,
                        %(v63)s, %(v64)s,
                        %(v65)s, %(v66)s, %(v67)s,
                        %(v68)s, %(v69)s,
                        %(v70)s, %(v71)s,
                        %(v72)s, %(v73)s,
                        %(v74)s, %(v75)s, %(v76)s,
                        %(v77)s,
                        %(v78)s,
                        %(v79)s, %(v80)s,
                        %(v81)s, %(v82)s, %(v83)s,
                        %(v84)s, %(v85)s, %(v86)s,
                        %(v87)s, %(v88)s,
                        %(v89)s, %(v90)s, %(v91)s,
                        %(v92)s, %(v93)s,
                        %(v94)s, %(v95)s,
                        %(v96)s, %(v97)s,
                        %(v98)s, %(v99)s,
                        %(v100)s, %(v101)s,
                        %(v102)s,
                        %(v103)s, %(v104)s,
                        %(v105)s,
                        %(v106)s, %(v107)s
                    )
                """, {
                    "v01": track, "v02": race_num, "v03": race_name, "v04": date_str, "v05": distance,
                    "v06": pick.get("horse", ""), "v07": str(pick.get("rank", "")),
                    "v08": str(pick.get("barrier", "")),
                    "v09": pick.get("jockey", ""), "v10": pick.get("trainer", ""),
                    "v11": pick.get("form", ""),
                    "v12": pick.get("win_pct", 0), "v13": mc.get("placePercentage", 0),
                    "v14": pick.get("raw_model_pct", 0),
                    "v15": pick.get("odds", 0),
                    "v16": (pick.get("win_pct", 0) / 100 * (pick.get("odds", 0) or 1) - 1) if pick.get("odds") else 0,
                    "v17": edge,
                    "v18": int(pick.get("staking", "0u").replace("u", "") or 0),
                    "v19": pick.get("confidence", "low"), "v20": value_rating,
                    "v21": True, "v22": pick.get("running_style", "unknown"),
                    "v23": " | ".join(unique_explanations),
                    "v24": mc.get("frankingElo") or mc.get("franking_elo"),
                    "v25": mc.get("frankingScore"),
                    "v26": mc.get("frankingConfidence"),
                    "v27": bool(mc.get("isAntiFranked")),
                    "v28": mc.get("fieldStrengthAvg"),
                    "v29": mc.get("formQualityTrend"),
                    "v30": mc.get("bestAdjustedMargin"),
                    "v31": mc.get("collateralAdvantage"),
                    "vg1": intel_franking.get("pagerank_authority"),
                    "vg2": intel_franking.get("community_strength"),
                    "vg3": intel_franking.get("form_stability"),
                    "vg4": intel_franking.get("graph_franking_score"),
                    "vg5": intel_franking.get("graph_franking_depth"),
                    "vg6": intel_franking.get("graph_franking_independence"),
                    "vg7": intel_franking.get("market_validated_franking"),
                    "vg8": intel_franking.get("bridge_score"),
                    "v32": fitness.get("runsThisPrep"),
                    "v33": fitness.get("runLabel"),
                    "v34": bool(fitness.get("isAtPeakRun")),
                    "v35": fitness.get("fitnessReadinessScore"),
                    "v36": fitness.get("prepFormTrajectory"),
                    "v37": fitness.get("description"),
                    "v38": _safe_json(fitness) if fitness else None,
                    "v39": bool(mc.get("recalibrationApplied")),
                    "v40": mc.get("rawWinProb"),
                    "v41": mc.get("calibratedWinProb"),
                    "v42": mc.get("recalibrationShift"),
                    "v43": bool(mc.get("sectionalMcEnhanced")),
                    "v44": mc.get("sectionalMcWinProb"),
                    "v45": mc.get("sectionalEdgeMultiplier"),
                    "v46": mc.get("sectionalEdgeInsight"),
                    "v47": mc.get("enhancedFactor"),
                    "v48": class_mv.get("factor"),
                    "v49": class_mv.get("description") or class_mv.get("desc"),
                    "v50": barrier_b.get("factor"),
                    "v51": barrier_b.get("description") or barrier_b.get("desc"),
                    "v52": h2h.get("factor"),
                    "v53": h2h.get("description") or h2h.get("desc"),
                    "v54": mc.get("trackBiasPoints"),
                    "v55": mc.get("trackBiasFit"),
                    "v56": mc.get("trackBiasSummary"),
                    "v57": tb_breakdown.get("barrier_pts") or tb_breakdown.get("barrier", 0),
                    "v58": tb_breakdown.get("pace_pts") or tb_breakdown.get("pace", 0),
                    "v59": tb_breakdown.get("jockey_pts") or tb_breakdown.get("jockey", 0),
                    "v60": tb_breakdown.get("trainer_pts") or tb_breakdown.get("trainer", 0),
                    "v61": bool(mc.get("mlModelActive")),
                    "v62": _safe_json(ml_breakdown),
                    "v63": mc.get("speedRating"),
                    "v64": mc.get("paceScore"),
                    "v65": mc.get("expectedPaceAdvantage") or mc.get("paceAdvantage"),
                    "v66": mc.get("weightedFormScore"),
                    "v67": mc.get("groundSuitability"),
                    "v68": mc.get("distanceStrikeRate") or mc.get("distanceSR"),
                    "v69": mc.get("courseStrikeRate") or mc.get("courseSR"),
                    "v70": bool(mc.get("isCourseDistanceWinner")),
                    "v71": bool(mc.get("isFirstUp")),
                    "v72": bool(mc.get("isSecondUp")),
                    "v73": mc.get("daysSinceRun"),
                    "v74": _safe_json(pace_scenario),
                    "v75": mc.get("stabilityScore"),
                    "v76": mc.get("ciLower"),
                    "v77": mc.get("ciUpper"),
                    "v78": mc.get("expectedPosition"),
                    "v79": pick.get("ai_insight") or mc.get("ai_insight"),
                    "v80": mc.get("ai_insight_generated_at"),
                    "v81": pick.get("ai_score") or mc.get("ai_score"),
                    "v82": _safe_json({
                        "ai_analysis": mc.get("ai_analysis", ""),
                        "ai_key_edge": mc.get("ai_key_edge", ""),
                        "ai_risk_factors": mc.get("ai_risk_factors", []),
                        "ai_vs_field": mc.get("ai_vs_field", ""),
                    }) if mc.get("ai_analysis") else None,
                    "v83": os.environ.get("LLM_PROVIDER", "groq") if pick.get("ai_score") else None,
                    "v84": pick.get("luckless_flag", False),
                    "v85": pick.get("luckless_score", 0) or None,
                    "v86": pick.get("luckless_uplift", 0) or None,
                    "v87": pick.get("luckless_explanation") or None,
                    "v88": _safe_json(pick.get("luckless_json")) if pick.get("luckless_json") else None,
                    "v89": pick.get("consensus_score"),
                    "v90": pick.get("consensus_mentions"),
                    "v91": pick.get("consensus_bucket_spread"),
                    "v92": pick.get("market_signal_score"),
                    "v93": pick.get("market_signal_category"),  # BUG FIX: was convergence_tier
                    "v94": pick.get("convergence_score"),
                    "v95": pick.get("convergence_tier"),
                    "v96": pick.get("selection_score_raw"),
                    "v97": pick.get("consensus_vote_pct"),
                    "v98": pick.get("consensus_injection"),
                    "v99": pick.get("consensus_injection_reason"),
                    "v100": pick.get("market_injection"),
                    "v101": pick.get("market_injection_reason"),
                    "v102": pick.get("convergence_gate"),
                    "v103": pick.get("market_confidence_score"),
                    "v104": pick.get("market_confidence_label"),
                    "v105": pick.get("market_confidence_colour"),
                    "v106": pick.get("tipsters_polled"),
                    "v107": pick.get("independent_source_rate"),
                })
                inserted += 1
            except Exception as e:
                print(f"  [DB] Insert failed for {pick.get('horse')}: {e}", file=sys.stderr)
                try:
                    conn.rollback()
                except Exception:
                    pass

    try:
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

    print(f"  [DB] Stored {inserted} selections for {date_str}", file=sys.stderr)


def store_final_probs_in_audit(race_tips, date_str):
    """
    Persist the wrapper-final win probability for EVERY runner into
    prediction_audit.final_win_prob. mc_api already logs the MC-stage
    probability at simulation time; this adds the published end-of-pipeline
    number so future conditional-logit fits can use genuinely final inputs
    (docs/12-hit-rate-research.md §5 item 1). Non-fatal and idempotent —
    a plain UPDATE keyed on the audit's unique (date, track, race, horse).
    """
    rows = []
    for race in race_tips:
        track = race.get("track")
        race_num = race.get("race_number")
        for f in race.get("full_field", []) or []:
            win_pct = f.get("win_pct")
            horse = f.get("horse")
            if track and race_num and horse and win_pct is not None:
                rows.append((float(win_pct), date_str, str(track), int(race_num), str(horse)))
    if not rows:
        return
    conn = None
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("ALTER TABLE prediction_audit ADD COLUMN IF NOT EXISTS final_win_prob NUMERIC")
        cur.executemany("""
            UPDATE prediction_audit SET final_win_prob = %s
            WHERE race_date = %s AND LOWER(track) = LOWER(%s)
              AND race_number = %s AND LOWER(horse_name) = LOWER(%s)
        """, rows)
        conn.commit()
        print(f"  [DB] final_win_prob recorded for {len(rows)} runners", file=sys.stderr)
        cur.close()
        conn.close()
    except Exception as e:
        print(f"  [DB] final_win_prob update failed (non-fatal): {e}", file=sys.stderr)
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def crowd_gate_only_enabled() -> bool:
    """Task 07: the crowd may confirm, downgrade, or veto a bet; it may never
    create one. Default on (safety fix); STRIDE_CROWD_GATE_ONLY=false restores
    the legacy promotion path byte-identically."""
    return os.environ.get("STRIDE_CROWD_GATE_ONLY", "true").strip().lower() in ("true", "1", "yes")


def apply_crowd_gate(current_should_bet: bool, gate_should_bet: bool,
                     gate_only: bool):
    """(new_should_bet, action). Actions: confirm, veto, promote, blocked, none.
    With gate_only, the promote transition is forbidden for every input."""
    if current_should_bet and not gate_should_bet:
        return False, "veto"
    if not current_should_bet and gate_should_bet:
        if gate_only:
            return False, "blocked"
        return True, "promote"
    return current_should_bet, ("confirm" if current_should_bet else "none")


def load_racecard(date_str):
    """Load racecard JSON for the given date."""
    racecard_path = RACECARDS_DIR / f"racecard_{date_str}.json"
    if not racecard_path.exists():
        print(f"[ERROR] No racecard found at {racecard_path}", file=sys.stderr)
        return None
    with open(racecard_path) as f:
        return json.load(f)


def filter_active_runners(runners):
    """Remove scratched/withdrawn runners."""
    active = []
    for r in runners:
        scratched = r.get("scratched") in (True, "true", "True")
        status = str(r.get("status", "")).lower()
        is_out = status in ("scratched", "non-runner", "withdrawn", "removed")
        if not scratched and not is_out:
            active.append(r)
    return active


def parse_distance_m(dist_str):
    """Parse distance string to metres (e.g. '1400m' -> 1400, '1400' -> 1400)."""
    if not dist_str:
        return 0
    s = str(dist_str).lower().replace("m", "").strip()
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def build_tips_output_path(date_str, output_suffix=None):
    """Return the canonical or suffixed tips output path."""
    if output_suffix:
        safe_suffix = re.sub(r"[^a-z0-9]+", "-", str(output_suffix).lower()).strip("-")
        if safe_suffix:
            return RACECARDS_DIR / f"tips_{date_str}_{safe_suffix}.json"
    return RACECARDS_DIR / f"tips_{date_str}.json"


def _safe_num(value, default=0.0):
    try:
        parsed = float(value)
        return parsed
    except (TypeError, ValueError):
        return default


def build_export_pick(horse, rank=None):
    if not horse:
        return None

    from prediction_stages import finalise_stages
    return {
        "rank": rank,
        "horse": horse.get("horse", "Unknown"),
        "barrier": horse.get("barrier", 0),
        "jockey": horse.get("jockey", ""),
        "trainer": horse.get("trainer", ""),
        "odds": horse.get("marketOdds", 0),
        "has_real_market_odds": horse.get("hasRealMarketOdds", False),
        "price_source": horse.get("priceSource")
                        or ("betfair" if horse.get("hasRealMarketOdds") else "none"),
        "inferred_market_odds": horse.get("inferredMarketOdds"),
        "fair_odds": horse.get("fairOdds"),
        "win_pct": round(horse.get("winPercentage", 0), 1),
        "raw_model_pct": round(horse.get("rawModelProb", 0), 1),
        "edge_pct": round(horse.get("modelEdge", 0), 1),
        "confidence": horse.get("confidence", "low"),
        "selection_score": round(horse.get("selectionScore", 0), 1),
        "prediction_stages": finalise_stages(horse),
        "staking": horse.get("staking", "0u"),
        "key_factors": horse.get("key_factors", []),
        "value_rating": horse.get("valueRating", "Fair"),
        "running_style": horse.get("runningStyle", "unknown"),
        "form": horse.get("form", ""),
        "ai_score": horse.get("ai_score"),
        "ai_insight": horse.get("ai_insight", ""),
        "luckless_flag": horse.get("luckless_flag", False),
        "luckless_score": horse.get("luckless_score", 0),
        "luckless_uplift": horse.get("luckless_uplift", 0),
        "luckless_explanation": horse.get("luckless_explanation", ""),
        "luckless_json": horse.get("luckless_json"),
        "franking_class": (horse.get("_intelligence", {}).get("franking_class") or
                           {"reference_class": "Unknown", "group_listed_in_chain": False}),
        "_mc_data": horse,
    }


def annotate_pick_contract(pick, model_leader_horse, selection_origin, selection_origin_reason, should_bet):
    if not pick:
        return None

    selected = dict(pick)
    horse_name = str(selected.get("horse", "")).strip().lower()
    model_leader_name = str(model_leader_horse or "").strip().lower()
    selected["selection_origin"] = selection_origin
    selected["selection_origin_reason"] = selection_origin_reason
    selected["matches_model_leader"] = bool(model_leader_name) and horse_name == model_leader_name
    selected["model_leader_horse"] = model_leader_horse
    selected["should_bet"] = bool(should_bet)
    return selected


def _check_intelligence_override(pick):
    """
    Intelligence override for the BET gate.

    Promotes rank-1 picks to BET when intelligence signals are strong enough
    to justify a bet regardless of edge calculation. Requires:
    - Rank 1 by the model
    - intel_bonus >= 3.0 (only achievable with DEEP_FRANKED + peak prep)
    - Non-DECLINING trajectory
    - franking_score >= 55 (above FRANKED threshold)

    With class-weighted franking, Group-franked horses naturally score higher
    and earn larger bonuses, so this gate is self-calibrating.

    Returns (should_override: bool, reason: str).
    """
    rank = pick.get("rank")
    if rank != 1:
        return False, ""

    mc = pick.get("_mc_data", {})
    bonus = _safe_num(mc.get("_intel_bonus"), 0.0)
    if bonus < 3.0:
        return False, ""

    intel = mc.get("_intelligence", {})

    # Require the franking score itself to be above FRANKED level
    franking = intel.get("franking", {})
    fk_score = _safe_num(franking.get("form_franking_score"), 0)
    if fk_score < 55:
        return False, ""

    prep = intel.get("prep_cycle", {})
    trajectory = str(prep.get("prep_trend") or "").upper()
    if trajectory == "DECLINING":
        return False, ""

    fc = intel.get("franking_class", {})
    ref_class = fc.get("reference_class", "Unknown")
    has_group = fc.get("group_listed_in_chain", False)

    reason = (
        f"INTEL OVERRIDE — rank-1, intel_bonus={bonus:.1f}, "
        f"franking={fk_score:.0f} ({ref_class}"
        f"{' via Group chain' if has_group else ''}), "
        f"trajectory={trajectory or 'N/A'}."
    )
    return True, reason


def evaluate_bet_candidate(pick):
    """
    Decide whether a runner is strong enough to be a real money bet.

    This is intentionally much stricter than "positive edge exists". It uses the
    validated sweet-spot idea from the staged backtests:
    - Short favourites must be dominant, not just shortest.
    - Primary win bets live mostly in the $3-$15 range.
    - >$15 runners stay coverage-only by default.

    An intelligence override can promote rank-1 picks with strong franking/prep
    signals to BET even when the edge calculation is below threshold.
    """
    if not pick:
        return False, "NO BET — no raw model leader was available."

    # Intelligence override — check before standard edge gates
    intel_override, intel_reason = _check_intelligence_override(pick)
    if intel_override:
        return True, intel_reason

    odds = _safe_num(pick.get("odds"), 0.0)
    has_real_market = bool(pick.get("has_real_market_odds")) and odds > 1
    edge = _safe_num(pick.get("edge_pct"), _safe_num(pick.get("edge"), 0.0))
    raw_prob = _safe_num(pick.get("raw_model_pct"), _safe_num(pick.get("modelProbability"), 0.0))
    calibrated_prob = _safe_num(pick.get("win_pct"), _safe_num(pick.get("winPercentage"), 0.0))
    confidence = str(pick.get("confidence") or "").strip().lower()
    prob = max(raw_prob, calibrated_prob)

    if not has_real_market:
        return False, "NO BET — the raw model leader has no real market quote."
    if edge <= 0:
        return False, "NO BET — the raw model leader does not clear a positive edge."

    if odds > 15:
        return False, f"NO BET — ${odds:.1f} sits outside the validated win-bet range, so this stays coverage only."

    if odds < 3:
        if edge < 4 or prob < 30:
            return False, "NO BET — short-priced runners need a dominant overlay and clear top-strike signal."
        return True, "Raw model leader is a strong favourite with a genuine overlay, so it is still bettable."

    if odds <= 5:
        if edge < 2.5 or prob < 15:
            return False, "NO BET — this short-to-mid price runner does not clear the stronger favourite guardrails."
        return True, "Raw model leader sits in the stronger runner band with enough edge to be a live bet."

    if edge < 3 or prob < 10:
        return False, "NO BET — the runner sits in the value band but lacks the edge/probability needed for a live bet."

    if confidence == "low" and odds > 12:
        return False, "NO BET — late-band value runner is still flagged low confidence, so it stays guide-only."

    return True, "Raw model leader sits in the validated value band and clears the edge threshold."


def _coverage_prob(pick):
    raw_prob = _safe_num(pick.get("raw_model_pct"), _safe_num(pick.get("modelProbability"), 0.0))
    calibrated_prob = _safe_num(pick.get("win_pct"), _safe_num(pick.get("winPercentage"), 0.0))
    return max(raw_prob, calibrated_prob)


def _coverage_sort_key(pick, model_leader_name):
    odds = _safe_num(pick.get("odds"), 999.0)
    edge = _safe_num(pick.get("edge_pct"), _safe_num(pick.get("edge"), 0.0))
    score = _safe_num(pick.get("selection_score"), 0.0)
    prob = _coverage_prob(pick)
    horse_name = str(pick.get("horse", "")).strip().lower()
    matches_model_leader = bool(model_leader_name) and horse_name == model_leader_name
    return (
        -prob,
        -edge,
        -score,
        odds,
        0 if matches_model_leader else 1,
        _safe_num(pick.get("rank"), 999.0),
    )


def _is_coverage_exception_candidate(pick):
    odds = _safe_num(pick.get("odds"), 0.0)
    if not (bool(pick.get("has_real_market_odds")) and odds > 20 and odds <= 30):
        return False

    edge = _safe_num(pick.get("edge_pct"), _safe_num(pick.get("edge"), 0.0))
    prob = _coverage_prob(pick)
    confidence = str(pick.get("confidence") or "").strip().lower()

    if odds <= 25:
        return prob >= 13 and edge >= 2 and confidence != "low"
    return prob >= 16 and edge >= 4 and confidence == "high"


def choose_coverage_race_pick(top_picks, model_leader_horse, fallback_candidates=None):
    """
    Choose one race guide horse from the filtered contenders.

    This can be a filtered substitute or a guide-only pick, but it must be
    labelled honestly so product surfaces never confuse it with the true bet.
    """
    if not top_picks and not fallback_candidates:
        return None

    fallback_candidates = fallback_candidates or []

    candidate_pool = []
    seen_horses = set()
    for source in [top_picks or [], fallback_candidates or []]:
        for pick in source:
            horse = str(pick.get("horse", "")).strip().lower()
            if not horse or horse in seen_horses:
                continue
            candidate_pool.append(pick)
            seen_horses.add(horse)

    if not candidate_pool:
        return None

    def _sorted_bucket(picks):
        return sorted(picks, key=lambda pick: _coverage_sort_key(pick, str(model_leader_horse or "").strip().lower()))

    bettable_positive = _sorted_bucket([
        pick for pick in candidate_pool
        if evaluate_bet_candidate(pick)[0]
    ])
    if bettable_positive:
        selected = bettable_positive[0]
    else:
        probability_first = _sorted_bucket([
            pick for pick in candidate_pool
            if bool(pick.get("has_real_market_odds"))
            and 1 < _safe_num(pick.get("odds"), 0.0) <= 20
            and _coverage_prob(pick) >= 8
        ])
        if probability_first:
            selected = probability_first[0]
        else:
            exception_candidates = _sorted_bucket([
                pick for pick in candidate_pool
                if _is_coverage_exception_candidate(pick)
            ])
            if exception_candidates:
                selected = exception_candidates[0]
            else:
                guide_positive = _sorted_bucket([
                    pick for pick in candidate_pool
                    if bool(pick.get("has_real_market_odds"))
                    and 1 < _safe_num(pick.get("odds"), 0.0) <= 20
                    and _safe_num(pick.get("edge_pct"), 0.0) > 0
                ])
                if guide_positive:
                    selected = guide_positive[0]
                else:
                    reasonable_market = _sorted_bucket([
                        pick for pick in candidate_pool
                        if bool(pick.get("has_real_market_odds")) and 1 < _safe_num(pick.get("odds"), 0.0) <= 20
                    ])
                    if reasonable_market:
                        selected = reasonable_market[0]
                    else:
                        real_positive = _sorted_bucket([
                            pick for pick in candidate_pool
                            if bool(pick.get("has_real_market_odds"))
                            and 1 < _safe_num(pick.get("odds"), 0.0) <= 25
                            and _safe_num(pick.get("edge_pct"), 0.0) > 0
                        ])
                        if real_positive:
                            selected = real_positive[0]
                        else:
                            real_market = _sorted_bucket([
                                pick for pick in candidate_pool
                                if bool(pick.get("has_real_market_odds")) and 1 < _safe_num(pick.get("odds"), 0.0) <= 25
                            ])
                            if real_market:
                                selected = real_market[0]
                            else:
                                any_market = _sorted_bucket([
                                    pick for pick in candidate_pool
                                    if bool(pick.get("has_real_market_odds")) and _safe_num(pick.get("odds"), 0.0) > 1
                                ])
                                if any_market:
                                    selected = any_market[0]
                                else:
                                    selected = candidate_pool[0]

    selected_horse = str(selected.get("horse", "")).strip().lower()
    model_leader_name = str(model_leader_horse or "").strip().lower()
    has_real_market = bool(selected.get("has_real_market_odds")) and _safe_num(selected.get("odds"), 0.0) > 1
    has_positive_edge = _safe_num(selected.get("edge_pct"), 0.0) > 0
    matches_model_leader = bool(model_leader_name) and selected_horse == model_leader_name
    clears_bet_guardrail, bet_guardrail_reason = evaluate_bet_candidate(selected)

    if not has_real_market:
        pick_role = "market_unavailable"
        pick_reason = "Guide horse has no real market quote; race should be treated as modelling context only."
        should_bet = False
    elif clears_bet_guardrail and matches_model_leader:
        pick_role = "model_backed"
        pick_reason = bet_guardrail_reason
        should_bet = True
    elif has_positive_edge and matches_model_leader:
        pick_role = "tip_only"
        pick_reason = bet_guardrail_reason
        should_bet = False
    elif has_positive_edge:
        pick_role = "filtered_substitute"
        pick_reason = "Guide horse is the safest filtered alternative, but it is not the raw model leader so this race remains a NO BET."
        should_bet = False
    else:
        pick_role = "tip_only"
        pick_reason = "No positive-edge model-backed bet exists in this race; this horse is a guide only."
        should_bet = False

    return annotate_pick_contract(
        selected,
        model_leader_horse=model_leader_horse,
        selection_origin=pick_role,
        selection_origin_reason=pick_reason,
        should_bet=should_bet,
    )


def choose_bet_race_pick(raw_model_leader):
    """
    Return the only horse money should go on in this race.

    No hidden substitutes: if the raw model leader is not a real positive-edge
    bet, the race is an explicit NO BET.
    """
    if not raw_model_leader:
        return None

    can_bet, bet_reason = evaluate_bet_candidate(raw_model_leader)
    if not can_bet:
        return None

    return annotate_pick_contract(
        raw_model_leader,
        model_leader_horse=raw_model_leader.get("horse"),
        selection_origin="model_backed",
        selection_origin_reason=bet_reason,
        should_bet=True,
    )


def derive_no_bet_reason(raw_model_leader, coverage_pick):
    if not raw_model_leader:
        return "No raw model leader was available, so the race defaults to guide-only."

    can_bet, bet_reason = evaluate_bet_candidate(raw_model_leader)
    if not can_bet:
        return bet_reason
    if coverage_pick and str(coverage_pick.get("horse", "")).strip().lower() != str(raw_model_leader.get("horse", "")).strip().lower():
        return "NO BET — a safer coverage horse exists, but it is not the raw model leader."
    return "NO BET — the race does not present a clean model-backed edge."


def run_tips(date_str, track_filter=None, output_path=None, store_in_db=True):
    """Run the full tips pipeline for a date."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  TIPS PIPELINE — {date_str}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    try:
        from betfair_enrich_racecard import enrich as _betfair_enrich
        _betfair_enrich(date_str)
    except Exception as e:
        print(f"[ODDS] Betfair enrichment unavailable ({e}) - "
              "scoring without live market prices", file=sys.stderr)

    racecard = load_racecard(date_str)
    if not racecard:
        return {"error": f"No racecard for {date_str}", "races": []}

    if not isinstance(racecard, list):
        racecard = [racecard]

    if track_filter:
        tf_lower = [t.lower() for t in track_filter]
        racecard = [
            m for m in racecard
            if any(
                tf in (m.get("course") or m.get("track") or "").lower()
                or (m.get("course") or m.get("track") or "").lower() in tf
                for tf in tf_lower
            )
        ]
        print(f"  Track filter: {', '.join(track_filter)}", file=sys.stderr)

    print(f"  Meets to process: {len(racecard)}", file=sys.stderr)
    for m in racecard:
        track_name = m.get("course") or m.get("track") or "Unknown"
        n_races = len(m.get("races", []))
        print(f"    - {track_name}: {n_races} races", file=sys.stderr)

    _load_env_vars()
    _configure_mc_runtime_flags()
    LLM_ENABLED = os.environ.get("LLM_ENABLED", "true").lower() in ("true", "1", "yes")
    llm_modules = None
    if LLM_ENABLED:
        llm_modules = _load_llm_modules()
        if llm_modules:
            try:
                provider = llm_modules["get_provider"]()
                print(f"  [LLM] Enabled — {type(provider).__name__} ({provider.model})", file=sys.stderr)
            except Exception as e:
                print(f"  [LLM] Provider init failed: {e} — continuing without LLM", file=sys.stderr)
                llm_modules = None
        else:
            print(f"  [LLM] Disabled — modules not available", file=sys.stderr)
    else:
        print(f"  [LLM] Disabled via LLM_ENABLED=false", file=sys.stderr)

    intelligence = load_intelligence_files()
    intel_count = sum(1 for k, v in intelligence.items() if not k.startswith("_") and v)
    print(f"  [INTEL] Loaded {intel_count}/8 intelligence files", file=sys.stderr)

    try:
        from consensus_blender import (
            load_consensus_intelligence,
            blend_scores as _blend_scores,
            determine_convergence_tier as _determine_tier,
            store_convergence_output as _store_convergence,
            calculate_consensus_injection as _calc_cons_inj,
            calculate_market_injection as _calc_mkt_inj,
            apply_convergence_gate as _apply_gate,
            calculate_market_confidence as _calc_mkt_confidence,
            confirm_with_model as _confirm_with_model,
            crowd_bet_decision as _crowd_bet_decision,
        )
        from intelligence.common import normalize_track as _normalize_track
        _consensus_intel, _market_intel, _pillars_available = load_consensus_intelligence(date_str)
        _convergence_enabled = bool(_consensus_intel or _market_intel)
    except Exception as _conv_err:
        print(f"  [BLENDER] Convergence layer not available: {_conv_err}", file=sys.stderr)
        _consensus_intel, _market_intel, _pillars_available = {}, {}, {"stride": True, "consensus": False, "market": False}
        _convergence_enabled = False
        _blend_scores = None
        _determine_tier = None
        _store_convergence = None
        _calc_cons_inj = None
        _calc_mkt_inj = None
        _apply_gate = None
        _calc_mkt_confidence = None
        _confirm_with_model = None
        _crowd_bet_decision = None
        _normalize_track = None

    all_race_tips = []
    all_convergence_rows = []
    total_races = 0
    total_mc_time = 0
    total_db_time = 0
    total_llm_time = 0
    book_scored_races = 0
    book_incoherent_races = 0
    book_corrupt_races = 0

    for meet in racecard:
        track = meet.get("course") or meet.get("track") or "Unknown"
        races_list = meet.get("races", [])

        for race in races_list:
          try:
            race_num = int(race.get("race_number", 0) or 0)
            race_name = race.get("race_name", f"Race {race_num}")
            distance = race.get("distance", "Unknown")
            distance_m = parse_distance_m(distance)
            going = race.get("going") or "Good"
            race_class = race.get("class") or race.get("race_class") or ""

            all_runners = race.get("runners", [])
            runners = filter_active_runners(all_runners)

            for runner in runners:
                stats = runner.get("stats") or {}
                runner["_comment"] = runner.get("comment", "")
                runner["_rating"] = runner.get("rating")
                runner["_last_won"] = stats.get("last_won")
                runner["_max_win_dist"] = stats.get("max_winning_distance")
                runner["_min_win_dist"] = stats.get("min_winning_distance")

            if len(runners) < 2:
                print(f"  {track} R{race_num}: Skipped ({len(runners)} runners)", file=sys.stderr)
                continue

            total_races += 1
            field_size = len(runners)
            print(f"\n  {track} R{race_num} — {race_name} {distance} ({going}) | {field_size} runners", file=sys.stderr)

            # ROI task 04: persist the odds actually knowable at prediction
            # time — one tip_time row per runner per bookmaker. Measurement
            # only: fire-and-forget, degrades to a no-op without a DB, and
            # must never delay or break tipping (STRIDE_ODDS_SNAPSHOT_WRITE=0
            # disables).
            try:
                from odds_snapshots import capture_tip_time_snapshots
                capture_tip_time_snapshots(
                    track=track, race_number=race_num, date_str=date_str,
                    runners=runners, race=race)
            except Exception:
                pass

            try:
                from race_normaliser import normalise_race as norm_race
                race["course"] = track
                normalised = norm_race(race, enable_llm=False)
                for flag in normalised["validation"]["flags"]:
                    if flag["severity"] in ("CRITICAL", "WARNING"):
                        print(f"    [NORM] {flag['severity']}: {flag['code']} — {flag['message']}", file=sys.stderr)
                if not normalised["validation"]["is_valid"]:
                    print(f"    Skipping — critical validation failure", file=sys.stderr)
                    continue
                pm = normalised.get("pace_map", {})
                print(f"    [NORM] Pace: {pm.get('predicted_tempo','?')} | Pressure: {pm.get('expected_pressure','?')} | Leaders: {pm.get('likely_leaders', [])}", file=sys.stderr)
            except Exception as e:
                print(f"    [NORM] Normaliser error (non-fatal): {e}", file=sys.stderr)

            luckless_data = {}
            try:
                from luckless_analyser import analyse_luckless
                hist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "historical_data", "track_imports")
                luckless_list = analyse_luckless(
                    runners, race, historical_dir=hist_dir,
                    enable_llm=LLM_ENABLED and llm_modules is not None,
                )
                for lr in luckless_list:
                    name = lr["horse"]
                    luckless_data[name] = lr
                    if lr.get("last_start", {}).get("is_luckless"):
                        uplift = lr["luckless_uplift"]
                        factors = lr["improvement"]["improvement_factors"]
                        print(f"    [LUCKLESS] {name}: forgive={lr['last_start']['forgive_score']}, "
                              f"uplift={uplift:+.3f}, factors={factors}", file=sys.stderr)
                for runner in runners:
                    rname = runner.get("horse", "")
                    lr = luckless_data.get(rname, {})
                    if lr.get("luckless_uplift", 0) > 0:
                        existing_adj = runner.get("llm_mu_adjustment", 0)
                        runner["llm_mu_adjustment"] = min(0.12, existing_adj + lr["luckless_uplift"])
            except Exception as e:
                print(f"    [LUCKLESS] Error (non-fatal): {e}", file=sys.stderr)

            llm_race_analysis = None
            if LLM_ENABLED and llm_modules:
                try:
                    llm_race_analysis = llm_modules["analyse_race_field"](
                        track=track, distance=distance, going=going,
                        race_class=race_class, race_number=race_num,
                        runners=runners,
                        luckless_data=luckless_data,
                    )
                    if llm_race_analysis:
                        runners = llm_modules["apply_pre_mc_adjustments"](runners, llm_race_analysis)
                        n_adj = len(llm_race_analysis.get("confidence_adjustments", {}))
                        print(f"    LLM pre-analysis: {n_adj} adjustments | pace: {llm_race_analysis.get('pace_scenario', 'n/a')[:60]}", file=sys.stderr)
                except Exception as e:
                    print(f"    LLM pre-analysis error: {e}", file=sys.stderr)

            try:
                from race_context import compute_race_context
                race_ctx = compute_race_context(runners, track, distance_m, race_class, going)
                for runner in runners:
                    runner.update(race_ctx)
                print(f"    [RACE_CTX] pace_pressure={race_ctx.get('pace_pressure_score', '?')}"
                      f" pace_clarity={race_ctx.get('pace_clarity_score', '?')}"
                      f" barrier_rel={race_ctx.get('barrier_relevance_score', '?')}"
                      f" efficiency={race_ctx.get('market_efficiency_flag', '?')}", file=sys.stderr)
            except Exception as e:
                race_ctx = {}
                print(f"    [RACE_CTX] Error (non-fatal): {e}", file=sys.stderr)

            try:
                from form_feature_builder import compute_race_form_features
                _form_conn = db_connect()
                try:
                    _form_cur = _form_conn.cursor()
                    _form_cur.execute("SET statement_timeout TO 5000")
                    _form_cur.close()
                except Exception:
                    pass
                runners = compute_race_form_features(
                    runners, track, date_str, distance_m, _form_conn,
                    going=going,
                )
                _form_conn.close()
                n_enriched = sum(1 for r in runners if r.get("days_since_run", 999) < 999)
                print(f"    [FORM] {n_enriched}/{len(runners)} runners enriched with form features", file=sys.stderr)
            except Exception as e:
                print(f"    [FORM] Error (non-fatal): {e}", file=sys.stderr)

            try:
                from ml_model import RacingMLModel
                _ml = RacingMLModel()
                if _ml.is_trained:
                    import pandas as _pd
                    # Task 03: single shared feature builder (serve_features.py) —
                    # one assembly used by BOTH inference paths. NaN-preserve
                    # contract (nan_contract.py) and interaction parity
                    # (STRIDE_INTERACTION_PARITY, default off) are applied there.
                    from serve_features import build_feature_row, log_movement_inert_once
                    from serve_features import (compute_race_pace,
                                                serve_live_features_enabled,
                                                serve_live_features_shadow_enabled)
                    log_movement_inert_once(date_str)
                    # Serve-feature liveness (FEATURE_PROVENANCE.md finding 1):
                    # 15 trained features carrying 25.45% of importance mass
                    # are plumbed only under STRIDE_SERVE_LIVE_FEATURES
                    # (default OFF). Shadow mode scores both ways, publishes
                    # LEGACY, logs deltas. Any error degrades to legacy.
                    _live_on = serve_live_features_enabled()
                    _shadow_on = serve_live_features_shadow_enabled() and not _live_on
                    _live_pace = None
                    _live_sect = {}
                    if _live_on or _shadow_on:
                        try:
                            _live_pace = compute_race_pace(runners)
                        except Exception as _pace_err:
                            print(f"    [FEATURES] pace liveness failed (legacy kept): {_pace_err}", file=sys.stderr)
                            _live_pace = None
                        try:
                            _live_sect = fetch_prior_sectionals(
                                [r.get("horse", "") for r in runners], date_str)
                        except Exception as _sect_err:
                            print(f"    [FEATURES] sectional liveness fetch failed (legacy kept): {_sect_err}", file=sys.stderr)
                            _live_sect = {}
                    _shadow_entries = []
                    # Phase 5: within-race relative market position (favourite ladder)
                    try:
                        from relative_market import compute_field_relative_market
                        _rel_mkt = compute_field_relative_market([extract_odds(r) for r in runners])
                    except Exception:
                        _rel_mkt = [{"fair_implied_prob": 0.0, "odds_rank": 0.0, "odds_rank_pct": 0.0}] * len(runners)
                    for _ri, runner in enumerate(runners):
                        _sect_row = _live_sect.get((runner.get("horse") or "").strip().lower())
                        feat = build_feature_row(
                            runner,
                            market_odds=extract_odds(runner),
                            distance_m=distance_m,
                            field_size=field_size,
                            rel_market=_rel_mkt[_ri],
                            pace=(_live_pace[_ri] if _live_pace else None),
                            sectionals=_sect_row,
                        )
                        df = _pd.DataFrame([feat])
                        X = _ml.prepare_features(df)
                        ml_prob = float(_ml.predict_proba(X, distance_m=distance_m)[0])
                        runner["mlPredictedProb"] = round(ml_prob * 100, 2)
                        if _shadow_on:
                            # Shadow: score the PLUMBED dict too, publish the
                            # legacy one above, log the delta.
                            try:
                                feat_live = build_feature_row(
                                    runner,
                                    market_odds=extract_odds(runner),
                                    distance_m=distance_m,
                                    field_size=field_size,
                                    rel_market=_rel_mkt[_ri],
                                    pace=(_live_pace[_ri] if _live_pace else None),
                                    sectionals=_sect_row,
                                    force_live=True,
                                )
                                X_live = _ml.prepare_features(_pd.DataFrame([feat_live]))
                                ml_prob_live = float(_ml.predict_proba(X_live, distance_m=distance_m)[0])
                                _shadow_entries.append({
                                    "horse": runner.get("horse", ""),
                                    "legacy_prob_pct": round(ml_prob * 100, 2),
                                    "live_prob_pct": round(ml_prob_live * 100, 2),
                                })
                            except Exception as _sh_err:
                                print(f"    [FEATURES] shadow score failed for {runner.get('horse','?')} (non-fatal): {_sh_err}", file=sys.stderr)
                    if _shadow_on and _shadow_entries:
                        _write_serve_liveness_shadow(date_str, track, race_num, _shadow_entries)
                        print(f"    [FEATURES] shadow liveness logged {len(_shadow_entries)} runners (legacy published)", file=sys.stderr)
                    n_ml = sum(1 for r in runners if r.get("mlPredictedProb", 0) > 0)
                    avg_ml = sum(r.get("mlPredictedProb", 0) for r in runners) / max(len(runners), 1)
                    print(f"    [ML] {n_ml}/{len(runners)} runners scored (avg {avg_ml:.1f}%)", file=sys.stderr)
            except Exception as e:
                print(f"    [ML] Error (non-fatal): {e}", file=sys.stderr)

            mc_input = {
                "track": track,
                "race": str(race_num),
                "iterations": get_iterations(field_size),
                "runners": runners,
                "distance": distance,
                "going": going,
                "mc_model": "plackett_luce",
                "pace_mode": "basic",
                "uncertainty": "on",
                "seed": _race_seed(date_str, track, race_num),
                "raceClass": race_class,
                "raceDate": date_str,
                "raceNumber": race_num,
            }

            t0 = time.time()
            mc_result = run_mc_simulation(mc_input)
            mc_elapsed = time.time() - t0
            total_mc_time += mc_elapsed

            if not mc_result:
                print(f"    MC failed, skipping", file=sys.stderr)
                continue

            horses = mc_result.get("results", mc_result.get("horses", []))
            if not horses:
                print(f"    No MC results", file=sys.stderr)
                continue

            print(f"    MC complete ({mc_elapsed:.1f}s) — {len(horses)} horses scored", file=sys.stderr)

            if intelligence:
                for h in horses:
                    enrich_horse_with_intelligence(h, track, distance_m, going, race_class, intelligence)
                intel_enriched = sum(1 for h in horses if h.get("_intelligence"))
                if intel_enriched:
                    print(f"    [INTEL] {intel_enriched}/{len(horses)} horses enriched with intelligence", file=sys.stderr)

            overround = calculate_overround(runners)
            # Judge the book BEFORE calibrate_and_score divides by it: an
            # impossible sum is laundered by the de-vig into plausible-looking
            # trueMarketProbs (100/1.16 = 86.2% over a 3.12 book reads as a
            # calm 27.6%), so this is the last site where the raw prices can
            # testify. The verdict is always computed (so enforce works
            # alone); output is behind STRIDE_BOOK_COHERENCE because the
            # durable record goes into the ARTIFACT — the handler truncates
            # stderr to its last 4000 chars, so 45 stderr verdicts would not
            # survive a cloud run.
            book_verdict = assess_book_coherence(runners)
            book_scored_races += 1
            if book_verdict["incoherent"]:
                book_incoherent_races += 1
            if book_verdict["corrupt"]:
                book_corrupt_races += 1
            if _flag_enabled("STRIDE_BOOK_COHERENCE") or _flag_enabled("STRIDE_BOOK_COHERENCE_ENFORCE"):
                print("    [BOOK] " + json.dumps({
                    "track": track, "race": race_num,
                    "book": book_verdict["book"],
                    "coverage": book_verdict["coverage"],
                    "top2": book_verdict["top2"],
                    "n_priced": book_verdict["n_priced"],
                    "n_active": book_verdict["n_active"],
                    "verdict": ("INCOHERENT" if book_verdict["incoherent"] else "OK"),
                    "corrupt": book_verdict["corrupt"],
                    "reasons": book_verdict["reasons"],
                }), file=sys.stderr)
            horses = calibrate_and_score(horses, overround, race_class=race_class)

            llm_tip_type = "win"
            llm_winner = ""
            llm_trifecta_horses = []
            llm_no_bet_reason = ""

            if LLM_ENABLED and llm_modules:
                try:
                    top6_sorted = sorted(horses, key=lambda x: x.get("selectionScore", 0), reverse=True)[:6]
                    scored_top6 = llm_modules["score_race_horses"](
                        horses=top6_sorted,
                        track=track, race_number=race_num,
                        race_name=race_name, distance=distance,
                        going=going, race_class=race_class,
                        all_horses=horses,
                        luckless_data=luckless_data,
                    )
                    ai_lookup = {(h.get("horse") or "").lower(): h for h in scored_top6}
                    for h in horses:
                        ai_h = ai_lookup.get((h.get("horse") or "").lower())
                        if ai_h:
                            for k in ("ai_score", "ai_analysis", "ai_key_edge", "ai_risk_factors", "ai_vs_field"):
                                if k in ai_h:
                                    h[k] = ai_h[k]
                    for h in horses:
                        ai_score = h.get("ai_score")
                        if ai_score is not None:
                            ai_norm = (ai_score / 100.0) * h.get("selectionScore", 0)
                            orig = h.get("selectionScore", 0)
                            h["selectionScore"] = round(0.70 * orig + 0.30 * ai_norm, 2)
                    scored = sum(1 for h in horses if h.get("ai_score") is not None)
                    print(f"    LLM post-scoring: {scored} horses scored", file=sys.stderr)

                    llm_ranking = None
                    llm_tip_type = "win"
                    llm_winner = ""
                    llm_trifecta_horses = []
                    llm_no_bet_reason = ""
                    for h in scored_top6:
                        if h.get("_llm_selection_ranking"):
                            llm_ranking = h["_llm_selection_ranking"]
                            llm_reasoning = h.get("_llm_ranking_reasoning", "")
                            llm_tip_type = h.get("_llm_tip_type", "win")
                            llm_winner = h.get("_llm_winner", "")
                            llm_trifecta_horses = h.get("_llm_trifecta_horses", [])
                            llm_no_bet_reason = h.get("_llm_no_bet_reason", "")
                            break

                    if llm_tip_type == "no_bet":
                        print(f"    [STRIDE_NO_BET] {llm_no_bet_reason or 'No positive edge found'}", file=sys.stderr)

                    if llm_tip_type == "trifecta" and llm_trifecta_horses:
                        print(f"    [STRIDE_TRIFECTA] {' / '.join(llm_trifecta_horses[:3])}", file=sys.stderr)

                    if llm_winner:
                        print(f"    [STRIDE_WINNER] {llm_winner}", file=sys.stderr)

                    if llm_ranking and isinstance(llm_ranking, list) and len(llm_ranking) >= 1:
                        rank_lookup = {name.strip().lower(): i for i, name in enumerate(llm_ranking)}
                        horses.sort(key=lambda h: (
                            rank_lookup.get((h.get("horse") or "").strip().lower(), 999),
                            -h.get("selectionScore", 0)
                        ))
                        print(f"    [STRIDE_RANKING] {' > '.join(llm_ranking[:3])}", file=sys.stderr)
                        if llm_reasoning:
                            print(f"    [STRIDE_REASON] {llm_reasoning[:120]}", file=sys.stderr)

                        _raw_probs = [h.get("rawModelProb", 0) for h in horses]
                        _mc_spread = (max(_raw_probs) - min(_raw_probs)) if len(_raw_probs) > 1 else 0
                        if _mc_spread < 6.0:
                            max_score = max(h.get("selectionScore", 0) for h in horses)
                            for h in horses:
                                llm_rank = rank_lookup.get((h.get("horse") or "").strip().lower(), 999)
                                if llm_rank <= 2:
                                    bonus = [5.0, 3.0, 1.0][llm_rank]
                                    h["selectionScore"] = round(max_score + bonus, 2)
                                    if llm_rank == 0:
                                        h["_llm_top_pick"] = True
                                        h_odds = h.get("marketOdds") or 0
                                        print(f"    [STRIDE_FLAT_BOOST] {h.get('horse','?')} (${h_odds}) score boosted to {h['selectionScore']} (STRIDE winner, MC flat)", file=sys.stderr)
                except Exception as e:
                    print(f"    LLM post-scoring error: {e}", file=sys.stderr)

            for h in horses:
                h["_distance_m"] = distance_m

            top3 = apply_safety_filters(horses, race_class=race_class)

            _pace_clarity = race_ctx.get("pace_clarity_score") if race_ctx else None
            for h in top3:
                h["confidence"] = compute_confidence(h, pace_clarity=_pace_clarity)
                h["staking"] = compute_staking(h)
            _log_ev_gate_transitions(top3, pace_clarity=_pace_clarity)
            # Enforcement zeroes the money, not a label: bet_status="NO_BET"
            # races still staked 1u per pick on 2026-08-08 because bet_status
            # never touches `staking`. Zeroing here propagates through
            # build_export_pick -> bet_pick -> the ledger.
            if book_verdict["incoherent"] and _flag_enabled("STRIDE_BOOK_COHERENCE_ENFORCE"):
                for h in top3:
                    if h.get("staking") not in (None, "", "0u"):
                        print("    [BOOK_REFUSED] " + json.dumps({
                            "horse": h.get("horse", "?"),
                            "track": track, "race": race_num,
                            "reasons": book_verdict["reasons"],
                            "staking_before": h.get("staking"),
                        }), file=sys.stderr)
                    h["staking"] = "0u"  # a refused row must never book P&L

            raw_probs = [h.get("rawModelProb", 0) for h in horses]
            mc_spread = (max(raw_probs) - min(raw_probs)) if len(raw_probs) > 1 else 0
            if mc_spread < 6.0:
                for h in top3:
                    h["confidence"] = "low"
                print(f"    [MC_FLAT_GATE] spread={mc_spread:.1f}pp — all tips forced to LOW confidence", file=sys.stderr)

            all_field_names = [h.get("horse", "") for h in horses if h.get("horse")]
            t_db = time.time()
            top3 = enrich_with_db(top3, track, date_str, all_field_names)
            total_db_time += time.time() - t_db

            t_llm = time.time()
            if LLM_ENABLED and llm_modules:
                for h in top3:
                    try:
                        insight = llm_modules["generate_rich_insight"](
                            horse=h, track=track, race_number=race_num,
                            distance=distance, going=going, race_class=race_class,
                            ai_analysis=h.get("ai_analysis", ""),
                            ai_vs_field=h.get("ai_vs_field", ""),
                            ai_key_edge=h.get("ai_key_edge", ""),
                            ai_risk_factors=h.get("ai_risk_factors", []),
                            luckless_data=luckless_data,
                            all_horses=horses,
                        )
                        h["ai_insight"] = insight
                        h["ai_insight_generated_at"] = datetime.utcnow().isoformat()
                    except Exception as e:
                        print(f"    LLM insight error for {h.get('horse', '?')}: {e}", file=sys.stderr)

            total_llm_time += time.time() - t_llm

            picks = []
            for rank, h in enumerate(top3, 1):
                picks.append(build_export_pick(h, rank=rank))

            for pick in picks:
                pname = pick["horse"]
                lr = luckless_data.get(pname, {})
                pick["luckless_flag"] = lr.get("last_start", {}).get("is_luckless", False)
                pick["luckless_score"] = lr.get("last_start", {}).get("forgive_score", 0)
                pick["luckless_uplift"] = lr.get("luckless_uplift", 0)
                pick["luckless_explanation"] = lr.get("luckless_explanation", "")
                pick["luckless_json"] = lr if lr.get("last_start", {}).get("found") else None

            if picks:
                bp = picks[0]
                _odds_label = f"${bp['odds']}" if bp.get("odds") else "N/A"
                print(f"    TOP: {bp['horse']} {_odds_label} — Win {bp['win_pct']}% | Edge {bp['edge_pct']:+.1f}% | {bp['confidence'].upper()}", file=sys.stderr)

            tipped_names = {h.get("horse", "").lower().strip() for h in top3}
            non_tipped = [h for h in horses if h.get("horse", "").lower().strip() not in tipped_names]
            if LLM_ENABLED and llm_modules and non_tipped:
                try:
                    _brief_fn = llm_modules.get("generate_brief_assessments")
                    if _brief_fn:
                        assessments = _brief_fn(non_tipped, top3, track, distance, going, race_class)
                        for h in horses:
                            hname = h.get("horse", "")
                            if hname in assessments:
                                h["_brief_assessment"] = assessments[hname]
                        print(f"    [LLM_BRIEF] {len(assessments)}/{len(non_tipped)} runners assessed", file=sys.stderr)
                except Exception as e:
                    print(f"    [LLM_BRIEF] Error (non-fatal): {e}", file=sys.stderr)

            if _convergence_enabled and _normalize_track:
                for _h in horses:
                    _h["_selection_score_raw"] = _h.get("selectionScore", 0.0)
                    _h["_consensus_injection"] = 0
                    _h["_market_injection"] = 0

            sorted_horses = sorted(horses, key=lambda x: x.get("selectionScore", 0), reverse=True)

            full_field = []
            from prediction_stages import finalise_stages
            for h in sorted_horses:
                is_tip = h.get("horse", "").lower().strip() in tipped_names
                entry = {
                    "horse": h.get("horse", "Unknown"),
                    "saddle_number": h.get("number") or h.get("tab_no", 0),
                    "barrier": h.get("barrier") or h.get("draw", 0),
                    "jockey": h.get("jockey", ""),
                    "trainer": h.get("trainer", ""),
                    "odds": h.get("marketOdds", 0),
                    "has_real_market_odds": h.get("hasRealMarketOdds", False),
                    "price_source": h.get("priceSource")
                                    or ("betfair" if h.get("hasRealMarketOdds") else "none"),
                    "inferred_market_odds": h.get("inferredMarketOdds"),
                    "fair_odds": h.get("fairOdds"),
                    "win_pct": round(h.get("winPercentage", 0), 1),
                    "place_pct": round(h.get("placePercentage", 0), 1),
                    "raw_model_pct": round(h.get("rawModelProb", 0), 1),
                    "edge_pct": round(h.get("modelEdge", 0), 1),
                    "selection_score": h.get("selectionScore", 0),
                    "prediction_stages": finalise_stages(h),
                    "form": h.get("form", ""),
                    "running_style": h.get("runningStyle", "unknown"),
                    "weighted_form_score": round(h.get("weighted_form_score", 0), 1),
                    "days_since_run": h.get("days_since_run", 999),
                    "is_first_up": bool(h.get("is_first_up", 0)),
                    "is_improving": bool(h.get("is_improving", 0)),
                    "distance_strike_rate": round(h.get("distance_strike_rate", 0), 3),
                    "course_strike_rate": round(h.get("course_strike_rate", 0), 3),
                    "is_tipped": is_tip,
                    "tip_rank": next((p["rank"] for p in picks if p["horse"].lower().strip() == h.get("horse", "").lower().strip()), None),
                    "confidence": h.get("confidence", ""),
                }
                if is_tip:
                    entry["ai_insight"] = h.get("ai_insight", "")
                    entry["ai_score"] = h.get("ai_score")
                else:
                    entry["brief_assessment"] = h.get("_brief_assessment", "")
                full_field.append(entry)

            raw_model_leader = build_export_pick(sorted_horses[0], rank=1) if sorted_horses else None
            if raw_model_leader:
                raw_leader_should_bet, _ = evaluate_bet_candidate(raw_model_leader)
                raw_model_leader = annotate_pick_contract(
                    raw_model_leader,
                    model_leader_horse=raw_model_leader.get("horse"),
                    selection_origin="raw_model_leader",
                    selection_origin_reason="Strongest pre-filter model signal in the race.",
                    should_bet=raw_leader_should_bet,
                )

            coverage_pick = choose_coverage_race_pick(
                picks,
                raw_model_leader.get("horse") if raw_model_leader else None,
                full_field,
            )
            bet_pick = choose_bet_race_pick(raw_model_leader)
            primary_pick = coverage_pick
            bet_status = "BET" if bet_pick else "NO_BET"
            bet_status_reason = (
                bet_pick.get("selection_origin_reason")
                if bet_pick
                else derive_no_bet_reason(raw_model_leader, coverage_pick)
            )

            _crowd_gate_evaluated = False

            model_leader_horse = raw_model_leader.get("horse") if raw_model_leader else None
            for pick in picks:
                if pick.get("selection_origin"):
                    continue
                pick_horse = str(pick.get("horse", "")).strip().lower()
                leader_name = str(model_leader_horse or "").strip().lower()
                has_real = bool(pick.get("has_real_market_odds")) and _safe_num(pick.get("odds"), 0.0) > 1
                has_edge = _safe_num(pick.get("edge_pct"), 0.0) > 0
                matches_leader = bool(leader_name) and pick_horse == leader_name
                clears_bet_guardrail, bet_guardrail_reason = evaluate_bet_candidate(pick)

                if clears_bet_guardrail and matches_leader:
                    origin = "model_backed"
                    reason = bet_guardrail_reason
                    should_bet = True
                elif has_real and has_edge and matches_leader:
                    origin = "tip_only"
                    reason = bet_guardrail_reason
                    should_bet = False
                elif has_real and has_edge:
                    origin = "filtered_substitute"
                    reason = "Filtered contender with edge, but not the raw model leader."
                    should_bet = False
                elif not has_real:
                    origin = "market_unavailable"
                    reason = "No real market quote available."
                    should_bet = False
                else:
                    origin = "tip_only"
                    reason = "Guide pick without a clear bettable edge."
                    should_bet = False

                pick["selection_origin"] = origin
                pick["selection_origin_reason"] = reason
                pick["should_bet"] = should_bet
                pick["matches_model_leader"] = matches_leader
                pick["model_leader_horse"] = model_leader_horse

            if _convergence_enabled and _confirm_with_model and _crowd_bet_decision and _normalize_track:
                _crowd_gate_evaluated = True
                _race_key = f"{_normalize_track(track)}_R{race_num}"

                _crowd_scores = {}
                _race_cons = _consensus_intel.get(_race_key, {})
                for _hname, _hdata in _race_cons.items():
                    if isinstance(_hdata, dict):
                        _crowd_scores[_hname] = _hdata.get("crowd_score", 0)

                _model_scores = {}
                for _h in horses:
                    _hname = _h.get("horse", "")
                    _model_scores[_hname] = _h.get("_selection_score_raw", _h.get("selectionScore", 0))

                _sorted_by_crowd = sorted(
                    _crowd_scores.items(), key=lambda x: x[1], reverse=True
                )
                _consensus_candidates = [
                    h for h, cs in _sorted_by_crowd
                    if cs >= 50  # at least half of sources checked must tip this horse
                ][:3]

                if _consensus_candidates:
                    print(
                        f"    [CROWD] Candidates: {', '.join(f'{h} ({_crowd_scores[h]:.0f})' for h in _consensus_candidates)}",
                        file=sys.stderr,
                    )

                _classifications = _confirm_with_model(_consensus_candidates, _model_scores)

                for pick in picks:
                    _horse = pick.get("horse", "")
                    _classification = _classifications.get(_horse)
                    _cs = _crowd_scores.get(_horse, 0)
                    _stride_raw = pick.get("_selection_score_raw", pick.get("selection_score", 0))

                    if _classification:
                        _should_bet, _gate_reason = _crowd_bet_decision(_classification, _cs)
                    else:
                        _should_bet = False
                        _classification = "REJECTED"
                        _gate_reason = "REJECTED — not a consensus candidate"

                    _new_bet, _gate_action = apply_crowd_gate(
                        bool(pick.get("should_bet")), _should_bet,
                        crowd_gate_only_enabled())
                    if _gate_action == "veto":
                        pick["should_bet"] = False
                        pick["selection_origin"] = "crowd_gated"
                        pick["selection_origin_reason"] = _gate_reason
                        print(f"    [GATE] {_horse}: BET → NO_BET ({_gate_reason})", file=sys.stderr)
                    elif _gate_action == "blocked":
                        # Task 07: promotion forbidden; shadow-track what the
                        # legacy path would have bet so its value is measured,
                        # not argued about.
                        pick["_crowd_promotion_blocked"] = True
                        pick["refusal_reason"] = "crowd_promotion_blocked"
                        pick["selection_origin_reason"] = _gate_reason
                        print(f"    [GATE-ONLY] {_horse}: promotion blocked, "
                              f"shadow-tracked ({_gate_reason})", file=sys.stderr)
                    elif _gate_action == "promote":
                        pick["should_bet"] = True
                        pick["selection_origin"] = "crowd_promoted"
                        pick["selection_origin_reason"] = _gate_reason
                        print(f"    [GATE] {_horse}: NO_BET → BET ({_gate_reason})", file=sys.stderr)

                    _cons = _race_cons.get(_horse, {})
                    pick["crowd_score"] = _cs
                    pick["crowd_classification"] = _classification
                    pick["crowd_gate_reason"] = _gate_reason
                    pick["selection_score_raw"] = _stride_raw
                    pick["consensus_injection"] = 0
                    pick["market_injection"] = 0
                    pick["consensus_score"] = _cons.get("consensus_score", 35.0) if isinstance(_cons, dict) else 35.0
                    pick["market_signal_score"] = 50.0
                    pick["consensus_mentions"] = _cons.get("total_mentions", 0) if isinstance(_cons, dict) else 0
                    pick["independent_mentions"] = _cons.get("independent_mentions", 0) if isinstance(_cons, dict) else 0
                    pick["commercial_mentions"] = _cons.get("commercial_mentions", 0) if isinstance(_cons, dict) else 0
                    pick["market_alignment"] = _cons.get("market_alignment", False) if isinstance(_cons, dict) else False

                    pick["stake_recommendation"] = (
                        "FULL" if _classification == "CONFIRMED" else
                        "STANDARD" if _classification == "CROWD_ONLY" and _cs > 70 else
                        "REDUCED" if _classification == "CROWD_ONLY" and _cs >= 50 else
                        "REDUCED" if _cs >= 100 else
                        "NONE"
                    )

                    pick["convergence_tier"] = _classification
                    pick["convergence_score"] = _cs
                    pick["convergence_gate"] = _gate_reason if not _should_bet else None

                _field_size = len(runners)
                for _runner in runners:
                    _rh = _runner.get("horse") or _runner.get("horseName") or ""
                    if not _rh:
                        continue
                    _r_cons = _race_cons.get(_rh, {})
                    _r_cs = _r_cons.get("crowd_score", 0) if isinstance(_r_cons, dict) else 0
                    _r_stride = _model_scores.get(_rh, 0)
                    _r_class = _classifications.get(_rh, "SKIP")
                    all_convergence_rows.append({
                        "track": _normalize_track(track), "race_number": race_num,
                        "horse_name": _rh,
                        "stride_score": _r_stride, "stride_score_raw": _r_stride,
                        "consensus_score": _r_cons.get("consensus_score", 35) if isinstance(_r_cons, dict) else 35,
                        "market_signal_score": 50,
                        "convergence_score": _r_cs,
                        "convergence_tier": _r_class,
                        "pillars_strong": (
                            2 if _r_class == "CONFIRMED" else
                            1 if _r_class in ("CROWD_ONLY", "CROWD_ONLY_WEAK") else
                            0
                        ),
                        "field_size": _field_size,
                        "consensus_injection": 0, "market_injection": 0,
                        "stride_score_final": _r_stride,
                        "market_confidence_score": None, "market_confidence_label": None,
                        "vote_pct": _r_cons.get("vote_pct", 0) if isinstance(_r_cons, dict) else 0,
                        "tipsters_polled": _r_cons.get("tipsters_polled", 0) if isinstance(_r_cons, dict) else 0,
                    })

                    if _r_class == "CONFIRMED":
                        print(f"    [CROWD] CONFIRMED: {_rh} — crowd ({_r_cs:.0f}) + model ({_r_stride:.0f})", file=sys.stderr)

            elif _convergence_enabled and not _confirm_with_model:
                _crowd_gate_evaluated = True
                for pick in picks:
                    pick["should_bet"] = False
                    pick["convergence_tier"] = "MODEL_ONLY"
                    pick["crowd_gate_reason"] = "crowd-first import failed — all picks gated"
                    pick["crowd_classification"] = "REJECTED"
                    print(f"    [GATE] {pick.get('horse', '?')}: BET → NO_BET (crowd-first import failed)", file=sys.stderr)

            if _convergence_enabled and _normalize_track:
                _race_key_ff = f"{_normalize_track(track)}_R{race_num}"
                _race_cons_ff = _consensus_intel.get(_race_key_ff, {})
                _pick_classifications = {}
                for _p in picks:
                    _p_horse = _p.get("horse", "")
                    if _p.get("crowd_classification"):
                        _pick_classifications[_p_horse] = _p["crowd_classification"]
                for _ff_entry in full_field:
                    _ff_horse = _ff_entry.get("horse", "")
                    _ff_cons = _race_cons_ff.get(_ff_horse, {})
                    if isinstance(_ff_cons, dict):
                        _ff_cs = _ff_cons.get("crowd_score", 0)
                        if _ff_cs > 0:
                            _ff_entry["crowd_score"] = _ff_cs
                            _ff_entry["crowd_classification"] = _pick_classifications.get(_ff_horse, "")
                            _ff_entry["independent_mentions"] = _ff_cons.get("independent_mentions", 0)
                            _ff_entry["commercial_mentions"] = _ff_cons.get("commercial_mentions", 0)
                            _ff_entry["total_mentions"] = _ff_cons.get("total_mentions", 0)

            from decision_contract import reconcile_crowd_bet
            bet_pick, refused_bet_pick, bet_status, bet_status_reason = reconcile_crowd_bet(
                bet_pick,
                picks,
                gate_evaluated=_crowd_gate_evaluated,
            )
            _final_candidate = bet_pick or refused_bet_pick
            if isinstance(_final_candidate, dict):
                from identity_normalization import normalize_runner_key
                _final_key = normalize_runner_key(_final_candidate.get("horse"))
                for _ff_entry in full_field:
                    if normalize_runner_key(_ff_entry.get("horse")) == _final_key:
                        _ff_entry["prediction_stages"] = _final_candidate.get("prediction_stages", {})

            _predictability = compute_race_predictability(runners, race_class, distance_m, going)

            all_race_tips.append({
                "track": track,
                "race_number": race_num,
                "race_name": race_name,
                "distance": distance,
                "going": going,
                "race_class": race_class,
                "field_size": field_size,
                "predictability": (
                    {
                        "score": _predictability.get("predictability_score"),
                        "category": _predictability.get("category"),
                        "confidence_modifier": _predictability.get("confidence_modifier"),
                        "key_factors": _predictability.get("key_factors", []),
                    }
                    if _predictability else None
                ),
                "top_picks": picks,
                "raw_model_leader": raw_model_leader,
                "bet_pick": bet_pick,
                "refused_bet_pick": refused_bet_pick,
                "coverage_pick": coverage_pick,
                "bet_status": bet_status,
                "bet_status_reason": bet_status_reason,
                "mc_seed": int(mc_result.get("seed", mc_input["seed"])),
                "primary_pick": primary_pick,
                "full_field": full_field,
            })
            # The durable record of the book verdict lives in the artifact:
            # the handler keeps only the last 4000 chars of stderr, so a
            # per-race stderr line does not survive a cloud run. Gated so the
            # artifact schema is unchanged until the flag is a decision.
            if _flag_enabled("STRIDE_BOOK_COHERENCE") or _flag_enabled("STRIDE_BOOK_COHERENCE_ENFORCE"):
                all_race_tips[-1]["book_coherence"] = {
                    "book": book_verdict["book"],
                    "coverage": book_verdict["coverage"],
                    "top2": book_verdict["top2"],
                    "n_priced": book_verdict["n_priced"],
                    "n_active": book_verdict["n_active"],
                    "incoherent": book_verdict["incoherent"],
                    "corrupt": book_verdict["corrupt"],
                    "reasons": book_verdict["reasons"],
                }
          except Exception as _race_err:
            _rn = int(race.get("race_number", 0) or 0)
            print(f"  [RACE_ERROR] {track} R{_rn} failed: {_race_err}", file=sys.stderr)
            all_race_tips.append({
                "track": track,
                "race_number": _rn,
                "race_name": race.get("race_name", f"Race {_rn}"),
                "error": str(_race_err),
                "top_picks": [],
                "full_field": [],
                "bet_status": "ERROR",
                "bet_status_reason": f"Pipeline error: {_race_err}",
            })
            continue

    failed_races = [r for r in all_race_tips if r.get("error")]
    if failed_races:
        print(f"\n  [WARN] {len(failed_races)} race(s) failed:", file=sys.stderr)
        for r in failed_races:
            print(f"    - {r['track']} R{r['race_number']}: {r['error']}", file=sys.stderr)

    if book_scored_races and (_flag_enabled("STRIDE_BOOK_COHERENCE")
                              or _flag_enabled("STRIDE_BOOK_COHERENCE_ENFORCE")):
        _corrupt_rate = book_corrupt_races / book_scored_races
        print(f"  [BOOK_SUMMARY] {book_incoherent_races}/{book_scored_races} "
              f"scored races incoherent "
              f"({book_corrupt_races} corrupt, "
              f"{book_incoherent_races - book_corrupt_races} coverage)",
              file=sys.stderr)
        # Corruption-vs-coverage split is load-bearing: replayed, 2026-08-02
        # is 8/8 coverage-only (no baseline yet, 0u staked) and 2026-08-05 is
        # 20 coverage + 1 corrupt — a rule counting both classes would abort
        # both cards. And this NEVER raises: a raise here fires before the
        # artifact write, so tips_<date>.json would not exist and the
        # backfill/sync never run — the exact 2026-08-06 no-artifact lesson.
        # A broken price source is announced, loudly, on a card that exists.
        if _corrupt_rate > 0.40:
            print(f"  [BOOK_ALERT] {book_corrupt_races}/{book_scored_races} "
                  f"races have CORRUPT books ({_corrupt_rate:.0%}) — the "
                  f"price source is broken; every stake on this card "
                  f"deserves suspicion", file=sys.stderr)

    # Final risk decisions happen before any aggregate, export contract, DB
    # write or ledger row is derived from the races.
    try:
        from staking_controls import apply_drawdown_breaker, enforce_exposure_caps
        apply_drawdown_breaker(all_race_tips)
        enforce_exposure_caps(all_race_tips)
    except Exception as _risk_err:
        print(f"  [RISK] staking controls unavailable (non-fatal): {_risk_err}",
              file=sys.stderr)

    all_picks = collect_day_picks(all_race_tips)

    day_summary = build_summary(
        all_race_tips, all_picks, total_races=total_races,
        mc_time=round(total_mc_time, 1),
        db_time=round(total_db_time, 1),
        llm_time=round(total_llm_time, 1),
    )
    high_count = day_summary["high_confidence"]
    pos_edge = day_summary["positive_edge"]
    total_units = day_summary["total_units"]

    output = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "races": all_race_tips,
        "best_bets": select_best_bets(all_picks),
        "value_plays": select_value_plays(all_picks),
        "bankers": select_bankers(all_picks),
        "summary": day_summary,
    }
    from release_manifest import release_context_from_env
    output["release"] = release_context_from_env()

    output["selection_contract"] = build_selection_contract(all_race_tips)

    if _convergence_enabled:
        _crowd_override_count = sum(1 for r in all_convergence_rows if r.get("convergence_tier") == "CROWD_ONLY_WEAK")
        _cs = build_convergence_summary(all_picks, _pillars_available, _crowd_override_count)
        output["convergence_summary"] = _cs
        print(
            f"  [CROWD-FIRST] V3: "
            f"{_cs['confirmed']} CONFIRMED | {_cs['crowd_only']} CROWD_ONLY | "
            f"{_cs['model_only']} MODEL_ONLY | {_cs['rejected']} REJECTED | "
            f"{_cs['gated_no_bet']} gated NO_BET | "
            f"Stakes: {_cs['stake_distribution']['full']}F/"
            f"{_cs['stake_distribution']['standard']}S/"
            f"{_cs['stake_distribution']['reduced']}R",
            file=sys.stderr,
        )

        if store_in_db and _store_convergence and all_convergence_rows:
            try:
                _store_convergence(date_str, all_convergence_rows, _pillars_available)
            except Exception as _conv_db_err:
                print(f"  [BLENDER] Convergence DB write failed: {_conv_db_err}", file=sys.stderr)

    # roi-roadmap 01: ledger capture is measurement-only and must never block
    # the pipeline; it no-ops unless STRIDE_LEDGER_WRITE=true.
    persist_selection_ledger(all_race_tips, date_str)

    if store_in_db:
        store_selections_in_db(all_race_tips, date_str)
        store_final_probs_in_audit(all_race_tips, date_str)
        # ROI task 04 (G1): tips is the invocation point every real path
        # shares — the odds clock starts here, or it does not start at all.
        launch_late_odds_watcher(date_str)
    else:
        print("  [DB] Skipping selection storage by request", file=sys.stderr)

    tips_path = Path(output_path) if output_path else build_tips_output_path(date_str)
    if track_filter and not output_path:
        canonical = build_tips_output_path(date_str)
        if canonical.exists():
            backup_name = f"{canonical.stem}.pre_merge_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{canonical.suffix}"
            shutil.copy2(canonical, canonical.parent / backup_name)
            print(f"  [MERGE] Backup: {backup_name}", file=sys.stderr)
        output = merge_tips_into_canonical(output, canonical, track_filter)

    try:
        tips_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=tips_path.parent, suffix=".tmp", prefix=tips_path.stem
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, default=str)
            Path(tmp_path).replace(tips_path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        print(f"\n  Tips saved to {tips_path}", file=sys.stderr)

        # Contract gate on the file just published: the same invariants
        # validate_tips.py asserts, run automatically so a broken document
        # is loud on the day it is produced. Non-fatal by design — a
        # validator problem must not kill a race-day run.
        try:
            _val = validate_tips_file(tips_path)
            if _val["errors"]:
                print(
                    f"  [VALIDATE] FAIL {tips_path.name}: "
                    f"{len(_val['errors'])} contract error(s)",
                    file=sys.stderr,
                )
                for _v_err in _val["errors"]:
                    print(f"    ERROR: {_v_err}", file=sys.stderr)
            else:
                _warn_note = (
                    f", {len(_val['warnings'])} warning(s)" if _val.get("warnings") else ""
                )
                print(
                    f"  [VALIDATE] PASS {tips_path.name}: {_val['total']} races "
                    f"({_val['bet']} BET, {_val['no_bet']} NO BET{_warn_note})",
                    file=sys.stderr,
                )
        except Exception as _val_err:
            print(f"  [VALIDATE] Could not validate tips file: {_val_err}", file=sys.stderr)
    except Exception as e:
        print(f"\n  [WARN] Could not save tips: {e}", file=sys.stderr)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  COMPLETE: {total_races} races | {high_count} high-conf | {pos_edge} positive-edge", file=sys.stderr)
    print(f"  MC: {total_mc_time:.1f}s | DB: {total_db_time:.1f}s | LLM: {total_llm_time:.1f}s | Outlay: {total_units}u", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the WizBet tips pipeline for a race date.")
    parser.add_argument("date", help="Race date in YYYY-MM-DD format")
    parser.add_argument("tracks", nargs="*", help="Optional track filters")
    parser.add_argument(
        "--output-suffix",
        help="Write to racecards/tips_<DATE>_<suffix>.json instead of the canonical file",
    )
    parser.add_argument(
        "--skip-db-store",
        action="store_true",
        help="Do not write selections into the database (useful for proof runs)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging verbosity (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,
    )

    output_path = build_tips_output_path(args.date, args.output_suffix)
    if args.tracks and not args.output_suffix:
        print(
            f"  [INFO] Track-filtered run will auto-merge into canonical output: {output_path}",
            file=sys.stderr,
        )

    result = run_tips(
        args.date,
        args.tracks or None,
        output_path=output_path,
        store_in_db=not args.skip_db_store,
    )
    print(json.dumps(result, indent=2, default=str))
