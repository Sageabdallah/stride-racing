"""Punting Form adapter (Starter tier) — the racecard serve path.

Discovery is /form/meetingslist and the card + per-runner last-10 form is
/form/meeting, both through pf_client (envelope checking, retries, pacing);
endpoint paths are only the ones confirmed live in PUNTINGFORM_MIGRATION.md
Phase A. Output is normalized into the frozen internal schema (base.py),
key-for-key compatible with the cards the retired provider wrote: fields
Punting Form cannot supply are explicit nulls, each logged once per run —
never fabricated.

Serve-time horse-ID bridge: features join race_results_history by horse_id
(hundreds of SQL sites), so every PF runner must resolve to the horse's
existing id. The rule is imported from pf_backfill_results (norm_name +
load_horse_id_bridge) so the results side and this card side cannot drift;
only genuinely new horses get a 'pf<runnerId>' id. A card with unbridged ids
silently forks identities, which is worse than no card — so a missing
DATABASE_URL aborts the run instead of minting fresh ids for known horses.
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .base import RacingDataProvider

# pf_client and pf_backfill_results live one level above the providers
# package; callers usually run with server/python on sys.path, but not always.
_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import pf_client
from pf_backfill_results import (
    aus_race_meetings,
    load_horse_id_bridge,
    norm_name,
    normalize_finish_position,
    parse_distance,
    race_meta,
    safe_int,
)


def _person_name(obj) -> Optional[str]:
    """Jockey/trainer arrive as objects in the meeting payload; tolerate a
    plain string too (the results payload uses one)."""
    if isinstance(obj, dict):
        for key in ("fullName", "name", "displayName"):
            if obj.get(key):
                return str(obj[key])
        return None
    return str(obj) if obj else None


class PuntingFormProvider(RacingDataProvider):
    name = "puntingform"

    def __init__(self):
        self._meet_info: Dict[str, Dict] = {}
        self._bridge_map: Optional[Dict[str, str]] = None
        self._scratch_map: Optional[Dict] = None
        self._noted = set()
        self._last_discovery: Optional[Dict] = None

    def has_credentials(self) -> bool:
        return bool((os.environ.get("PUNTINGFORM_API_KEY") or "").strip())

    # ------------------------------------------------------------------
    # once-per-run logging — nulls must be visible, never silent
    # ------------------------------------------------------------------

    def _note_once(self, key: str, message: str):
        if key not in self._noted:
            self._noted.add(key)
            print(f"  [{self.name}] {message}", file=sys.stderr)

    def _absent(self, field: str):
        self._note_once(f"field:{field}", f"field {field} unavailable from puntingform")
        return None

    def _take(self, obj: Dict, keys, field: str):
        for key in keys:
            value = obj.get(key)
            if value not in (None, ""):
                return value
        return self._absent(field)

    def _pf_id(self, raw, field: str):
        # Same 'pf<id>' convention pf_backfill_results uses for jockey ids.
        return f"pf{raw}" if raw not in (None, "") else self._absent(field)

    # ------------------------------------------------------------------
    # horse-ID bridge
    # ------------------------------------------------------------------

    def _open_cursor(self, db_url):
        import psycopg2
        conn = psycopg2.connect(db_url)
        return conn, conn.cursor()

    def _bridge(self) -> Dict[str, str]:
        if self._bridge_map is None:
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                print(
                    f"  [{self.name}] DATABASE_URL is not set — cannot bridge PF runner ids "
                    "to existing horse_ids, and a card with forked identities would poison "
                    "every horse_id join downstream; refusing to build the racecard",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            conn, cur = self._open_cursor(db_url)
            try:
                self._bridge_map = load_horse_id_bridge(cur)
            finally:
                conn.close()
            print(
                f"  [{self.name}] horse-id bridge loaded: {len(self._bridge_map)} known horses",
                file=sys.stderr,
            )
        return self._bridge_map

    # ------------------------------------------------------------------
    # contract methods
    # ------------------------------------------------------------------

    def fetch_meets(self, date_str: str) -> List[Dict]:
        # Reset first so discovery_stats() can never describe a previous call.
        self._last_discovery = {"raw": None, "country_kept": 0,
                                "country_missing": 0, "error": None, "listed": []}
        try:
            meetings = pf_client.meetings_for_date(date_str)
        except pf_client.PFError as e:
            # Contract: empty list on upstream failure. download_racecards
            # then exits 1 on the empty day, so the failure stays loud.
            # raw stays None, which is what marks this as "we never found out"
            # rather than "we asked and the calendar was empty".
            self._last_discovery["error"] = str(e)
            print(f"  [{self.name}] meetings fetch failed: {e}", file=sys.stderr)
            return []

        meetings = meetings or []
        self._last_discovery["raw"] = len(meetings)

        meets = []
        for meeting in meetings:
            track = meeting.get("track") or {}
            country = track.get("country")
            if country is None:
                # Counted, not just skipped. If PF ever renames or drops this
                # field, every meeting silently fails the filter below and the
                # day reads as "no racing in Australia" — which is never true.
                # The count is what lets the caller say so out loud.
                self._last_discovery["country_missing"] += 1
            # AUS-only mirrors the retired /v1/australia endpoint; barrier-trial
            # meetings stay in so races get tagged, not dropped (the
            # TARGET_TRACKS filter downstream is unchanged).
            if country != "AUS":
                continue
            self._last_discovery["country_kept"] += 1
            meeting_id = meeting.get("meetingId")
            course = track.get("name") or ""
            if meeting_id is None or not course:
                continue
            self._last_discovery["listed"].append({
                "course": course,
                "state": track.get("state"),
                "location": track.get("location"),
                "surface": track.get("surface"),
                "tab_meeting": meeting.get("tabMeeting"),
                "is_trial": bool(meeting.get("isBarrierTrial")),
            })
            self._meet_info[str(meeting_id)] = meeting
            meets.append({"meet_id": str(meeting_id), "course": course})
        return meets

    def discovery_stats(self) -> Optional[Dict]:
        return self._last_discovery

    def _scratchings(self) -> Dict:
        """(meetingId, raceNo, tabNo) -> deduction for every upcoming
        scratching, fetched once per run. The feed is auxiliary: a failure
        means the card goes out without LATE scratchings (declared-runner
        flags from meeting_detail still apply), which beats no card at all —
        so unlike meeting_detail, an error here is logged, not fatal."""
        if self._scratch_map is None:
            try:
                items = pf_client.scratchings()
            except pf_client.PFError as e:
                self._note_once(
                    "scratchings",
                    f"scratchings feed unavailable — cards proceed without late scratchings: {e}")
                items = []
            self._scratch_map = {}
            for s in items or []:
                key = (str(s.get("meetingId")), safe_int(s.get("raceNo")),
                       safe_int(s.get("tabNo")))
                if None not in key[1:]:
                    self._scratch_map[key] = s.get("deduction")
        return self._scratch_map

    def fetch_detailed_races(self, meet_id: str, date_str: str, course: str) -> List[Dict]:
        bridge = self._bridge()
        scratch_map = self._scratchings()

        info = self._meet_info.get(str(meet_id))
        if info is None:
            self.fetch_meets(date_str)
            info = self._meet_info.get(str(meet_id)) or {}

        # A PFError here aborts the whole run: downstream reads the card file
        # blind, so a card silently missing a meeting is worse than no card.
        detail = pf_client.meeting_detail(meet_id) or {}
        track = detail.get("track") or info.get("track") or {}
        course_name = course or track.get("name") or ""

        races = []
        for race in detail.get("races") or []:
            race_number = safe_int(race.get("raceNumber") or race.get("number"))
            if race_number is None:
                continue
            race_name, race_class = race_meta(race)
            metres = parse_distance(race)
            races.append({
                "course": course_name,
                "course_id": self._pf_id(track.get("trackId"), "course_id"),
                "date": date_str,
                "distance": f"{metres}m" if metres else self._absent("distance"),
                "going": self._take(info, ("expectedCondition",), "going"),
                "is_jump_out": self._absent("is_jump_out"),
                "is_trial": bool(info.get("isBarrierTrial")),
                "meet_id": str(meet_id),
                "off_time": self._take(race, ("startTime", "raceStartTime"), "off_time"),
                "prizes": self._absent("prizes"),
                "prize_total": self._take(race, ("prizeMoney", "totalPrizeMoney"), "prize_total"),
                "class": race_class or self._absent("class"),
                "race_conditions": self._take(race, ("restrictions", "conditions"), "race_conditions"),
                "race_group": self._take(race, ("group", "raceGroup"), "race_group"),
                "race_name": race_name or "",
                "race_number": race_number,
                "race_status": self._absent("race_status"),
                "state": self._take(track, ("state",), "state"),
                "runners": [self._runner(r, bridge, scratch_map, str(meet_id), race_number)
                            for r in race.get("runners") or []],
                # Result-side fields: null on every pre-race card, as before.
                "winning_time": None,
                "winning_time_hundredths": None,
            })
        return races

    def fetch_results(self, date_str: str) -> List[Dict]:
        """Resulted races for a date, in the internal results shape the
        collector's matcher consumes: {course, track, race_number, race_name,
        distance, runners:[{horse_name, horse, position, sp}]}.

        A meetings-list failure raises (the caller's error contract turns
        that into an empty retry-later day); a single meeting failing is
        reported and skipped so one broken track cannot sink the rest of the
        day's settlement. Races where no runner has a finishing position are
        not resulted yet and are omitted."""
        meetings = aus_race_meetings(pf_client.meetings_for_date(date_str))
        results: List[Dict] = []
        for meet in meetings:
            meet_id = meet.get("meetingId")
            track_name = (meet.get("track") or {}).get("name") or "Unknown"
            if not meet_id:
                continue
            try:
                blocks = pf_client.results_for_meeting(meet_id)
            except pf_client.PFError as e:
                print(f"  [puntingform] results unavailable for {track_name}: {e}",
                      file=sys.stderr)
                continue
            for block in blocks or []:
                block_track = block.get("track") or track_name
                for rr in block.get("raceResults") or []:
                    runners = rr.get("runners") or []
                    if not any(normalize_finish_position(r.get("position")) is not None
                               for r in runners):
                        continue
                    results.append({
                        "course": block_track,
                        "track": block_track,
                        "race_number": rr.get("raceNumber"),
                        "race_name": rr.get("raceName") or "",
                        "distance": rr.get("distance") or "",
                        "runners": [
                            {"horse_name": r.get("runner"),
                             "horse": r.get("runner"),
                             "position": r.get("position"),
                             "sp": r.get("price")}
                            for r in runners
                        ],
                    })
        return results

    # ------------------------------------------------------------------

    def _runner(self, r: Dict, bridge: Dict[str, str], scratch_map: Dict = None,
                meet_id: str = None, race_number: int = None) -> Dict:
        name = str(r.get("name") or r.get("runner") or "").strip()
        runner_id = r.get("runnerId")
        horse_id = bridge.get(norm_name(name))
        if not horse_id:
            # Genuinely new horse — same id rule as the results backfill.
            horse_id = f"pf{runner_id}" if runner_id not in (None, "") else None

        scratch_key = (meet_id, race_number, safe_int(r.get("tabNo")))
        late_scratched = bool(scratch_map) and scratch_key in scratch_map

        return {
            "horse_id": horse_id,
            "horse": name,
            "age": self._take(r, ("age",), "age"),
            "comment": self._take(r, ("comment",), "comment"),
            "colour": self._take(r, ("colour", "color"), "colour"),
            "dam": self._take(r, ("dam",), "dam"),
            "dam_id": self._absent("dam_id"),
            "draw": safe_int(r.get("barrier")),
            "form": (r.get("last10") or "").strip() or None,
            "jockey": _person_name(r.get("jockey")),
            "jockey_id": self._person_id(r.get("jockey"), r.get("jockeyId"), "jockey_id"),
            "jockey_claim": self._take(r, ("claim", "jockeyClaim"), "jockey_claim"),
            "margin": None,
            "number": safe_int(r.get("tabNo")),
            # Empty list, not null: consumers iterate odds, and pre-race cards
            # from the old provider carried [] until race day. Prices arrive
            # via Betfair, never from Punting Form.
            "odds": self._odds(),
            "owner": self._take(r, ("owner", "owners"), "owner"),
            "position": None,
            "prize": None,
            "rating": self._take(r, ("rating",), "rating"),
            # Scratched runners are FLAGGED, never deleted — downstream
            # field-size logic needs to see them. Declared-runner flags come
            # from meeting_detail; late scratchings from /Updates/Scratchings.
            # scratch_deduction is additive (None unless the feed priced it) —
            # the betting adjustment for future price logic.
            "scratched": bool(r.get("scratched")) or late_scratched,
            "scratch_deduction": scratch_map.get(scratch_key) if late_scratched else None,
            "sex": self._take(r, ("sex",), "sex"),
            "silk_url": self._absent("silk_url"),
            "sire": self._take(r, ("sire",), "sire"),
            "sire_id": self._absent("sire_id"),
            "sp": None,
            "stats": self._stats(r),
            "trainer": _person_name(r.get("trainer")),
            "trainer_id": self._person_id(r.get("trainer"), r.get("trainerId"), "trainer_id"),
            "weight": r.get("weight"),
        }

    def _person_id(self, obj, flat_id, field: str):
        raw = flat_id
        if raw in (None, "") and isinstance(obj, dict):
            for key in ("id", "jockeyId", "trainerId", "personId"):
                if obj.get(key) not in (None, ""):
                    raw = obj[key]
                    break
        return self._pf_id(raw, field)

    def _odds(self) -> List:
        self._note_once("field:odds", "field odds unavailable from puntingform")
        return []

    def _stats(self, r: Dict) -> Dict:
        # Always a dict, never null: consumers chain .get() off stats
        # (advanced_features distance_stats) and an explicit null would
        # defeat their defaults.
        stats = {}
        if r.get("winPct") not in (None, ""):
            stats["career_win_percent"] = r.get("winPct")
        if r.get("placePct") not in (None, ""):
            stats["career_place_percent"] = r.get("placePct")
        self._note_once(
            "field:stats",
            "stats beyond career win/place percentages unavailable from puntingform",
        )
        return stats
