#!/usr/bin/env python3
"""
STRIDE Intelligence — shared utilities for Codex agents.
Provides DB connection, racecard loading, and JSON output helpers.
"""

import json
import os
import re
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("[intelligence_common] psycopg2 not available", file=sys.stderr)
    psycopg2 = None

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RACECARDS_DIR = PROJECT_ROOT / "racecards"
INTELLIGENCE_DIR = SCRIPT_DIR / "intelligence"


def _load_env():
    """Load .env file into os.environ (same pattern as run_tips_pipeline.py)."""
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


def get_connection():
    """Return a psycopg2 connection using DATABASE_URL."""
    _load_env()
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is not installed")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(url)


def parse_distance_m(distance_str):
    """Convert distance string like '1100m' to integer metres."""
    if not distance_str:
        return 0
    m = re.search(r"(\d+)", str(distance_str))
    return int(m.group(1)) if m else 0


def normalize_going(going_str):
    """Bucket going description into canonical categories."""
    if not going_str:
        return "good"
    g = going_str.lower().strip()
    if "heavy" in g:
        return "heavy"
    if "soft" in g:
        return "soft"
    if "firm" in g:
        return "firm"
    if "synthetic" in g or "poly" in g:
        return "synthetic"
    return "good"


def classify_distance(distance_m):
    """Classify distance into sprint/mile/staying."""
    if distance_m < 1200:
        return "sprint"
    elif distance_m <= 1600:
        return "mile"
    else:
        return "staying"


def best_odds(odds_list):
    """Extract the best (highest) win odds from a runner's odds array."""
    if not odds_list:
        return None
    best = 0.0
    for o in odds_list:
        try:
            val = float(o.get("win_odds", 0))
            if val > best:
                best = val
        except (ValueError, TypeError):
            continue
    return best if best > 0 else None


def load_todays_runners(race_date):
    """Parse racecards/racecard_{date}.json and return structured dict of tracks and runners."""
    racecard_path = RACECARDS_DIR / f"racecard_{race_date}.json"
    if not racecard_path.exists():
        raise FileNotFoundError(f"Racecard not found: {racecard_path}")

    with open(racecard_path, "r") as f:
        meets = json.load(f)

    all_tracks = set()
    all_horse_ids = set()
    all_horse_names = set()
    all_trainers = set()
    all_jockeys = set()
    races = []

    for meet in meets:
        track = meet.get("course", "")
        for race in meet.get("races", []):
            distance_str = race.get("distance", "")
            distance_m = parse_distance_m(distance_str)
            going = race.get("going", "Good")
            race_class = race.get("class", "")
            race_number = int(race.get("race_number", 0))
            race_name = race.get("race_name", "")

            runners = []
            for r in race.get("runners", []):
                if r.get("scratched"):
                    continue
                horse_id = r.get("horse_id", "")
                horse_name = r.get("horse", "")
                barrier = int(r.get("draw", 0) or 0)
                number = int(r.get("number", 0) or 0)
                jockey = r.get("jockey", "")
                trainer = r.get("trainer", "")
                form = r.get("form", "")
                odds = best_odds(r.get("odds", []))
                age_raw = r.get("age", "")
                try:
                    age_years = int(str(age_raw).replace("yo", "").strip())
                except (ValueError, TypeError):
                    age_years = None

                runners.append({
                    "horse_id": horse_id,
                    "horse_name": horse_name,
                    "barrier": barrier,
                    "number": number,
                    "jockey": jockey,
                    "trainer": trainer,
                    "form": form,
                    "best_odds": odds,
                    "running_style": None,
                    "age_years": age_years,
                })

                if horse_id:
                    all_horse_ids.add(horse_id)
                if horse_name:
                    all_horse_names.add(horse_name)
                if trainer:
                    all_trainers.add(trainer)
                if jockey:
                    all_jockeys.add(jockey)

            if runners:
                all_tracks.add(track)
                races.append({
                    "track": track,
                    "race_number": race_number,
                    "race_name": race_name,
                    "distance_m": distance_m,
                    "distance_str": distance_str,
                    "going": going,
                    "going_norm": normalize_going(going),
                    "race_class": race_class,
                    "field_size": len(runners),
                    "runners": runners,
                })

    return {
        "date": race_date,
        "tracks": sorted(all_tracks),
        "races": races,
        "horse_ids": sorted(all_horse_ids),
        "horse_names": sorted(all_horse_names),
        "trainers": sorted(all_trainers),
        "jockeys": sorted(all_jockeys),
    }


def json_write(data, filepath):
    """Write data as JSON with indent=2."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)
    size_kb = filepath.stat().st_size / 1024
    print(f"  [OK] {filepath.name} ({size_kb:.1f} KB)", file=sys.stderr)
