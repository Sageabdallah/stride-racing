#!/usr/bin/env python3
"""Shared Punting Form results mapper — the single implementation of the
race_results_history contract for every PF-sourced writer.

Extracted from pf_backfill_results.py (the audited Phase B reference) so the
backfill, the daily importer (fetch_and_import_date.py) and the racecard
serve path all share ONE mapping rather than drifting copies. What lives
here:

  * norm_name — the horse-name normaliser behind the ID bridge (lowercase,
    country suffix like '(NZ)' stripped, letters+digits only). One name rule
    across PF, the DB, and any other source: three systems, one normaliser.
  * the horse-ID bridge — load_bridge (normalised name -> existing horse_id,
    most recent prior row wins) and resolve_horse_id (only a genuinely
    unknown horse gets a 'pf<runnerId>' id; 442 SQL sites join on horse_id,
    so forking an identity silently corrupts every feature built on it).
  * the payload -> 21-column row mapping (build_rows) with the
    trials/unplaced exclusion (barrier-trial meetings filtered at the
    meeting level; scratched/no-position runners dropped at the runner
    level).
  * two skip-if-existing dedup predicates, both canonicalising the track
    key while the stored track column keeps the source spelling (closing
    the F-TRACK-ALIAS double-insert hole):
      - race granularity (load_existing_keys + row_race_key) for the bulk
        backfill tools, which never revisit a date to resolve late results;
      - runner granularity (load_existing_runner_keys + row_runner_key)
        for the daily importer, whose next-day re-import exists to top up
        races stored while placings were still pending — a race-level key
        skips such a race forever once any of its rows exist (issue #126).
  * the pf_raw_payloads archival insert (archive_payloads) — every fetched
    payload lands in the archive before the row insert, in the same
    transaction; the Starter subscription only serves ~31 days back, so this
    archive is the permanent history being accrued. Never skip it on a
    committing run.

Importing this module never requires psycopg2 (lazy imports inside the DB
helpers) and never touches the network.
"""
import json
import re

from identity_normalization import normalize_runner_key

METRO_TRACKS = [
    "flemington", "caulfield", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "gold coast", "morphettville",
    "newcastle", "kembla grange", "ascot", "belmont",
]

RRH_COLUMNS = (
    "horse_id", "horse_name", "race_id", "track", "race_date",
    "distance_m", "race_class", "class_level", "going", "position",
    "margin_lengths", "weight_kg", "jockey", "jockey_id", "barrier",
    "sp_odds", "field_size", "race_name", "race_number", "form_string",
    "opponents_json"
)

RAW_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS pf_raw_payloads (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    ref_date DATE,
    meeting_id BIGINT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
)
"""


def norm_name(name):
    """Horse-name key for the ID bridge: lowercase, no country suffix like
    '(NZ)', letters+digits only."""
    return normalize_runner_key(name)


# --------------------------------------------------------- track canonical

# Track-name aliases for the DEDUP KEY ONLY (the stored track column keeps
# the source spelling). Single-sourced here (moved from
# auto_results_collector.py, which imports it back); two research scripts
# under server/python/research/ keep their own copies.
#
# Keys are normalised (lowercase, letters+digits only). Targets are the
# canonical key for one physical track. Entries fall in two groups:
#   1. the collector's pre-existing map (behaviour pinned, do not re-target);
#   2. F-TRACK-ALIAS additions — each cites its K5 evidence pair (the
#      doubled meetings pf_trust_checks.py measured 2026-08-01 on the
#      2026-06-30..07-13 overlap). Explicit entries ONLY: a false-positive
#      merge of two real distinct tracks corrupts more than a missed
#      alias. Targets are never themselves keys (the map is idempotent).
TRACK_ALIASES = {
    # --- group 1: pre-existing collector map (pinned) ---
    "rosehill": "rosehillgardens",
    "rosehillgardens": "rosehillgardens",
    "ascot": "ascotwa",
    "ascotwa": "ascotwa",
    "randwick": "randwick",
    # K5 pair: PF 'Randwick' vs old 'Royal Randwick' (1 day)
    "royalrandwick": "randwick",
    "kensington": "kensington",
    "flemington": "flemington",
    "morphettville": "morphettville",
    "morphettvilleparks": "morphettville",
    "doomben": "doomben",
    "eaglefarm": "eaglefarm",
    "caulfield": "caulfield",
    "mrc": "caulfield",
    "mooneevalley": "mooneevalley",
    "sandownhillside": "sandownhillside",
    "sandownlakeside": "sandownlakeside",
    "sandown": "sandownhillside",
    "bendigo": "bendigo",
    "ballarat": "ballarat",
    "geelong": "geelong",
    "pakenham": "pakenham",
    "cranbourne": "cranbourne",
    # --- group 2: F-TRACK-ALIAS, K5-measured pairs (2026-08-01) ---
    # K5 pair: PF 'Bairnsdale' vs old 'bet365 Bairnsdale' (1 day)
    "bet365bairnsdale": "bairnsdale",
    # K5 pair: PF 'Ballarat Synthetic' vs old 'Sportsbet-Ballarat Synthetic'
    # (4 days). NOTE: K5 also listed 'Ballarat' vs 'Sportsbet-Ballarat
    # Synthetic' for 2026-07-12 — a greedy-matching artifact: that day's old
    # rows have field sizes identical race-by-race to PF's SYNTHETIC meeting
    # (the turf meeting was never resulted), so the map below is correct and
    # 'ballarat' must NOT alias to 'ballaratsynthetic' (distinct tracks).
    "sportsbetballaratsynthetic": "ballaratsynthetic",
    # K5 pair: PF 'Beaudesert' vs old 'Aquis Beaudesert' (1 day)
    "aquisbeaudesert": "beaudesert",
    # K5 pair: PF 'Belmont Park' vs old 'Belmont' (4 days)
    "belmont": "belmontpark",
    # K5 pair: PF 'Canterbury' vs old 'Canterbury Park' (1 day)
    "canterburypark": "canterbury",
    # K5 pair: PF 'Devonport Synthetic' vs old 'Devonport Tapeta Synthetic' (1 day)
    "devonporttapetasynthetic": "devonportsynthetic",
    # K5 pair: PF 'Fannie Bay' vs old 'Darwin' (3 days)
    "darwin": "fanniebay",
    # K5 pair: PF 'Geelong' vs old 'Ladbrokes Geelong' (1 day)
    "ladbrokesgeelong": "geelong",
    # K5 pair: PF 'Gold Coast' vs old 'Aquis Park Gold Coast' (2 days).
    # 'Aquis Park Gold Coast Poly' is a DISTINCT synthetic track — no entry.
    "aquisparkgoldcoast": "goldcoast",
    # K5 pair: PF 'Hamilton' vs old 'bet365 Hamilton' (1 day)
    "bet365hamilton": "hamilton",
    # K5 pair: PF 'Mt Gambier' vs old 'Mount Gambier' (1 day)
    "mountgambier": "mtgambier",
    # K5 pair: PF 'Mt Isa' vs old 'Sportsbet Mount Isa' (1 day)
    "sportsbetmountisa": "mtisa",
    # K5 pair: PF 'Murray Bridge GH' vs old 'Thomas Farms RC Murray Bridge' (3 days)
    "thomasfarmsrcmurraybridge": "murraybridgegh",
    # K5 pair: PF 'Pakenham Synthetic' vs old 'Southside Pakenham Synthetic' (1 day)
    "southsidepakenhamsynthetic": "pakenhamsynthetic",
    # K5 pair: PF 'Pinjarra' vs old 'Pinjarra Park' (1 day).
    # 'Pinjarra Scarpside' is a DISTINCT track — no entry.
    "pinjarrapark": "pinjarra",
    # K5 pair: PF 'Pioneer Park' vs old 'Ladbrokes Pioneer Park' (2 days)
    "ladbrokespioneerpark": "pioneerpark",
    # K5 pair: PF 'Randwick-Kensington' vs old 'Kensington' (1 day)
    "randwickkensington": "kensington",
    # K5 pair: PF 'Sandown-Lakeside' vs old 'Sportsbet Sandown Lakeside' (2 days)
    "sportsbetsandownlakeside": "sandownlakeside",
    # K5 pair: PF 'Wangaratta' vs old 'Sportsbet-Wangaratta' (1 day)
    "sportsbetwangaratta": "wangaratta",
}


def canonical_track_key(value):
    """Canonical track key for DEDUP/MATCHING ONLY — the stored track
    column keeps the source spelling. Normalise (lowercase, letters+digits
    only) then apply the explicit TRACK_ALIASES map; unknown tracks pass
    through normalised. Idempotent: alias targets are never alias keys."""
    text = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    return TRACK_ALIASES.get(text, text)


def parse_class_level(rc):
    if not rc:
        return 1
    rc = str(rc).upper()
    if any(x in rc for x in ("GROUP 1", "GROUP1", " G1")):
        return 10
    if any(x in rc for x in ("GROUP 2", "GROUP2", " G2")):
        return 9
    if any(x in rc for x in ("GROUP 3", "GROUP3", " G3")):
        return 8
    if "LISTED" in rc:
        return 7
    bm = re.search(r"BM\s*(\d+)", rc)
    if bm:
        n = int(bm.group(1))
        if n >= 88:
            return 6
        if n >= 70:
            return 5
        if n >= 58:
            return 4
        if n >= 50:
            return 3
        return 2
    if "MAIDEN" in rc:
        return 2
    return 1


def safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_distance(race_obj):
    for k in ("distance", "raceDistance", "distanceM", "length"):
        v = race_obj.get(k)
        if v:
            m = re.search(r"(\d+)", str(v))
            if m:
                return int(m.group(1))
    return None


def race_meta(race_obj):
    """(race_name, race_class) from a meeting-detail race object, tolerant of
    naming differences; unknown keys are reported once in dry-run output."""
    name = race_obj.get("name") or race_obj.get("raceName")
    rclass = (race_obj.get("raceClass") or race_obj.get("class")
              or race_obj.get("restrictions") or "")
    return name, str(rclass or "")


def normalize_finish_position(v, field_size=None):
    pos = safe_int(v)
    if pos is None or pos <= 0 or pos >= 100:
        return None
    if field_size and pos > field_size:
        return None
    return pos


# ------------------------------------------------------- meeting filtering

def aus_race_meetings(meetings):
    """AUS non-barrier-trial meetings from a meetingslist payload — the
    trials exclusion at the meeting level (results importers exclude trials;
    the trials importer includes ONLY trials, so the partition is total)."""
    return [m for m in meetings or []
            if (m.get("track") or {}).get("country") == "AUS"
            and not m.get("isBarrierTrial")]


def aus_trial_meetings(meetings):
    """AUS barrier-trial meetings from a meetingslist payload — the ONLY
    meetings the trials importer handles (PF marks them isBarrierTrial)."""
    return [m for m in meetings or []
            if (m.get("track") or {}).get("country") == "AUS"
            and m.get("isBarrierTrial")]


def assert_trial_partition(meetings):
    """The results/trials partition over AUS meetings: each AUS meeting is
    handled by EXACTLY ONE importer (results excludes trials; the trials
    script includes only trials). A meeting in both — or an AUS meeting in
    neither — is a bug; fail loudly rather than double- or never-import."""
    aus = [m for m in meetings or []
           if (m.get("track") or {}).get("country") == "AUS"]
    results_ids = {id(m) for m in aus_race_meetings(aus)}
    trial_ids = {id(m) for m in aus_trial_meetings(aus)}
    both = results_ids & trial_ids
    neither = {id(m) for m in aus} - results_ids - trial_ids
    if both or neither:
        raise AssertionError(
            f"results/trials partition broken: {len(both)} meeting(s) in both, "
            f"{len(neither)} AUS meeting(s) in neither")
    return aus


def is_metro_track(name):
    n = str(name or "").lower()
    return any(t in n or n in t for t in METRO_TRACKS)


# ------------------------------------------------------------- DB helpers

def load_existing_keys(cur, dates):
    """The dedup set: (race_date::text, canonical_track_key(track),
    race_number) for every row already in race_results_history on the given
    ISO dates. The raw track is projected and canonicalised here in Python —
    the canonical map is explicit and never lives in SQL."""
    cur.execute("""
        SELECT race_date::text, track, race_number
        FROM race_results_history WHERE race_date = ANY(%s)
    """, (list(dates),))
    return {(r[0], canonical_track_key(r[1]), r[2]) for r in cur.fetchall()}


def row_race_key(row):
    """The dedup key of one mapped 21-column row: (race_date,
    canonical_track_key(track), race_number). The STORED track column keeps
    the source spelling — only the key canonicalises."""
    return (row[4], canonical_track_key(row[3]), row[18])


def load_existing_runner_keys(cur, dates):
    """Runner-granularity dedup set: race key + norm_name(horse_name) for
    every row already stored on the given ISO dates. norm_name is the same
    rule the ID bridge trusts to identify one horse across sources, so a
    re-fetch cannot double-insert a runner over a spelling drift."""
    cur.execute("""
        SELECT race_date::text, track, race_number, horse_name
        FROM race_results_history WHERE race_date = ANY(%s)
    """, (list(dates),))
    return {(r[0], canonical_track_key(r[1]), r[2], norm_name(r[3]))
            for r in cur.fetchall()}


def row_runner_key(row):
    """Runner-granularity dedup key of one mapped 21-column row."""
    return row_race_key(row) + (norm_name(row[1]),)


# The SELECT projects the ORIGINAL-case horse_name so norm_name can strip an
# uppercase country suffix like "(NZ)"; projecting LOWER(horse_name) here
# hands norm_name a lowercase "(nz)" it cannot strip, corrupting the key
# ("oceandeepnz" vs the card-side "oceandeep") so every country-suffixed
# horse forks a fresh id instead of bridging. DISTINCT ON/ORDER BY still
# lower for grouping; only the projection keeps case.
BRIDGE_SQL = """
    SELECT DISTINCT ON (LOWER(horse_name)) horse_name, horse_id
    FROM race_results_history ORDER BY LOWER(horse_name), race_date DESC
"""


def load_bridge(cur):
    """normalised name -> existing horse_id; most recent prior row wins."""
    cur.execute(BRIDGE_SQL)
    return {norm_name(name): hid for name, hid in cur.fetchall()}


def resolve_horse_id(bridge, runner_name, runner_id):
    """The bridge rule: match an existing horse by normalised name; only a
    genuinely unknown horse gets a 'pf<runnerId>' id."""
    return bridge.get(norm_name(runner_name)) or f"pf{runner_id}"


def archive_payloads(cur, raw_payloads):
    """Archive raw payloads into pf_raw_payloads. Call this BEFORE the row
    insert, in the same transaction — never skip it on a committing run."""
    from psycopg2.extras import Json
    for raw in raw_payloads:
        cur.execute(
            "INSERT INTO pf_raw_payloads (kind, ref_date, meeting_id, payload) VALUES (%s,%s,%s,%s)",
            (raw["kind"], raw["ref_date"], raw["meeting_id"], Json(raw["payload"])))


# ------------------------------------------------------------- row mapping

def build_rows(day, bridge, unknown_keys):
    """Map one collected day to RRH rows. bridge: norm_name -> horse_id."""
    rows, races_seen = [], []
    for m in day["meetings"]:
        for block in m["results"]:
            race_date = str(block.get("meetingDate") or day["date"])[:10]
            track = block.get("track") or m["track"]
            for rr in block.get("raceResults") or []:
                rnum = safe_int(rr.get("raceNumber"))
                if not rnum:
                    continue
                detail = m["detail_races"].get(rnum, {})
                race_name, race_class = race_meta(detail)
                if not race_name and detail:
                    unknown_keys.update(k for k in detail.keys() if k != "runners")
                distance_m = parse_distance(detail) or parse_distance(rr)
                going = rr.get("trackConditionLabel")
                runners = [r for r in (rr.get("runners") or [])
                           if normalize_finish_position(r.get("position")) is not None]
                field_size = len(runners)
                opponents = [{"horse_id": None, "horse_name": r.get("runner"),
                              "position": normalize_finish_position(r.get("position"), field_size),
                              "margin": safe_float(r.get("margin"))} for r in runners]
                # same shape as row_race_key/load_existing_keys — a plain
                # lower() here never matches the canonicalised dedup set for
                # multi-word tracks, so "already existed" counts were wrong
                races_seen.append((race_date, canonical_track_key(track), rnum))
                for r in runners:
                    nk = norm_name(r.get("runner"))
                    horse_id = bridge.get(nk) or f"pf{r.get('runnerId')}"
                    my_opponents = [o for o in opponents if norm_name(o["horse_name"]) != nk]
                    rows.append((
                        horse_id,
                        r.get("runner", ""),
                        f"pf{m['meeting_id']}_{rnum}",
                        track,
                        race_date,
                        distance_m,
                        race_class,
                        parse_class_level(race_class),
                        going,
                        normalize_finish_position(r.get("position"), field_size),
                        safe_float(r.get("margin")),
                        safe_float(r.get("weight")),
                        r.get("jockey"),
                        f"pf{r.get('jockeyId')}" if r.get("jockeyId") else None,
                        safe_int(r.get("barrier")),
                        safe_float(r.get("price")),
                        field_size,
                        race_name,
                        rnum,
                        m["last10"].get(str(r.get("runnerId") or "")),
                        json.dumps(my_opponents),
                    ))
    return rows, races_seen


def count_excluded_runners(results_payload):
    """Scratched/no-position runners in one results payload (reporting twin
    of the build_rows exclusion filter)."""
    excluded = 0
    for block in results_payload or []:
        for rr in block.get("raceResults") or []:
            if not safe_int(rr.get("raceNumber")):
                continue
            for r in rr.get("runners") or []:
                if normalize_finish_position(r.get("position")) is None:
                    excluded += 1
    return excluded
