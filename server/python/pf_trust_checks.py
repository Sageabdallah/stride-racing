#!/usr/bin/env python3
"""PF trust checks — Phase D: quantify PF-vs-old-pipeline discrepancies on
the overlap window before any model feature is built on PF data.

READ-ONLY. Three reports, all computed from data already in the DB (nothing
is refetched from the PF API — the Starter tier only serves ~31 days back,
so the pf_raw_payloads archive is the only permanent record of the overlap):

  1. Result parity on overlap days (days with both old-pipeline rows —
     race_id NOT LIKE 'pf%' — and PF results in the raw archive):
       * winner agreement per race
       * margin deltas, histogram buckets exact / <=0.5L / <=1L / >1L
       * field-size deltas
       * track-name normalisation collisions (two raw names mapping to one
         lower(track)) and alias duplicates (the same meeting stored under
         two different track names — the dedup key is (race_date,
         lower(track), race_number), so a different spelling slips past it)
       * horse-name bridge disagreements (same race, same position,
         different horse_id)
  2. Sectionals alignment: for dates with both PF results and
     sectional_times rows, runner-set intersection per race (names via
     pf_results_mapper.norm_name). PF Starter has no sectionals — the
     racing.com/RQ collectors stay in service; what is checked here is that
     the two sources agree on WHO ran.
  3. Coverage by state: PF resulted meetings vs the DB's historical average
     meetings-per-state-per-weekday — a test of the "92% of TAB meetings"
     claim on our own data.

Meeting matching across sources is by runner-name overlap, NOT track-name
string equality: the same meeting appears as e.g. 'Ballarat Synthetic' (PF)
and 'Sportsbet-Ballarat Synthetic' (old pipeline). Two meetings on the same
date are the same meeting when they share >=3 normalised runner names and
the overlap covers >=50% of the PF field.

Exit codes: 0 = report produced; 1 = no DATABASE_URL, DB failure, or no
overlap data (a trust report on nothing is a loud failure, not a green run).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

from pf_results_mapper import norm_name

STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Meeting-identity matching thresholds (see module docstring).
MIN_SHARED_NAMES = 3
MIN_OVERLAP_RATIO = 0.5

# Country one-off tracks whose state rests on knowledge, not data
# (single picnic/annual meetings): Richmond (taken as QLD), Wean (NSW),
# Oak Park (QLD), Mallawa (NSW), Hawker (SA), Flinton (QLD),
# Burrandowan (QLD), Illawarra Grange (NSW, = Kembla Grange).
_TRACK_STATE = {
    # NSW
    "albury": "NSW", "armidale": "NSW", "ballina": "NSW", "bathurst": "NSW",
    "binnaway": "NSW", "boorowa": "NSW", "bourke": "NSW", "bowraville": "NSW",
    "brewarrina": "NSW", "canterbury": "NSW", "canterburypark": "NSW",
    "carinda": "NSW", "casino": "NSW", "cobar": "NSW", "coffsharbour": "NSW",
    "coonabarabran": "NSW", "cootamundra": "NSW", "corowa": "NSW",
    "cowra": "NSW", "deniliquin": "NSW", "dubbo": "NSW", "gilgandra": "NSW",
    "gosford": "NSW", "goulburn": "NSW", "grafton": "NSW", "grenfell": "NSW",
    "gulgong": "NSW", "gundagai": "NSW", "gunnedah": "NSW",
    "hawkesbury": "NSW", "hillston": "NSW", "illawarragrange": "NSW",
    "inverell": "NSW", "kemblagrange": "NSW", "kempsey": "NSW",
    "kensington": "NSW", "leeton": "NSW", "lightningridge": "NSW",
    "mallawa": "NSW", "merriwa": "NSW", "moree": "NSW", "moruya": "NSW",
    "mudgee": "NSW", "mungindi": "NSW", "murwillumbah": "NSW",
    "muswellbrook": "NSW", "narrandera": "NSW", "narromine": "NSW",
    "newcastle": "NSW", "nowra": "NSW", "nyngan": "NSW", "orange": "NSW",
    "parkes": "NSW", "portmacquarie": "NSW", "quambone": "NSW",
    "queanbeyan": "NSW", "quirindi": "NSW", "randwick": "NSW",
    "randwickkensington": "NSW", "rosehill": "NSW", "rosehillgardens": "NSW",
    "royalrandwick": "NSW", "sapphirecoast": "NSW", "scone": "NSW",
    "tamworth": "NSW", "taree": "NSW", "tocumwal": "NSW", "tomingley": "NSW",
    "trangie": "NSW", "tuncurry": "NSW", "wagga": "NSW",
    "waggariverside": "NSW", "walgett": "NSW", "warialda": "NSW",
    "warren": "NSW", "warwickfarm": "NSW", "wauchope": "NSW", "wean": "NSW",
    "wellington": "NSW", "wyong": "NSW",
    # VIC
    "ararat": "VIC", "bairnsdale": "VIC", "bet365bairnsdale": "VIC",
    "ballarat": "VIC", "ballaratsynthetic": "VIC",
    "sportsbetballarat": "VIC", "sportsbetballaratsynthetic": "VIC",
    "balnarring": "VIC", "bendigo": "VIC", "betdeluxewarracknabeal": "VIC",
    "casterton": "VIC", "caulfield": "VIC", "caulfieldheath": "VIC",
    "coleraine": "VIC", "cranbourne": "VIC", "southsidecranbourne": "VIC",
    "donald": "VIC", "echuca": "VIC", "bet365echuca": "VIC",
    "flemington": "VIC", "geelong": "VIC", "ladbrokesgeelong": "VIC",
    "hamilton": "VIC", "bet365hamilton": "VIC", "horsham": "VIC",
    "kerang": "VIC", "bet365parkkilmore": "VIC", "bet365parkkyneton": "VIC",
    "mildura": "VIC", "bet365mildura": "VIC", "moe": "VIC",
    "mornington": "VIC", "murtoa": "VIC", "pakenhamsynthetic": "VIC",
    "southsidepakenham": "VIC", "southsidepakenhamsynthetic": "VIC",
    "sale": "VIC", "sandownhillside": "VIC", "sandownlakeside": "VIC",
    "sportsbetsandownhillside": "VIC", "sportsbetsandownlakeside": "VIC",
    "seymour": "VIC", "bet365seymour": "VIC", "bet365stawell": "VIC",
    "swanhill": "VIC", "bet365swanhill": "VIC", "swiftscreek": "VIC",
    "bet365terang": "VIC", "wangaratta": "VIC", "sportsbetwangaratta": "VIC",
    "warrnambool": "VIC", "picklebetparkwerribee": "VIC", "wodonga": "VIC",
    "picklebetparkwodonga": "VIC", "yea": "VIC",
    # QLD
    "alpha": "QLD", "atherton": "QLD", "augathella": "QLD",
    "barcaldine": "QLD", "beaudesert": "QLD", "aquisbeaudesert": "QLD",
    "blackall": "QLD", "bluff": "QLD", "sportsbetbowen": "QLD",
    "bundaberg": "QLD", "sportsbetbundaberg": "QLD", "burrandowan": "QLD",
    "cairns": "QLD", "ladbrokescannonpark": "QLD", "charleville": "QLD",
    "chinchilla": "QLD", "clermont": "QLD", "cloncurry": "QLD",
    "corfield": "QLD", "cunnamulla": "QLD", "dalby": "QLD", "doomben": "QLD",
    "eaglefarm": "QLD", "emerald": "QLD", "esk": "QLD", "flinton": "QLD",
    "gatton": "QLD", "gayndah": "QLD", "sportsbetgladstone": "QLD",
    "goldcoast": "QLD", "aquisparkgoldcoast": "QLD",
    "aquisparkgoldcoastpoly": "QLD", "goondiwindi": "QLD",
    "gordonvale": "QLD", "gregorydowns": "QLD", "gympie": "QLD",
    "homehill": "QLD", "hughenden": "QLD", "ilfracombe": "QLD",
    "ingham": "QLD", "injune": "QLD", "innisfail": "QLD", "ipswich": "QLD",
    "jandowae": "QLD", "juliacreek": "QLD", "kilcoy": "QLD", "lauraq": "QLD",
    "longreach": "QLD", "sportsbetlongreach": "QLD", "mackay": "QLD",
    "sportsbetmareeba": "QLD", "maxwelton": "QLD", "middlemount": "QLD",
    "moranbah": "QLD", "mountgarnet": "QLD", "mtisa": "QLD",
    "sportsbetmountisa": "QLD", "sportsbetnanango": "QLD", "noorama": "QLD",
    "oakpark": "QLD", "quilpie": "QLD", "richmond": "QLD",
    "rockhampton": "QLD", "roma": "QLD", "stamford": "QLD",
    "sunshinecoast": "QLD", "sunshinecoastpolytrack": "QLD", "talmoi": "QLD",
    "tambo": "QLD", "thangool": "QLD", "toowoomba": "QLD",
    "townsville": "QLD", "picklebetparkwarwick": "QLD", "wondai": "QLD",
    "yeppoon": "QLD",
    # SA
    "balaklava": "SA", "bordertown": "SA", "clare": "SA",
    "sportsbetgawler": "SA", "hawker": "SA", "morphettville": "SA",
    "morphettvilleparks": "SA", "mountgambier": "SA", "mtgambier": "SA",
    "murraybridgegh": "SA", "thomasfarmsrcmurraybridge": "SA",
    "naracoorte": "SA", "sportsbetoakbank": "SA", "penola": "SA",
    "portaugusta": "SA", "roxbydowns": "SA", "strathalbyn": "SA",
    # WA
    "albany": "WA", "ascot": "WA", "belmont": "WA", "belmontpark": "WA",
    "broome": "WA", "bunbury": "WA", "carnarvon": "WA", "derby": "WA",
    "dongara": "WA", "geraldton": "WA", "kalgoorlie": "WA",
    "marblebar": "WA", "narrogin": "WA", "northam": "WA", "pinjarra": "WA",
    "pinjarrapark": "WA", "pinjarrascarpside": "WA", "porthedland": "WA",
    "roebourne": "WA", "rockingham": "WA", "york": "WA",
    # TAS
    "devonportsynthetic": "TAS", "devonporttapetasynthetic": "TAS",
    "hobart": "TAS", "launceston": "TAS",
    # NT
    "darwin": "NT", "fanniebay": "NT", "ladbrokespioneerpark": "NT",
    "pioneerpark": "NT", "tennantcreek": "NT",
    # ACT
    "canberra": "ACT", "canberraacton": "ACT",
}


def norm_track(track):
    """Track-name key for the state lookup: lowercase, letters+digits only.
    NOT a meeting-identity key — meeting identity is by runner overlap."""
    return re.sub(r"[^a-z0-9]+", "", str(track or "").lower())


def state_for(track):
    """State for a track name, or None if unmapped (callers report loudly)."""
    return _TRACK_STATE.get(norm_track(track))


# --------------------------------------------------------------------------
# Pure data-shaping helpers (unit-tested, no DB)
# --------------------------------------------------------------------------

def build_pf_day(meeting_payloads):
    """PF archive payloads for one date -> {track: {"races": {rnum: race},
    "names": set}}. race = {"runners": {norm: {"name","position","margin"}}}.
    Payloads may be a list of meetings or a single meeting dict."""
    meetings = {}
    for payload in meeting_payloads:
        for m in (payload if isinstance(payload, list) else [payload]):
            track = m.get("track")
            if not track:
                continue
            entry = meetings.setdefault(track, {"races": {}, "names": set()})
            for race in m.get("raceResults") or []:
                rnum = race.get("raceNumber")
                runners = {}
                for r in race.get("runners") or []:
                    name = r.get("runner")
                    key = norm_name(name)
                    if not key:
                        continue
                    pos = r.get("position")
                    try:
                        pos = int(pos)
                    except (TypeError, ValueError):
                        pos = None
                    if pos is not None and pos <= 0:
                        pos = None  # scratched / no finishing position
                    margin = r.get("margin")
                    try:
                        margin = float(margin)
                    except (TypeError, ValueError):
                        margin = None
                    runners[key] = {"name": name, "position": pos,
                                    "margin": margin}
                    entry["names"].add(key)
                if rnum is not None:
                    entry["races"][int(rnum)] = {"runners": runners}
    return meetings


def build_old_day(rows):
    """Old-pipeline RRH rows for one date -> same shape as build_pf_day.
    rows: iterable of (track, race_number, position, horse_name, horse_id,
    margin_lengths, field_size)."""
    meetings = {}
    for track, rnum, pos, name, horse_id, margin, field_size in rows:
        entry = meetings.setdefault(track, {"races": {}, "names": set()})
        race = entry["races"].setdefault(int(rnum), {"runners": {},
                                                     "field_size": None})
        key = norm_name(name)
        if not key:
            continue
        race["runners"][key] = {"name": name, "position": pos,
                                "margin": margin, "horse_id": horse_id}
        entry["names"].add(key)
        if field_size is not None:
            race["field_size"] = max(race["field_size"] or 0, field_size)
    return meetings


def match_meetings(pf_meetings, old_meetings,
                   min_shared=MIN_SHARED_NAMES, min_ratio=MIN_OVERLAP_RATIO):
    """Match PF meetings to old-pipeline meetings on the same date by
    runner-name overlap. Returns (matches, unmatched_pf, unmatched_old);
    matches = [(pf_track, old_track, shared, ratio)]."""
    matches, unmatched_pf, used_old = [], [], set()
    for pf_track, pf in sorted(pf_meetings.items()):
        best, best_key = None, None
        for old_track, old in old_meetings.items():
            if old_track in used_old:
                continue
            shared = len(pf["names"] & old["names"])
            ratio = shared / max(1, len(pf["names"]))
            if shared >= min_shared and ratio >= min_ratio:
                if best is None or shared > best[2]:
                    best, best_key = (pf_track, old_track, shared, ratio), old_track
        if best:
            matches.append(best)
            used_old.add(best_key)
        else:
            unmatched_pf.append(pf_track)
    unmatched_old = [t for t in old_meetings if t not in used_old]
    return matches, unmatched_pf, unmatched_old


def margin_bucket(delta):
    if delta < 1e-9:
        return "exact"
    if delta <= 0.5:
        return "<=0.5L"
    if delta <= 1.0:
        return "<=1L"
    return ">1L"


def compare_race(pf_race, old_race):
    """Compare one matched race. Returns a dict of counts:
      winner_agree / winner_disagree / winner_unknown
      margin histogram (exact/<=0.5L/<=1L/>1L) over shared placed runners
      winner_null_semantic: old stores NULL margin for the winner, PF stores
        the winning margin — counted separately, not a delta
      null_mismatch: any other null-vs-value margin pairing
      shared_runners, pf_only_runners, old_only_runners (placed runners)
      field_delta: pf placed count - old field_size (None if unknown)"""
    out = {"winner": None, "margins": Counter(), "winner_null_semantic": 0,
           "null_mismatch": 0, "shared": 0, "pf_only": [], "old_only": [],
           "field_delta": None}
    pf_placed = {k: v for k, v in pf_race["runners"].items()
                 if v.get("position") and v["position"] >= 1}
    old_placed = {k: v for k, v in old_race["runners"].items()
                  if v.get("position") and v["position"] >= 1}
    pf_winner = {k for k, v in pf_placed.items() if v["position"] == 1}
    old_winner = {k for k, v in old_placed.items() if v["position"] == 1}
    if pf_winner and old_winner:
        out["winner"] = bool(pf_winner & old_winner)
    for key in sorted(set(pf_placed) | set(old_placed)):
        p, o = pf_placed.get(key), old_placed.get(key)
        if p is None:
            out["old_only"].append(key)
            continue
        if o is None:
            out["pf_only"].append(key)
            continue
        out["shared"] += 1
        pm, om = p.get("margin"), o.get("margin")
        if pm is not None and om is not None:
            out["margins"][margin_bucket(abs(pm - om))] += 1
        elif p["position"] == 1 and om is None:
            out["winner_null_semantic"] += 1
        else:
            out["null_mismatch"] += 1
    if old_race.get("field_size") is not None:
        out["field_delta"] = len(pf_placed) - old_race["field_size"]
    return out


def literal_track_collisions(tracks):
    """Distinct raw track names sharing one lower(track) — the case the
    (race_date, lower(track), race_number) dedup key cannot tell apart.
    tracks: iterable of raw names. Returns {lower: [raw, ...]} (len > 1)."""
    by_lower = defaultdict(set)
    for t in tracks:
        by_lower[str(t).lower()].add(t)
    return {k: sorted(v) for k, v in by_lower.items() if len(v) > 1}


def bridge_disagreements(pf_rows, old_rows):
    """Same race, same position, different horse_id — for races stored by
    BOTH sources (alias duplicates). pf_rows/old_rows: (race_number,
    position, horse_name, horse_id). Returns (same_name_diff_id,
    diff_name_diff_id) lists of detail tuples."""
    old_by_pos = {(int(r), p): (norm_name(n), hid)
                  for r, p, n, hid in old_rows if p is not None}
    same_name, diff_name = [], []
    for r, p, n, hid in pf_rows:
        if p is None:
            continue
        o = old_by_pos.get((int(r), p))
        if o and o[1] != hid:
            (same_name if o[0] == norm_name(n) else diff_name).append(
                (int(r), p, n, hid, o[1]))
    return same_name, diff_name


def sectionals_alignment(sec_races, pf_meetings):
    """One date. sec_races: {(sec_track, rnum): set(norm names)}.
    pf_meetings: build_pf_day output. Sectional tracks are matched to PF
    meetings by runner overlap. Returns (report, unmatched_sec) where report
    covers EVERY sectional race on a matched meeting: (sec_track, pf_track,
    rnum, sec_only, pf_only, no_pf_race). Clean races have empty diffs."""
    sec_meeting_names = defaultdict(set)
    for (track, _rnum), names in sec_races.items():
        sec_meeting_names[track] |= names
    sec_as_meetings = {t: {"names": n} for t, n in sec_meeting_names.items()}
    pf_as_meetings = {t: {"names": m["names"]} for t, m in pf_meetings.items()}
    matches, unmatched_sec, _ = match_meetings(sec_as_meetings, pf_as_meetings)
    report = []
    for sec_track, pf_track, _shared, _ratio in matches:
        pf_races = pf_meetings[pf_track]["races"]
        for (t, rnum), names in sorted(sec_races.items()):
            if t != sec_track:
                continue
            pf_race = pf_races.get(rnum)
            pf_names = ({k for k, v in pf_race["runners"].items()
                         if v.get("position") and v["position"] >= 1}
                        if pf_race else set())
            report.append((sec_track, pf_track, rnum,
                           sorted(names - pf_names), sorted(pf_names - names),
                           pf_race is None))
    return report, unmatched_sec


def state_rollup(pf_meetings, hist_meetings, pf_span, hist_span):
    """Meetings-per-state-per-weekday, PF vs historical.
    pf_meetings/hist_meetings: iterable of (iso_date, track).
    pf_span/hist_span: (start_iso, end_iso) inclusive — weekday occurrence
    counts come from the span, so partial windows average honestly.
    Returns (table, unmapped) where table[state][weekday] =
    {"pf": float, "hist": float}; unmapped = sorted track names with no
    state mapping (excluded from the table)."""
    def weekday_counts(span):
        start = date.fromisoformat(span[0])
        end = date.fromisoformat(span[1])
        counts = Counter()
        d = start
        while d <= end:
            counts[d.weekday()] += 1
            d += timedelta(days=1)
        return counts

    raw = {"pf": defaultdict(Counter), "hist": defaultdict(Counter)}
    unmapped = set()
    for label, meetings, span in (("pf", pf_meetings, pf_span),
                                  ("hist", hist_meetings, hist_span)):
        wd_counts = weekday_counts(span)
        per_meeting = Counter()
        for iso, track in meetings:
            st = state_for(track)
            if st is None:
                unmapped.add(track)
                continue
            per_meeting[(st, date.fromisoformat(str(iso)).weekday())] += 1
        for (st, wd), n in per_meeting.items():
            raw[label][st][wd] = n / wd_counts[wd]
    table = {st: {WEEKDAYS[wd]: {"pf": raw["pf"][st].get(wd, 0.0),
                                 "hist": raw["hist"][st].get(wd, 0.0)}
                  for wd in range(7)} for st in STATES}
    return table, sorted(unmapped)


# --------------------------------------------------------------------------
# DB access (front-loaded: Neon drops idle SSL connections during long
# compute, so every read happens up front inside the read-only session)
# --------------------------------------------------------------------------

def load_all(cur, start, end, hist_start, hist_end):
    data = {}
    cur.execute(
        "SELECT ref_date::text, payload FROM pf_raw_payloads "
        "WHERE kind='results' AND ref_date BETWEEN %s AND %s ORDER BY 1",
        (start, end))
    archive = defaultdict(list)
    for ref_date, payload in cur.fetchall():
        archive[ref_date].append(payload)
    data["archive"] = archive

    cur.execute(
        "SELECT race_date, track, race_number, position, horse_name, "
        "horse_id, margin_lengths, field_size FROM race_results_history "
        "WHERE race_id NOT LIKE 'pf%%' AND race_date BETWEEN %s AND %s",
        (start, end))
    old_rows = defaultdict(list)
    for row in cur.fetchall():
        old_rows[row[0]].append(row[1:])
    data["old_rows"] = old_rows

    cur.execute(
        "SELECT race_date, track, race_number, position, horse_name, "
        "horse_id FROM race_results_history "
        "WHERE race_id LIKE 'pf%%' AND race_date BETWEEN %s AND %s",
        (start, end))
    pf_rows = defaultdict(list)
    for row in cur.fetchall():
        pf_rows[row[0]].append(row[1:])
    data["pf_rows"] = pf_rows

    cur.execute(
        "SELECT race_date, track, race_number, horse_name "
        "FROM sectional_times WHERE race_date BETWEEN %s AND %s",
        (start, end))
    sec = defaultdict(lambda: defaultdict(set))
    for race_date, track, rnum, name in cur.fetchall():
        key = norm_name(name)
        if key:
            sec[race_date][(track, int(rnum))].add(key)
    data["sectionals"] = sec

    cur.execute(
        "SELECT DISTINCT race_date, track FROM race_results_history "
        "WHERE race_id NOT LIKE 'pf%%' AND race_date BETWEEN %s AND %s",
        (hist_start, hist_end))
    data["hist_meetings"] = cur.fetchall()

    cur.execute(
        "SELECT ref_date::text, m->>'track' FROM pf_raw_payloads, "
        "jsonb_array_elements(payload) m WHERE kind='results'")
    data["pf_meetings_all"] = cur.fetchall()

    cur.execute(
        "SELECT DISTINCT track FROM race_results_history "
        "WHERE race_date BETWEEN %s AND %s", (start, end))
    data["window_tracks"] = [r[0] for r in cur.fetchall()]
    return data


def compare_day(pf_day, old_day):
    """Pure parity comparison for one date (build_pf_day / build_old_day
    outputs). Returns a dict: tot (Counter), margin_hist (Counter),
    field_deltas (Counter), alias_pairs (Counter ((pf,old)->days)),
    winner_disagreements (list of (pf_track, rnum)), matches,
    unmatched_pf, unmatched_old."""
    out = {"tot": Counter(), "margin_hist": Counter(),
           "field_deltas": Counter(), "alias_pairs": Counter(),
           "winner_disagreements": []}
    matches, unmatched_pf, unmatched_old = match_meetings(pf_day, old_day)
    out["matches"] = matches
    out["unmatched_pf"] = unmatched_pf
    out["unmatched_old"] = unmatched_old
    tot = out["tot"]
    for pf_track, old_track, _shared, _ratio in matches:
        if pf_track != old_track:
            out["alias_pairs"][(pf_track, old_track)] += 1
        pf_races = pf_day[pf_track]["races"]
        old_races = old_day[old_track]["races"]
        for rnum in sorted(pf_races):
            tot["pf_races"] += 1
            old_race = old_races.get(rnum)
            if old_race is None:
                tot["races_pf_only"] += 1
                continue
            tot["races_compared"] += 1
            cmp = compare_race(pf_races[rnum], old_race)
            if cmp["winner"] is True:
                tot["winner_agree"] += 1
            elif cmp["winner"] is False:
                tot["winner_disagree"] += 1
                out["winner_disagreements"].append((pf_track, rnum))
            else:
                tot["winner_unknown"] += 1
            out["margin_hist"].update(cmp["margins"])
            tot["winner_null_semantic"] += cmp["winner_null_semantic"]
            tot["null_mismatch"] += cmp["null_mismatch"]
            tot["shared_runners"] += cmp["shared"]
            tot["pf_only_runners"] += len(cmp["pf_only"])
            tot["old_only_runners"] += len(cmp["old_only"])
            if cmp["field_delta"] is not None:
                out["field_deltas"][cmp["field_delta"]] += 1
                pf_placed_n = sum(
                    1 for v in pf_races[rnum]["runners"].values()
                    if v.get("position") and v["position"] >= 1)
                if cmp["field_delta"] != 0 and pf_placed_n == 0:
                    tot["field_delta_void_shells"] += 1
        for rnum in old_races:
            if rnum not in pf_races:
                tot["races_old_only"] += 1
    return out


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def report_parity(data, start, end):
    print("=" * 72)
    print("REPORT 1 — RESULT PARITY (old pipeline vs PF raw archive)")
    print("=" * 72)
    overlap = sorted(set(data["archive"]) & set(data["old_rows"]))
    print(f"overlap days (old rows AND PF archive): {len(overlap)} "
          f"({overlap[0] if overlap else '-'} .. {overlap[-1] if overlap else '-'})")
    if not overlap:
        print("FATAL: no overlap days — nothing to measure")
        return False

    tot = Counter()
    margin_hist = Counter()
    field_deltas = Counter()
    alias_pairs = Counter()
    bridge_same, bridge_diff = Counter(), Counter()
    winner_disagreements = []
    unmatched_pf_all, unmatched_old_all = Counter(), Counter()

    for day in overlap:
        pf_day = build_pf_day(data["archive"][day])
        old_day = build_old_day(data["old_rows"][day])
        res = compare_day(pf_day, old_day)
        unmatched_pf_all[day] = len(res["unmatched_pf"])
        unmatched_old_all[day] = len(res["unmatched_old"])
        tot.update(res["tot"])
        margin_hist.update(res["margin_hist"])
        field_deltas.update(res["field_deltas"])
        alias_pairs.update(res["alias_pairs"])
        winner_disagreements.extend((day, t, r)
                                    for t, r in res["winner_disagreements"])
        # bridge disagreements need the pf% rows actually stored
        pf_stored = [r for r in data["pf_rows"].get(day, [])]
        if pf_stored:
            old_rows_day = data["old_rows"][day]
            for pf_track, old_track, _s, _r in res["matches"]:
                sub_pf = [(r[1], r[2], r[3], r[4]) for r in pf_stored
                          if r[0] == pf_track]
                sub_old = [(r[1], r[2], r[3], r[4]) for r in old_rows_day
                           if r[0] == old_track]
                if sub_pf and sub_old:
                    same, diff = bridge_disagreements(sub_pf, sub_old)
                    bridge_same[day] += len(same)
                    bridge_diff[day] += len(diff)

    n_cmp = tot["races_compared"]
    print(f"\nmeetings matched by runner overlap; "
          f"unmatched PF meetings by day: {dict(unmatched_pf_all)}")
    print(f"unmatched old-pipeline meetings by day: {dict(unmatched_old_all)}")
    print(f"\nraces in PF archive on matched meetings: {tot['pf_races']}")
    print(f"races compared (same race_number both sides): {n_cmp}")
    print(f"races only in PF: {tot['races_pf_only']}   "
          f"races only in old pipeline: {tot['races_old_only']}")
    decided = tot["winner_agree"] + tot["winner_disagree"]
    pct = (100.0 * tot["winner_agree"] / decided) if decided else 0.0
    print(f"\nWINNER AGREEMENT: {tot['winner_agree']}/{decided} = {pct:.1f}% "
          f"(unknown: {tot['winner_unknown']})")
    if winner_disagreements:
        print("  disagreements (day, track, race):")
        for d, t, r in winner_disagreements[:20]:
            print(f"    {d} {t} R{r}")
    total_margins = sum(margin_hist.values())
    print(f"\nMARGIN DELTAS over {total_margins} shared placed runners "
          f"(|pf - old|, buckets):")
    for b in ("exact", "<=0.5L", "<=1L", ">1L"):
        n = margin_hist.get(b, 0)
        share = (100.0 * n / total_margins) if total_margins else 0.0
        print(f"    {b:>7}: {n:5d}  ({share:.1f}%)")
    print(f"  old-NULL-winner semantic (old stores NULL for winner, PF the "
          f"winning margin): {tot['winner_null_semantic']} — excluded from "
          f"the histogram, not a discrepancy")
    print(f"  other null-mismatch pairings: {tot['null_mismatch']}")
    print(f"\nFIELD-SIZE DELTAS (pf placed count - old field_size):")
    for d in sorted(field_deltas):
        print(f"    {d:+d}: {field_deltas[d]}")
    print(f"  of the non-zero deltas, races where PF has zero placed runners "
          f"(abandoned/void race shells — old pipeline kept NULL-position "
          f"runner rows with the full field_size): "
          f"{tot['field_delta_void_shells']}")
    print(f"\nRUNNER-SET agreement on compared races: shared "
          f"{tot['shared_runners']}, pf-only {tot['pf_only_runners']}, "
          f"old-only {tot['old_only_runners']}")

    collisions = literal_track_collisions(data["window_tracks"])
    print(f"\nTRACK-NAME NORMALISATION COLLISIONS (two raw names, one "
          f"lower(track)): {len(collisions)}")
    for low, raws in sorted(collisions.items()):
        print(f"    {low}: {raws}")
    print(f"ALIAS DUPLICATE PAIRS (same meeting, different stored track "
          f"name — slips past the dedup key): {len(alias_pairs)}")
    for (pf_t, old_t), n in sorted(alias_pairs.items()):
        print(f"    '{pf_t}' (PF) vs '{old_t}' (old) on {n} day(s)")

    print(f"\nBRIDGE DISAGREEMENTS on duplicate-stored races (same race, "
          f"same position, different horse_id):")
    print(f"    same normalised name, different id (bridge miss): "
          f"{sum(bridge_same.values())}")
    print(f"    different name at same position (source disagreement): "
          f"{sum(bridge_diff.values())}")
    return True


def report_sectionals(data):
    print("\n" + "=" * 72)
    print("REPORT 2 — SECTIONALS ALIGNMENT (sectional_times vs PF raw archive)")
    print("=" * 72)
    days = sorted(set(data["sectionals"]) & set(data["archive"]))
    print(f"dates with both sectionals and PF archive: {len(days)} "
          f"({days[0] if days else '-'} .. {days[-1] if days else '-'})")
    if not days:
        print("no sectionals overlap — nothing to measure")
        return
    n_races = n_unmatched_races = 0
    mismatches, missing, unmatched = [], [], []
    for day in days:
        pf_day = build_pf_day(data["archive"][day])
        sec_races = data["sectionals"][day]
        report, unmatched_sec = sectionals_alignment(sec_races, pf_day)
        unmatched.extend((day, t) for t in unmatched_sec)
        n_unmatched_races += sum(1 for (t, _r) in sec_races
                                 if t in unmatched_sec)
        n_races += len(sec_races)
        for r in report:
            if r[5]:
                missing.append((day,) + r)
            elif r[3] or r[4]:
                mismatches.append((day,) + r)
    n_diff = len(mismatches)
    print(f"sectional races checked: {n_races}; fully aligned: "
          f"{n_races - n_diff - len(missing) - n_unmatched_races}")
    print(f"races with runner-set differences: {n_diff}")
    print(f"sectional races with NO PF race at that number: {len(missing)}")
    if unmatched:
        print("sectional meetings with no PF meeting match:")
        for d, t in unmatched:
            print(f"    {d} {t}")
    for day, sec_t, pf_t, rnum, sec_only, pf_only, no_pf in (missing + mismatches)[:40]:
        if no_pf:
            print(f"    {day} {sec_t} R{rnum}: no PF race")
        else:
            print(f"    {day} {sec_t}->{pf_t} R{rnum}: "
                  f"sectionals-only={sec_only} pf-only={pf_only}")
    if len(missing) + len(mismatches) > 40:
        print(f"    ... and {len(missing) + len(mismatches) - 40} more")


def report_coverage(data, hist_start, hist_end):
    print("\n" + "=" * 72)
    print("REPORT 3 — COVERAGE BY STATE (PF meetings vs DB historical average)")
    print("=" * 72)
    pf_meetings = data["pf_meetings_all"]
    if not pf_meetings:
        print("FATAL: no PF archive meetings")
        return False
    pf_dates = sorted({d for d, _t in pf_meetings})
    pf_span = (pf_dates[0], pf_dates[-1])
    hist_span = (hist_start, hist_end)
    table, unmapped = state_rollup(pf_meetings, data["hist_meetings"],
                                   pf_span, hist_span)
    print(f"PF window: {pf_span[0]} .. {pf_span[1]} "
          f"({len(pf_dates)} days, {len(pf_meetings)} meetings)")
    print(f"historical window: {hist_span[0]} .. {hist_span[1]} "
          f"({len(data['hist_meetings'])} meetings)")
    if unmapped:
        print(f"WARNING unmapped tracks (excluded): {unmapped}")
    header = "state " + " ".join(f"{wd:>11}" for wd in WEEKDAYS) + "   weekly ratio"
    print(header)
    print("       (each cell: pf_avg/hist_avg meetings per weekday)")
    overall_pf = overall_hist = 0.0
    for st in STATES:
        cells, pf_wk, hist_wk = [], 0.0, 0.0
        for wd in WEEKDAYS:
            c = table[st][wd]
            pf_wk += c["pf"]
            hist_wk += c["hist"]
            cells.append(f"{c['pf']:4.1f}/{c['hist']:<4.1f}  ")
        overall_pf += pf_wk
        overall_hist += hist_wk
        ratio = (pf_wk / hist_wk) if hist_wk else 0.0
        print(f"{st:5} " + " ".join(cells) + f"   {ratio:5.1%}")
    print(f"\nOVERALL PF meetings/week {overall_pf:.1f} vs historical "
          f"{overall_hist:.1f} -> ratio {overall_pf / overall_hist:.1%} "
          f"(claim under test: 92%)")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2026-06-30",
                    help="overlap window start (default 2026-06-30 = first "
                         "PF coverage day)")
    ap.add_argument("--end", default="2026-07-13",
                    help="overlap window end (default 2026-07-13 = last "
                         "old-pipeline day)")
    ap.add_argument("--hist-start", default="2026-04-01")
    ap.add_argument("--hist-end", default="2026-07-13")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        return 1
    import psycopg2
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    data = load_all(cur, args.start, args.end, args.hist_start, args.hist_end)
    conn.close()

    ok = report_parity(data, args.start, args.end)
    if not ok:
        return 1
    report_sectionals(data)
    report_coverage(data, args.hist_start, args.hist_end)
    print("\nDone. Read-only; nothing written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
