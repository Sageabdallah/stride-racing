"""TheRacingAPI.com adapter.

The internal schema (see base.py) was frozen from this provider's shape, so
normalization here is mostly filling fallbacks (meet_id/id, course/track) and
guaranteeing course/date on every race. Future adapters do real field mapping.
"""

import base64
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import RacingDataProvider

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

_env_path = Path(__file__).resolve().parents[3] / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

_RESULT_STATUSES = {"results", "completed", "final", "official"}
_POSITION_KEYS = ("position", "finishing_position", "finish_position")


class TheRacingAPIProvider(RacingDataProvider):
    name = "theracingapi"

    # Throttle between per-race detail calls; tests zero this out.
    detail_delay = 0.1

    def __init__(self):
        self.base_url = os.environ.get("RACING_API_BASE", "https://api.theracingapi.com")
        self.username = os.environ.get("RACING_API_USERNAME", "")
        self.password = os.environ.get("RACING_API_PASSWORD", "")
        self._session: Optional[requests.Session] = None

    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
            self._session.mount("https://", HTTPAdapter(max_retries=retries))
            creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            self._session.headers.update({
                "Authorization": f"Basic {creds}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            self._session.verify = False
        return self._session

    def _api_get(self, endpoint: str, params: Optional[Dict] = None):
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self._get_session().get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  [{self.name}] Request failed {endpoint}: {e}", file=sys.stderr)
            return None

    @staticmethod
    def _unwrap(payload, key: str) -> List[Dict]:
        if isinstance(payload, dict):
            payload = payload.get(key, payload)
        return payload if isinstance(payload, list) else []

    def fetch_meets(self, date_str: str) -> List[Dict]:
        meets = []
        for meet in self._unwrap(self._api_get("/v1/australia/meets", {"date": date_str}), "meets"):
            meet_id = meet.get("meet_id") or meet.get("id")
            course = meet.get("course") or meet.get("track") or ""
            if meet_id and course:
                meets.append({"meet_id": str(meet_id), "course": course})
        return meets

    def fetch_detailed_races(self, meet_id: str, date_str: str, course: str) -> List[Dict]:
        races = self._unwrap(self._api_get(f"/v1/australia/meets/{meet_id}/races"), "races")

        detailed = []
        for race in races:
            race_num = race.get("race_number") or race.get("number")
            if race_num is None:
                continue

            details = self._api_get(f"/v1/australia/meets/{meet_id}/races/{int(race_num)}")
            if not details or not isinstance(details, dict):
                continue

            details["course"] = details.get("course") or course
            details["date"] = details.get("date") or date_str
            detailed.append(details)
            if self.detail_delay:
                time.sleep(self.detail_delay)

        return detailed

    def fetch_results(self, date_str: str) -> List[Dict]:
        results = []
        for meet in self._unwrap(self._api_get("/v1/australia/meets", {"date": date_str}), "meets"):
            meet_id = meet.get("meet_id") or meet.get("id")
            track_name = meet.get("track") or meet.get("course") or meet.get("name", "Unknown")
            if not meet_id:
                continue

            races = self._unwrap(self._api_get(f"/v1/australia/meets/{meet_id}/races"), "races")
            for race in races:
                status = str(race.get("race_status", "")).lower()
                if status not in _RESULT_STATUSES:
                    continue
                runners = race.get("runners", [])
                if not any(any(r.get(k) for k in _POSITION_KEYS) for r in runners):
                    continue
                results.append({
                    "course": track_name,
                    "track": track_name,
                    "race_number": race.get("race_number") or race.get("number"),
                    "race_name": race.get("race_name") or race.get("name", ""),
                    "distance": race.get("distance", ""),
                    "runners": runners,
                })
        return results
