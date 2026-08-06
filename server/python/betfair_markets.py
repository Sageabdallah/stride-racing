#!/usr/bin/env python3
"""Betfair Exchange market discovery + STRIDE mapping for odds snapshots.

Endpoint contracts (Betfair Exchange API-NG, JSON-RPC over POST):
    betting:  https://api.betfair.com/exchange/betting/json-rpc/v1
    account:  https://api.betfair.com/exchange/account/json-rpc/v1
    body:     {"jsonrpc": "2.0", "method": "SportsAPING/v1.0/<op>",
               "params": {...}, "id": 1}
    headers:  X-Application: <app key>, X-Authentication: <session token>,
              Content-Type: application/json
    reply:    {"jsonrpc": "2.0", "result": ..., "id": 1} on success;
              {"error": {..., "data": {"APINGException": {...}}}} on failure.
    An HTML body instead of JSON is Betfair's edge/geo block page — the
    request never reached the API (scripts/BETFAIR_KEYS_STATUS.md).

Operations used:
    SportsAPING/v1.0/listMarketCatalogue — AU thoroughbred WIN markets for a
        date window; marketProjection EVENT, MARKET_START_TIME,
        RUNNER_DESCRIPTION gives venue, jump time and runner names.
    SportsAPING/v1.0/listMarketBook — EX_BEST_OFFERS prices (virtualised) for
        mapped markets, max 40 markets per call (5 weight points each against
        the documented 200-point request cap).
    AccountAPING/v1.0/getDeveloperAppKeys — the key's delayData flag decides
        the odds_source provenance value; no code edit needed when the live
        key is activated.

Mapping rules:
    market -> race: Betfair venue vs race_schedule.track through resolve_venue
        (exact/alias via a shared normaliser kept in lockstep with
        stride_norm_track() in build_betfair_mapping.py, then single-candidate
        token containment against the day's schedule — ambiguity is refused,
        never guessed) + race number from the market name, with a jump-time
        fallback. Read-only SELECTs; unmapped markets are reported,
        never guessed.
    runner -> horse_id: runnerName (trap-number prefix stripped, Betfair
        style "1. Fast Horse") through pf_backfill_results.norm_name against
        the race_results_history bridge — the same rule that keeps PF results
        from forking horse identities. Unknown runners are reported, not
        invented.

All transports are injectable (post=...) so tests run with zero network.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pf_backfill_results import norm_name
from providers.betfair_auth import BetfairEdgeBlocked, looks_like_edge_block

BETTING_RPC_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"
ACCOUNT_RPC_URL = "https://api.betfair.com/exchange/account/json-rpc/v1"

# Race days are an AEST concept; Betfair marketStartTime is UTC.
_SYDNEY = ZoneInfo("Australia/Sydney")

# listMarketBook: EX_BEST_OFFERS costs 5 weight points per market against a
# 200-point request cap, so 40 markets per call.
BOOK_CHUNK_SIZE = 40

# Jump-time fallback tolerance when the market name carries no race number.
START_TIME_TOLERANCE = timedelta(minutes=30)

# Keep in lockstep with stride_norm_track() (build_betfair_mapping.py) so
# Python-side matching agrees with what the DB function would say.
_TRACK_ALIASES = {
    "royalrandwick": "randwick",
    "randwickkensington": "kensington",
    "rosehillgardens": "rosehill",
    "ascot": "ascotwa",
    "ascotwa": "ascotwa",
    # Betfair's venue is plain "Sandown" for both circuits; race_schedule
    # carries the circuit name. Same physical venue, one meeting per day,
    # so collapsing both onto "sandown" cannot collide.
    "sandownlakeside": "sandown",
    "sandownhillside": "sandown",
}


class BetfairAPIError(RuntimeError):
    """JSON-RPC level failure (APINGException, bad envelope, non-JSON)."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _default_post(url: str, payload: dict, headers: dict, timeout: int = 30) -> Tuple[int, str]:
    import requests
    resp = requests.post(url, data=json.dumps(payload).encode("utf-8"),
                         headers=headers, timeout=timeout)
    return resp.status_code, resp.text


def rpc(method: str, params: dict, app_key: str, token: str,
        post: Optional[Callable] = None, endpoint: str = BETTING_RPC_URL):
    """One JSON-RPC call. Returns the result member; loud on everything else."""
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers = {"X-Application": app_key, "X-Authentication": token,
               "Content-Type": "application/json", "Accept": "application/json"}
    status, text = (post or _default_post)(endpoint, payload, headers)
    if looks_like_edge_block(text):
        raise BetfairEdgeBlocked(method)
    try:
        body = json.loads(text)
    except ValueError:
        raise BetfairAPIError(f"{method}: non-JSON response (HTTP {status}): {text[:200]}")
    if not isinstance(body, dict) or "error" in body:
        err = body.get("error", body) if isinstance(body, dict) else body
        code = ""
        if isinstance(err, dict):
            code = (((err.get("data") or {}).get("APINGException") or {}).get("errorCode")
                    or err.get("message") or "")
        raise BetfairAPIError(f"{method}: {code or err}")
    if "result" not in body:
        raise BetfairAPIError(f"{method}: reply has no result member")
    return body["result"]


# ---------------------------------------------------------------------------
# Exchange operations
# ---------------------------------------------------------------------------

def market_time_window(date_iso: str) -> Tuple[str, str]:
    """UTC bounds of the AEST race day, formatted for marketStartTime."""
    day = datetime.strptime(date_iso, "%Y-%m-%d")
    start = day.replace(tzinfo=_SYDNEY).astimezone(timezone.utc)
    end = (day + timedelta(days=1)).replace(tzinfo=_SYDNEY).astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def list_win_market_catalogue(date_iso: str, app_key: str, token: str,
                              post: Optional[Callable] = None) -> List[dict]:
    """AU thoroughbred WIN markets jumping on the given AEST date."""
    time_from, time_to = market_time_window(date_iso)
    params = {
        "filter": {
            "eventTypeIds": ["7"],
            "marketCountries": ["AU"],
            "marketTypeCodes": ["WIN"],
            "marketStartTime": {"from": time_from, "to": time_to},
        },
        "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
        "maxResults": 1000,
    }
    result = rpc("SportsAPING/v1.0/listMarketCatalogue", params, app_key, token, post=post)
    if not isinstance(result, list):
        raise BetfairAPIError(f"listMarketCatalogue: expected a list, got {type(result).__name__}")
    return result


def list_market_books(market_ids: Sequence[str], app_key: str, token: str,
                      post: Optional[Callable] = None) -> List[dict]:
    """EX_BEST_OFFERS books for the given markets, chunked under the
    request-weight cap. virtualise=True gives the cross-matched (displayed)
    prices rather than raw unmatched offers."""
    books: List[dict] = []
    ids = list(market_ids)
    for i in range(0, len(ids), BOOK_CHUNK_SIZE):
        params = {
            "marketIds": ids[i:i + BOOK_CHUNK_SIZE],
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True},
        }
        result = rpc("SportsAPING/v1.0/listMarketBook", params, app_key, token, post=post)
        if not isinstance(result, list):
            raise BetfairAPIError(f"listMarketBook: expected a list, got {type(result).__name__}")
        books.extend(result)
    return books


def resolve_odds_source(app_key: str, token: str,
                        post: Optional[Callable] = None) -> str:
    """Provenance value for snapshot rows, from the key's own delayData flag.

    'betfair_delayed' unless getDeveloperAppKeys says this key serves live
    data — so activating the live key flips the value without a code edit.
    Unreachable/failed lookups default to delayed: never claim live prices
    on anything less than Betfair's word."""
    try:
        result = rpc("AccountAPING/v1.0/getDeveloperAppKeys", {}, app_key, token,
                     post=post, endpoint=ACCOUNT_RPC_URL)
        for app in result or []:
            for version in app.get("appVersions", []):
                if version.get("applicationKey") == app_key:
                    return "betfair_live" if version.get("delayData") is False else "betfair_delayed"
    except (BetfairAPIError, BetfairEdgeBlocked) as e:
        print(f"  [betfair] getDeveloperAppKeys unavailable ({e}) — assuming delayed key",
              file=sys.stderr)
    return "betfair_delayed"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def plain_norm(name: Any) -> str:
    """Lowercase-alnum, matching roi/04's normalize_track_name / the DB's
    stride_norm_name — used for row fields so joins line up."""
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def norm_track(name: Any) -> str:
    """Matching key for Betfair venue <-> race_schedule.track: strip
    parenthetical qualifiers like '(AUS)', normalise, then alias."""
    base = re.sub(r"\([^)]*\)", "", str(name or "")).split(" - ")[0]
    key = plain_norm(base)
    return _TRACK_ALIASES.get(key, key)


def _venue_tokens(name: Any) -> frozenset:
    """Tokens for containment matching — must tokenise BEFORE plain_norm,
    which strips all non-alphanumerics including spaces and would fuse
    'Ballarat Synthetic' into the single token 'ballaratsynthetic'."""
    base = re.sub(r"\([^)]*\)", " ", str(name or "")).split(" - ")[0]
    return frozenset(t for t in re.split(r"[^a-z0-9]+", base.lower()) if t)


def resolve_venue(venue: Any,
                  schedule_tokens: Dict[str, frozenset]) -> Tuple[Optional[str], str]:
    """Betfair venue -> race_schedule venue key, or (None, reason).

    Exact/alias match via norm_track, then token containment against ONLY the
    venues racing today. AU Betfair venues carry naming-rights prefixes
    ('Southside Cranbourne') and our cards carry surface suffixes ('Ballarat
    Synthetic'); neither side is canonical, so containment either way counts.

    Ambiguity is reported, never resolved by preference: the containment check
    runs BEFORE the exact hit is honoured, so 'Randwick Kensington' refuses
    when both Randwick and Kensington are carded rather than riding the alias
    table onto one of them.
    """
    key = norm_track(venue)
    vt = _venue_tokens(venue)
    if not vt:
        return None, "empty venue"
    hits = [k for k, kt in schedule_tokens.items() if vt <= kt or kt <= vt]
    if len(hits) > 1:
        return None, f"ambiguous: matches {', '.join(sorted(hits))}"
    if key in schedule_tokens:
        return key, "exact"
    if hits:
        return hits[0], "token-containment"
    return None, "no race_schedule match for venue"


def clean_runner_name(name: Any) -> str:
    # Betfair runner names carry the saddlecloth number: "1. Fast Horse"
    # (convention: clean_horse_name in build_betfair_mapping.py).
    return re.sub(r"^\d+\.\s*", "", str(name or "").strip()).strip()


def extract_race_number(*candidates: Any) -> Optional[int]:
    """Race number from Betfair market/event names ('R1 1200m Hcap'),
    following build_betfair_mapping.py conventions."""
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if re.fullmatch(r"\d{1,2}", text):
            return int(text)
        match = re.search(r"\bR(?:ace)?\s*0?(\d{1,2})\b", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def parse_iso_utc(value: Any) -> Optional[datetime]:
    """ISO timestamp -> aware UTC datetime; naive values are assumed AEST
    (race_schedule.off_time is written in local race time)."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_SYDNEY)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# DB reads (read-only SELECTs; cursors are passed in so tests can fake them)
# ---------------------------------------------------------------------------

SCHEDULE_SQL = """
    SELECT track, race_number, race_date::text, off_time
    FROM race_schedule
    WHERE race_date = %s
"""

# Most recent prior row wins — the same bridge rule pf_backfill_results.py
# uses so Betfair runners can never fork a horse's identity. The SELECT keeps
# the ORIGINAL-case horse_name so norm_name can strip an uppercase country
# suffix like "(NZ)"; lowercasing it here first would leave "(nz)" in the key
# and country-suffixed runners (every NZ import) would never bridge.
BRIDGE_SQL = """
    SELECT DISTINCT ON (LOWER(horse_name)) horse_name, horse_id
    FROM race_results_history
    ORDER BY LOWER(horse_name), race_date DESC
"""


def load_schedule(cur, date_iso: str) -> List[dict]:
    cur.execute(SCHEDULE_SQL, (date_iso,))
    return [{"track": r[0], "race_number": r[1], "race_date": r[2], "off_time": r[3]}
            for r in cur.fetchall()]


def load_horse_bridge(cur) -> Dict[str, str]:
    cur.execute(BRIDGE_SQL)
    return {norm_name(name): hid for name, hid in cur.fetchall()}


# ---------------------------------------------------------------------------
# Mapping (pure, DB-free)
# ---------------------------------------------------------------------------

def make_race_id(race_date: Any, track: Any, race_number: int) -> str:
    # Same shape as roi/04's make_race_id (odds_snapshots.py) minus the
    # meet_id prefix Betfair cannot know.
    return f"{race_date}|{plain_norm(track)}|R{int(race_number)}"


def map_markets(catalogue: Sequence[dict], schedule_rows: Sequence[dict],
                bridge: Dict[str, str]) -> Tuple[List[dict], List[dict], List[dict]]:
    """(mapped_markets, unmapped_markets, unmapped_runners).

    A market maps when its venue+race number hit a race_schedule row (jump
    time within tolerance is the fallback when the name has no number). The
    venue is resolved by resolve_venue: exact/alias first, then
    single-candidate token containment against the day's venues only. A
    runner maps when its normalised name is in the horse-id bridge. Anything
    unmapped is reported for the run summary — never guessed, never fatal to
    the rest of the capture."""
    by_key: Dict[Tuple[str, int], dict] = {}
    by_venue: Dict[str, List[dict]] = {}
    schedule_tokens: Dict[str, frozenset] = {}
    for row in schedule_rows:
        vkey = norm_track(row["track"])
        try:
            rn = int(row["race_number"])
        except (TypeError, ValueError):
            continue
        by_key.setdefault((vkey, rn), row)
        by_venue.setdefault(vkey, []).append(row)
        schedule_tokens.setdefault(vkey, _venue_tokens(row["track"]))

    mapped: List[dict] = []
    unmapped_markets: List[dict] = []
    unmapped_runners: List[dict] = []
    claimed_races: Dict[str, str] = {}

    for market in catalogue:
        market_id = str(market.get("marketId") or "")
        event = market.get("event") or {}
        venue = event.get("venue") or event.get("name") or ""
        vkey, reason = resolve_venue(venue, schedule_tokens)
        market_name = market.get("marketName") or ""
        start_time = parse_iso_utc(market.get("marketStartTime"))
        rn = extract_race_number(market_name, event.get("name"))

        race = None
        if vkey is not None:
            race = by_key.get((vkey, rn)) if rn is not None else None
            if race is None and rn is None and start_time is not None:
                # Only when the name carried no race number at all: nearest
                # scheduled jump at the same venue, inside tolerance. A number
                # that missed the schedule is reported, never re-guessed by time.
                best = None
                for candidate in by_venue.get(vkey, []):
                    off = parse_iso_utc(candidate.get("off_time"))
                    if off is None:
                        continue
                    delta = abs(off - start_time)
                    if delta <= START_TIME_TOLERANCE and (best is None or delta < best[0]):
                        best = (delta, candidate)
                if best:
                    race = best[1]
            if race is None:
                reason = (f"venue '{venue}' resolved to '{vkey}' but no "
                          f"scheduled race matches race number/jump time")

        if race is None:
            unmapped_markets.append({
                "market_id": market_id, "venue": venue, "market_name": market_name,
                "start_time": market.get("marketStartTime"),
                "reason": reason,
            })
            continue

        race_id = make_race_id(race["race_date"], race["track"], race["race_number"])
        if race_id in claimed_races:
            unmapped_markets.append({
                "market_id": market_id, "venue": venue, "market_name": market_name,
                "start_time": market.get("marketStartTime"),
                "reason": f"race already mapped by market {claimed_races[race_id]}",
            })
            continue
        claimed_races[race_id] = market_id

        runners = []
        for runner in market.get("runners", []):
            selection_id = runner.get("selectionId")
            horse_name = clean_runner_name(runner.get("runnerName"))
            horse_id = bridge.get(norm_name(horse_name))
            if not horse_id:
                unmapped_runners.append({
                    "market_id": market_id, "selection_id": selection_id,
                    "runner_name": horse_name, "race_id": race_id,
                    "reason": "no horse_id in race_results_history bridge",
                })
                continue
            runners.append({
                "selection_id": selection_id,
                "horse_name": horse_name,
                "horse_id": horse_id,
            })

        mapped.append({
            "market_id": market_id,
            "market_name": market_name,
            "venue": venue,
            "start_time": start_time,
            "race_id": race_id,
            "race_date": race["race_date"],
            "track": race["track"],
            "race_number": int(race["race_number"]),
            "runners": runners,
        })

    return mapped, unmapped_markets, unmapped_runners
