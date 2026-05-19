from __future__ import annotations

import math
import re
from typing import Iterable, List, Optional


METRO_TRACK_ORDER: List[str] = [
    "Randwick",
    "Kensington",
    "Rosehill",
    "Flemington",
    "Caulfield",
    "Moonee Valley",
    "Eagle Farm",
    "Doomben",
    "Morphettville",
    "Ascot WA",
    "Sha Tin",
    "Happy Valley",
]

TRACK_ALIAS_MAP = {
    "randwick": "Randwick",
    "royal randwick": "Randwick",
    "randwick-kensington": "Kensington",
    "kensington": "Kensington",
    "rosehill": "Rosehill",
    "rosehill gardens": "Rosehill",
    "flemington": "Flemington",
    "caulfield": "Caulfield",
    "moonee valley": "Moonee Valley",
    "valley": "Moonee Valley",
    "eagle farm": "Eagle Farm",
    "doomben": "Doomben",
    "morphettville": "Morphettville",
    "ascot": "Ascot WA",
    "ascot wa": "Ascot WA",
    "sha tin": "Sha Tin",
    "happy valley": "Happy Valley",
}

TIGHT_TURNING_TRACKS = {"Moonee Valley", "Morphettville", "Doomben"}
SWEEPING_TRACKS = {"Randwick", "Rosehill", "Flemington", "Eagle Farm"}

PRICE_BRACKETS = [
    (1.01, 1.50, "1.01-1.50"),
    (1.51, 2.00, "1.51-2.00"),
    (2.01, 3.00, "2.01-3.00"),
    (3.01, 4.50, "3.01-4.50"),
    (4.51, 6.00, "4.51-6.00"),
    (6.01, 9.00, "6.01-9.00"),
    (9.01, 14.00, "9.01-14.00"),
    (14.01, 20.00, "14.01-20.00"),
    (20.01, 40.00, "20.01-40.00"),
    (40.01, 9999.0, "40.01+"),
]

DSLR_BUCKETS = [
    (1, 6, "1-6"),
    (7, 13, "7-13"),
    (14, 20, "14-20"),
    (21, 27, "21-27"),
    (28, 35, "28-35"),
    (36, 55, "36-55"),
    (56, 90, "56-90"),
    (91, 180, "91-180"),
    (181, 10000, "180+"),
]

FIELD_SIZE_BUCKETS = [
    (0, 8, "<=8"),
    (9, 12, "9-12"),
    (13, 16, "13-16"),
    (17, 99, "17+"),
]


def canonical_track(track: Optional[str]) -> str:
    if not track:
        return ""
    key = re.sub(r"\s+", " ", str(track).strip().lower())
    return TRACK_ALIAS_MAP.get(key, str(track).strip())


def resolve_tracks(scope: str, track_override: Optional[Iterable[str]] = None) -> List[str]:
    if track_override:
      resolved = [canonical_track(track) for track in track_override if str(track).strip()]
      seen = set()
      return [track for track in resolved if not (track in seen or seen.add(track))]
    if scope == "metro":
        return METRO_TRACK_ORDER[:]
    return []


def normalize_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def normalize_going(value: Optional[str]) -> str:
    if not value:
        return "Unknown"
    text = str(value).strip().lower()
    if not text:
        return "Unknown"
    if "heavy" in text:
        return "Heavy"
    if "soft" in text:
        return "Soft"
    if "good" in text:
        return "Good"
    if "firm" in text:
        return "Firm"
    if "synthetic" in text or "poly" in text:
        return "Synthetic"
    return str(value).strip()


def normalize_going_group(value: Optional[str]) -> str:
    normalized = normalize_going(value)
    raw = str(value or "").lower()
    if normalized == "Heavy":
        return "Heavy 8-10" if re.search(r"heavy\s*(8|9|10)", raw) else "Heavy"
    if normalized == "Soft":
        return "Soft 6-7" if re.search(r"soft\s*(6|7)", raw) else "Soft"
    if normalized == "Good":
        return "Good"
    return normalized


def distance_bucket(distance_m: Optional[int]) -> str:
    if not distance_m:
        return "Unknown"
    if distance_m <= 1200:
        return "Sprint <=1200"
    if distance_m <= 1800:
        return "Middle 1400-1800"
    return "Staying 2000+"


def class_bucket(race_class: Optional[str], class_level: Optional[int]) -> str:
    rc = str(race_class or "").strip().lower()
    if "group 1" in rc or "g1" in rc:
        return "Group 1"
    if "group 2" in rc or "g2" in rc:
        return "Group 2"
    if "group 3" in rc or "g3" in rc:
        return "Group 3"
    if "listed" in rc:
        return "Listed"
    if "maiden" in rc or "mdn" in rc:
        return "Maiden"
    if "benchmark" in rc or "bm" in rc or (class_level is not None and class_level < 100):
        return "Handicap/BM"
    return race_class or "Unknown"


def price_bracket(odds: Optional[float]) -> str:
    if odds is None or odds <= 1:
        return "Unknown"
    for low, high, label in PRICE_BRACKETS:
        if odds >= low and odds <= high:
            return label
    return "Unknown"


def dslr_bucket(days: Optional[float]) -> str:
    if days is None or (isinstance(days, float) and math.isnan(days)):
        return "Unknown"
    try:
        value = int(days)
    except (TypeError, ValueError):
        return "Unknown"
    for low, high, label in DSLR_BUCKETS:
        if value >= low and value <= high:
            return label
    return "Unknown"


def field_size_bucket(size: Optional[int]) -> str:
    if not size:
        return "Unknown"
    for low, high, label in FIELD_SIZE_BUCKETS:
        if size >= low and size <= high:
            return label
    return "Unknown"


def quarter_label(index: int) -> str:
    return {0: "early", 1: "mid", 2: "late"}.get(index, f"window_{index + 1}")
