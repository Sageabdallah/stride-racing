"""Punting Form for the chat: pf_client behind a cache and the window wall.

Never raw REST. pf_client owns the envelope errors, PFAuthError on 401/403,
the retry set and the 0.4-second pacing; this module adds only what an
interactive caller needs on top:

* A short TTL cache. One question often needs the same meetings list twice
  (once to find a meeting id, once to answer), and a warm Lambda container
  answers several questions about the same day.
* The subscription window. Starter serves about 31 days back; a date beyond
  it is answered as a fact ("not served on this plan") without spending the
  call that would return HTTP 400.
* One exception type for the tools to catch. A rejected key is not something
  a chat turn can fix; it is reported as such and the turn continues.

pf_client is a flat module beside this package (chat/_paths.py makes it
importable). It reads PUNTINGFORM_API_KEY at call time, not import time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from . import config


class PuntingFormUnavailable(RuntimeError):
    """The key was rejected, the key is unset, or the provider failed."""


class PuntingFormOutsideWindow(LookupError):
    """The requested date is older than the plan serves. Not an error to
    retry: the honest answer is that the data is not available here."""


def outside_window(iso_date: str, today: Optional[str] = None,
                   window_days: int = config.PUNTINGFORM_WINDOW_DAYS) -> bool:
    d = datetime.strptime(iso_date, "%Y-%m-%d").date()
    t = datetime.strptime(today, "%Y-%m-%d").date() if today else date.today()
    return d < t - timedelta(days=window_days)


@dataclass
class PuntingForm:
    """Thin, cached, window-aware facade over pf_client's functions.

    `client` is the module (or any object) exposing meetings_for_date,
    results_for_meeting, meeting_detail, scratchings, conditions,
    speedmaps_for_meeting, ratings_for_meeting and strike_rates. Tests pass a
    fake; production passes pf_client.
    """

    client: Any = None
    cache_ttl_seconds: int = 300
    today: Optional[str] = None
    _cache: Dict[Tuple[str, Tuple[Any, ...]], Tuple[float, Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if self.client is None:
            import pf_client  # flat module; see chat/_paths.py
            self.client = pf_client

    def _errors(self):
        """(PFError, PFAuthError) from the real client, or generic fallbacks
        when a fake has none, so `except` clauses stay honest either way."""
        pf_error = getattr(self.client, "PFError", None)
        pf_auth = getattr(self.client, "PFAuthError", None)
        if pf_error is None or pf_auth is None:
            try:
                import pf_client
                pf_error = pf_error or pf_client.PFError
                pf_auth = pf_auth or pf_client.PFAuthError
            except Exception:  # noqa: BLE001
                pf_error = pf_error or RuntimeError
                pf_auth = pf_auth or RuntimeError
        return pf_error, pf_auth

    def _call(self, name: str, *args: Any) -> Any:
        key = (name, args)
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self.cache_ttl_seconds:
            return hit[1]
        fn: Callable[..., Any] = getattr(self.client, name)
        pf_error, pf_auth = self._errors()
        try:
            value = fn(*args)
        except pf_auth as e:
            raise PuntingFormUnavailable(f"Punting Form rejected the key: {e}") from e
        except pf_error as e:
            text = str(e)
            if "400" in text:
                # The sliding wall, measured live rather than assumed.
                raise PuntingFormOutsideWindow(text) from e
            raise PuntingFormUnavailable(f"Punting Form call failed: {e}") from e
        self._cache[key] = (now, value)
        return value

    def assert_in_window(self, iso_date: str) -> None:
        if outside_window(iso_date, self.today or config.today_sydney()):
            raise PuntingFormOutsideWindow(
                f"{iso_date} is older than the ~{config.PUNTINGFORM_WINDOW_DAYS} days "
                f"the Punting Form Starter plan serves")

    # -- the endpoints, in pf_client's own names ---------------------------

    def meetings_for_date(self, iso_date: str):
        self.assert_in_window(iso_date)
        return self._call("meetings_for_date", iso_date) or []

    def results_for_meeting(self, meeting_id):
        return self._call("results_for_meeting", meeting_id) or []

    def meeting_detail(self, meeting_id):
        return self._call("meeting_detail", meeting_id)

    def scratchings(self, jurisdiction=None):
        return self._call("scratchings", jurisdiction) or []

    def conditions(self, jurisdiction=None):
        return self._call("conditions", jurisdiction) or []

    def speedmaps_for_meeting(self, meeting_id, race_no=0):
        return self._call("speedmaps_for_meeting", meeting_id, race_no) or []

    def ratings_for_meeting(self, meeting_id):
        return self._call("ratings_for_meeting", meeting_id) or []

    def strike_rates(self, entity_type=None, jurisdiction=None):
        return self._call("strike_rates", entity_type, jurisdiction) or []

    # -- a join the model should not have to write ------------------------

    def find_meeting(self, iso_date: str, track: str) -> Optional[Dict[str, Any]]:
        """The meeting on `iso_date` whose track matches `track`, or None."""
        from identity_normalization import normalize_track_key
        wanted = normalize_track_key(track)
        if not wanted:
            return None
        for m in self.meetings_for_date(iso_date):
            name = ((m or {}).get("track") or {}).get("name") if isinstance(m.get("track"), dict) \
                else (m or {}).get("track")
            got = normalize_track_key(name)
            if got == wanted or wanted in got or got in wanted:
                return m
        return None
