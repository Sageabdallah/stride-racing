"""Day artifacts: tips, racecards, consensus and market signals.

The Fargate chain relays its file artifacts through S3 (infra/jobs/handler.py,
_sync_up), under s3://$STRIDE_EVIDENCE_BUCKET/artifacts/<repo-relative path>.
Those are the fresh copies. The same paths in a local checkout are the stale
copies the old TypeScript chat kept reading after the Mac's scheduler was
gated off (CHAT_LAMBDA_ARCHITECTURE_AUDIT.md §1.1), so the order here is S3
first when a bucket is configured, local checkout second, and a clear miss
when neither has the file. The caller always learns which one answered.

boto3 is imported inside _s3() so this module imports without it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ._paths import REPO_ROOT
from . import config

ARTIFACTS_PREFIX = "artifacts"  # infra/jobs/handler.py ARTIFACTS_PREFIX

# Repo-relative directories the relay writes, by artifact kind. These are the
# `rel_dir` arguments handler.py passes to _sync_up, verbatim.
TIPS_DIR = "racecards"
RACECARDS_DIR = "server/python/racecards"
INTELLIGENCE_DIR = "server/python/intelligence"


class ArtifactMissing(LookupError):
    """Neither the relay nor the local checkout has the file."""


class ArtifactUnavailable(RuntimeError):
    """The relay is configured but could not be read (not a missing key)."""


def tips_path(date_str: str) -> str:
    return f"{TIPS_DIR}/tips_{date_str}.json"


def racecard_path(date_str: str) -> str:
    return f"{RACECARDS_DIR}/racecard_{date_str}.json"


def consensus_path(date_str: str) -> str:
    return f"{INTELLIGENCE_DIR}/consensus_{date_str}.json"


def market_signals_path(date_str: str) -> str:
    return f"{INTELLIGENCE_DIR}/market_signals_{date_str}.json"


def blackbook_candidates_path(date_str: str) -> str:
    return f"{TIPS_DIR}/blackbook_candidates_{date_str}.json"


@dataclass
class ArtifactStore:
    """Reads JSON artifacts by repo-relative path.

    `bucket` None means local-only (CI, a developer with no AWS credential).
    `local_root` is the checkout root; on Lambda it is the task root, which
    has no artifacts, so every read there goes to S3 or misses.
    """

    bucket: Optional[str] = None
    prefix: str = ARTIFACTS_PREFIX
    local_root: str = REPO_ROOT
    cache_ttl_seconds: int = 300
    _cache: Dict[str, Tuple[float, Any, str]] = field(default_factory=dict, repr=False)
    _client: Any = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "ArtifactStore":
        return cls(bucket=config.artifact_bucket())

    # -- transport ---------------------------------------------------------

    def _s3(self):
        if self._client is None:
            import boto3  # lazy: not installed in CI, not needed locally
            self._client = boto3.client(
                "s3", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
        return self._client

    def _key(self, rel_path: str) -> str:
        return f"{self.prefix}/{rel_path}"

    def _read_s3(self, rel_path: str) -> Optional[str]:
        try:
            obj = self._s3().get_object(Bucket=self.bucket, Key=self._key(rel_path))
            return obj["Body"].read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 - classified below
            name = type(e).__name__
            text = str(e)
            if name in ("NoSuchKey", "NoSuchBucket") or "NoSuchKey" in text \
                    or "Not Found" in text or "(404)" in text:
                return None
            raise ArtifactUnavailable(
                f"artifact relay read failed for {rel_path}: {name}: {e}") from e

    def _read_local(self, rel_path: str) -> Optional[str]:
        path = os.path.join(self.local_root, rel_path)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    # -- public -----------------------------------------------------------

    def get_json(self, rel_path: str) -> Tuple[Any, str]:
        """Return (parsed_json, source). Source is 's3:<key>' or 'local:<path>'.

        Raises ArtifactMissing when neither location has it, and
        ArtifactUnavailable when the relay is configured but unreadable, so a
        dead bucket credential never reads as a quiet day.
        """
        now = time.time()
        hit = self._cache.get(rel_path)
        if hit and now - hit[0] < self.cache_ttl_seconds:
            return hit[1], hit[2]

        text: Optional[str] = None
        source = ""
        if self.bucket:
            text = self._read_s3(rel_path)
            if text is not None:
                source = f"s3:{self._key(rel_path)}"
        if text is None:
            text = self._read_local(rel_path)
            if text is not None:
                source = f"local:{rel_path}"
        if text is None:
            raise ArtifactMissing(rel_path)

        data = json.loads(text)
        self._cache[rel_path] = (now, data, source)
        return data, source

    def describe(self) -> str:
        if self.bucket:
            return f"s3://{self.bucket}/{self.prefix} then {self.local_root}"
        return f"{self.local_root} (no artifact bucket configured)"
