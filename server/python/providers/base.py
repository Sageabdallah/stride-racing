"""Provider-agnostic contract for racing data ingestion.

The internal schema is frozen to the shape the downstream pipeline already
consumes (run_tips_pipeline.py, stride_build.py, the pipeline.ts local-cache
reader, racecards/racecard_<DATE>.json on disk). Adapters for new providers
must normalize INTO this shape — downstream code never sees provider-specific
fields, so swapping providers never touches the pipeline.

Internal schema
---------------
Meet        {"meet_id": str, "course": str}
MeetCard    {"date": "YYYY-MM-DD", "meet_id": str, "course": str, "races": [Race]}
Race        {"race_number": int|str, "race_name": str, "class": str,
             "distance": str, "going": str, "off_time": str,
             "course": str, "date": "YYYY-MM-DD",
             "runners": [Runner], ...extra provider fields tolerated}
Runner      {"horse": str, "number": int, "draw": int, "jockey": str,
             "trainer": str, "weight": str, "form": str, "scratched": bool,
             "odds": [{"bookmaker": str, "win_odds": float}], ...}
RaceResult  {"course": str, "track": str, "race_number": int,
             "race_name": str, "distance": str,
             "runners": [{"position"|"finishing_position": ...,
                          "sp"|"starting_price"|...: ...,
                          "horse_name"|"horse"|"name": str, ...}]}
Listing     {"course": str, "state": str|None, "location": str|None,
             "surface": str|None, "tab_meeting": bool|None, "is_trial": bool}
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class RacingDataProvider(ABC):
    """One implementation per upstream source. All methods return the
    internal schema documented above, never the provider's raw shape."""

    name: str = "abstract"

    @abstractmethod
    def has_credentials(self) -> bool:
        """True when the environment carries what this provider needs to run."""

    @abstractmethod
    def fetch_meets(self, date_str: str) -> List[Dict]:
        """All meets for a date as [Meet]. Empty list on upstream failure —
        callers treat that as 'no data', mirroring historical behaviour."""

    def discovery_stats(self) -> Optional[Dict]:
        """What the last fetch_meets call saw before its own filtering, as
        {"raw": int|None, "country_kept": int, "country_missing": int,
         "error": str|None, "listed": [Listing]}.

        This exists because an empty return from fetch_meets is ambiguous, and
        the ambiguity is expensive: "the upstream is down", "there is no racing
        anywhere today" and "there is racing but none of it is ours" are three
        different situations that a caller cannot tell apart from an empty list.
        Only the adapter knows how many meetings arrived before it filtered, so
        only the adapter can resolve it.

        Not abstract, and None by default: an adapter that does not report these
        counts keeps working unchanged, and callers must then treat an empty
        meets list as indistinguishable from an outage — i.e. fail loudly. The
        conservative reading is the default precisely because the alternative
        failure (a dead feed read as a quiet day) is the silent one."""
        return None

    @abstractmethod
    def fetch_detailed_races(self, meet_id: str, date_str: str, course: str) -> List[Dict]:
        """Full race details (declared runners, barriers, odds) for one meet
        as [Race], with course/date guaranteed populated on every race."""

    @abstractmethod
    def fetch_results(self, date_str: str) -> List[Dict]:
        """Completed races for a date as [RaceResult]. Only races with an
        official/final status and at least one placed runner are returned."""
