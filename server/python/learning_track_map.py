#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Dict, Literal, TypedDict


CollectorSource = Literal["nsw_pidata", "racing_qld", "racing_com", "results_only"]


class TrackResolution(TypedDict):
    canonical_track: str
    normalized_key: str
    source: CollectorSource
    known: bool


SPONSOR_PREFIXES = [
    r"Sportsbet[\s\-]+",
    r"Ladbrokes\s+Park\s+",
    r"Ladbrokes\s+",
    r"bet365\s+Park\s+",
    r"bet365\s+",
    r"Picklebet\s+Park\s+",
    r"Picklebet\s+",
    r"Southside\s+",
    r"TABtouch\s+Park\s+",
    r"TABtouch\s+",
    r"TAB\s+",
    r"Neds\s+",
    r"Betfair\s+",
]

SPONSOR_REGEX = re.compile(
    r"^(?:" + "|".join(SPONSOR_PREFIXES) + r")",
    re.IGNORECASE,
)


def _clean(raw: str | None) -> str:
    value = str(raw or "").strip()
    value = SPONSOR_REGEX.sub("", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_track_key(raw: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(raw).lower())


TRACK_SOURCE_MAP: Dict[str, CollectorSource] = {}
TRACK_DISPLAY_MAP: Dict[str, str] = {}


def _register(source: CollectorSource, canonical_track: str, *aliases: str) -> None:
    names = (canonical_track, *aliases)
    for name in names:
        key = normalize_track_key(name)
        if not key:
            continue
        TRACK_SOURCE_MAP[key] = source
        TRACK_DISPLAY_MAP[key] = canonical_track


_register("nsw_pidata", "Royal Randwick", "Randwick")
_register("nsw_pidata", "Kensington", "Randwick Kensington", "Randwick-Kensington")
_register("nsw_pidata", "Rosehill Gardens", "Rosehill")
_register("nsw_pidata", "Canterbury Park", "Canterbury")
_register("nsw_pidata", "Warwick Farm")
_register("nsw_pidata", "Newcastle")
_register("nsw_pidata", "Beaumont Newcastle")
_register("nsw_pidata", "Hawkesbury")
_register("nsw_pidata", "Kembla Grange")
_register("nsw_pidata", "Gosford")
_register("nsw_pidata", "Scone")
_register("nsw_pidata", "Wyong")
_register("nsw_pidata", "Muswellbrook")
_register("nsw_pidata", "Tamworth")
_register("nsw_pidata", "Nowra")
_register("nsw_pidata", "Port Macquarie")

_register("racing_qld", "Eagle Farm")
_register("racing_qld", "Doomben")
_register("racing_qld", "Gold Coast", "Aquis Park Gold Coast", "Aquis Park Gold Coast Poly")
_register("racing_qld", "Sunshine Coast")
_register("racing_qld", "Ipswich")
_register("racing_qld", "Toowoomba")
_register("racing_qld", "Rockhampton")
_register("racing_qld", "Townsville")
_register("racing_qld", "Cairns")
_register("racing_qld", "Mackay")
_register("racing_qld", "Gatton")
_register("racing_qld", "Dalby")
_register("racing_qld", "Kilcoy")
_register("racing_qld", "Beaudesert")
_register("racing_qld", "Cannon Park", "Ladbrokes Cannon Park")

_register("racing_com", "Caulfield", "Caulfield Heath")
_register("racing_com", "Flemington")
_register("racing_com", "Moonee Valley")
_register("racing_com", "Sandown", "Sandown Hillside", "Sandown Lakeside")
_register("racing_com", "Morphettville")
_register("racing_com", "Morphettville Parks")
_register("racing_com", "Pakenham", "Southside Pakenham")
_register("racing_com", "Cranbourne", "Southside Cranbourne")
_register("racing_com", "Werribee", "Picklebet Park Werribee")
_register("racing_com", "Geelong", "bet365 Geelong", "bet365 Stadium")
_register("racing_com", "Kyneton", "bet365 Park Kyneton")
_register("racing_com", "Ballarat", "Sportsbet-Ballarat", "Sportsbet Ballarat")
_register("racing_com", "Bendigo")
_register("racing_com", "Seymour")
_register("racing_com", "Moe")
_register("racing_com", "Sale")
_register("racing_com", "Warrnambool")
_register("racing_com", "Murray Bridge")
_register("racing_com", "Gawler")
_register("racing_com", "Port Augusta")
_register("racing_com", "Penola")
_register("racing_com", "Naracoorte")
_register("racing_com", "Hamilton")
_register("racing_com", "Echuca", "bet365 Echuca")

_register("results_only", "Ascot", "Ascot WA")
_register("results_only", "Belmont", "Belmont Park")


def normalize_track_name(raw: str | None) -> str:
    key = normalize_track_key(raw)
    if not key:
        return ""
    return TRACK_DISPLAY_MAP.get(key, _clean(raw))


def resolve_sectional_source(raw: str | None) -> CollectorSource:
    key = normalize_track_key(raw)
    if not key:
        return "results_only"
    return TRACK_SOURCE_MAP.get(key, "results_only")


def resolve_track(raw: str | None) -> TrackResolution:
    key = normalize_track_key(raw)
    canonical_track = TRACK_DISPLAY_MAP.get(key, _clean(raw))
    source = TRACK_SOURCE_MAP.get(key, "results_only")
    return {
        "canonical_track": canonical_track,
        "normalized_key": key,
        "source": source,
        "known": key in TRACK_SOURCE_MAP,
    }
