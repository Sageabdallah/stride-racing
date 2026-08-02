#!/usr/bin/env python3
"""Durable evidence store for retrain-gate artifacts (gate 3).

Shadow evidence used to live only on the filesystem of whatever box ran the
pipeline. That works on the Mac and fails on AWS: Lambda and Fargate
filesystems are ephemeral, so a file written during an invocation is gone
when it ends, and a counter pointed at a local path counts zero forever.
Evidence therefore lands in S3 (STRIDE_EVIDENCE_BUCKET) whenever a bucket
is configured, with the local logs/ copy kept as a working cache so local
development needs no AWS at all.

Loudness contract, deliberately asymmetric:
  * put_evidence never raises — a logging failure must not kill a tips run.
    A day whose shadow write failed is a dirty day under the registered flip
    criteria, and the caller prints the registered failure line.
  * list_evidence_dates DOES raise when the bucket is configured but
    unreachable — a counter that silently reads zero is exactly the defect
    this module replaces.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent


class EvidenceStoreError(RuntimeError):
    """The configured durable store could not be read."""


def bucket() -> Optional[str]:
    return os.environ.get("STRIDE_EVIDENCE_BUCKET", "").strip() or None


def prefix() -> str:
    return os.environ.get("STRIDE_EVIDENCE_PREFIX", "evidence").strip().strip("/")


def s3_configured() -> bool:
    return bucket() is not None


def local_dir() -> Path:
    """Repo-root logs/ when writable; a temp dir on read-only images."""
    d = PROJECT_ROOT / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        if os.access(d, os.W_OK):
            return d
    except OSError:
        pass
    d = Path(tempfile.gettempdir()) / "stride_evidence"
    d.mkdir(parents=True, exist_ok=True)
    return d


def describe() -> str:
    b = bucket()
    return (f"s3://{b}/{prefix()} + {local_dir()}" if b else str(local_dir()))


def _s3_client():
    import boto3
    return boto3.client(
        "s3", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))


def _sns_client():
    import boto3
    return boto3.client(
        "sns", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))


def _alert(message: str) -> None:
    """Evidence-write failures must reach a human, not an ephemeral log
    nobody reads for two months — that is the defect this store exists to
    kill. Publishes to STRIDE_ALERT_TOPIC_ARN when configured; never
    raises (alerting about a failure must not create a second failure)."""
    arn = os.environ.get("STRIDE_ALERT_TOPIC_ARN", "").strip()
    if not arn:
        return
    try:
        _sns_client().publish(TopicArn=arn,
                              Subject="STRIDE evidence write FAILED",
                              Message=message)
    except Exception as e:
        print(f"[evidence] alert publish also failed: {e}", file=sys.stderr)


def _key(filename: str) -> str:
    return f"{prefix()}/{filename}"


def put_evidence(filename: str, text: str) -> Dict[str, Optional[str]]:
    """Write locally and, when a bucket is configured, to S3. Never raises."""
    out: Dict[str, Optional[str]] = {"local": None, "local_error": None,
                                     "s3": None, "s3_error": None}
    try:
        path = local_dir() / filename
        with open(path, "w") as f:
            f.write(text)
        out["local"] = str(path)
    except OSError as e:
        out["local_error"] = str(e)

    b = bucket()
    if b:
        try:
            _s3_client().put_object(
                Bucket=b, Key=_key(filename), Body=text.encode("utf-8"),
                ContentType="application/json" if filename.endswith(".json")
                else "text/plain")
            out["s3"] = f"s3://{b}/{_key(filename)}"
        except Exception as e:
            out["s3_error"] = f"{type(e).__name__}: {e}"
            _alert(f"evidence write to s3://{b}/{_key(filename)} FAILED: "
                   f"{out['s3_error']}\nlocal copy: {out['local'] or 'ALSO FAILED'}"
                   f"\nA failed shadow write restarts that flag's 5-day count "
                   f"(shadow-flip-criteria.md #2) — investigate today, not at "
                   f"flip review.")
    return out


def fetch_evidence(filename: str) -> Optional[str]:
    """Local copy first (same-process appends), then S3. None when absent.

    Raises EvidenceStoreError on a non-missing S3 failure so a caller that
    intends to append never silently clobbers the durable copy.
    """
    path = local_dir() / filename
    if path.exists():
        try:
            return path.read_text()
        except OSError:
            pass
    b = bucket()
    if not b:
        return None
    try:
        obj = _s3_client().get_object(Bucket=b, Key=_key(filename))
        return obj["Body"].read().decode("utf-8")
    except Exception as e:
        if type(e).__name__ in ("NoSuchKey", "NoSuchBucket") or \
                "NoSuchKey" in str(e) or "Not Found" in str(e) or "404" in str(e):
            return None
        raise EvidenceStoreError(
            f"evidence fetch failed for {filename}: {type(e).__name__}: {e}")


def list_evidence_dates(stem: str) -> List[str]:
    """Distinct YYYY-MM-DD dates with a `<stem>_<date>.json` evidence file.

    Union of the local cache and S3. The strict pattern is the counting
    contract: summary/pooled artifacts must not inflate a day count.
    """
    pat = re.compile(rf"^{re.escape(stem)}_(\d{{4}}-\d{{2}}-\d{{2}})\.json$")
    dates = set()
    try:
        for p in local_dir().iterdir():
            m = pat.match(p.name)
            if m:
                dates.add(m.group(1))
    except OSError:
        pass

    b = bucket()
    if b:
        try:
            paginator = _s3_client().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=b, Prefix=f"{prefix()}/{stem}_"):
                for obj in page.get("Contents", []):
                    m = pat.match(obj["Key"].rsplit("/", 1)[-1])
                    if m:
                        dates.add(m.group(1))
        except Exception as e:
            raise EvidenceStoreError(
                f"evidence list failed for {stem}: {type(e).__name__}: {e}")
    return sorted(dates)
