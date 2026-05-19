"""Canonical horse name normalization for the racing analytics pipeline."""

import re
from difflib import SequenceMatcher


def normalize_horse_name(name):
    """Normalize a horse name for comparison: lowercase, strip parentheticals, fix quotes."""
    if not name:
        return ""
    name = name.strip()
    name = re.sub(r'\s*\([^)]*\)\s*$', '', name)
    name = name.replace("\u2019", "'").replace("\u2018", "'")
    name = re.sub(r'\s+', ' ', name)
    return name.lower().strip()


def horse_name_match(name1, name2):
    """Fuzzy match two horse names (exact, apostrophe-stripped, or >85% sequence match)."""
    n1 = normalize_horse_name(name1)
    n2 = normalize_horse_name(name2)
    if n1 == n2:
        return True
    if n1.replace("'", "") == n2.replace("'", ""):
        return True
    return SequenceMatcher(None, n1, n2).ratio() > 0.85
