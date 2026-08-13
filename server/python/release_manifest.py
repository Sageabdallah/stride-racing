"""Atomic release-bundle contract for STRIDE inference artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


MANIFEST_VERSION = 1
ARTIFACT_NAMES = (
    "catboost", "lightgbm", "xgboost", "ensemble", "calibrator",
    "decision_model",
)
ALLOWED_STATUSES = {"PRESENT", "EMBEDDED", "NONE", "NOT_APPLICABLE"}
REQUIRED_METADATA = (
    "release_id", "created_at", "source_commit", "image_digest",
    "training_data_build", "feature_schema", "wrapper",
    "risk_configuration", "decision_time_configuration",
    "settlement_configuration",
)


class ReleaseManifestError(RuntimeError):
    """A release bundle is incomplete, inconsistent, or has been altered."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_artifact_path(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ReleaseManifestError(f"artifact path must be relative: {relative!r}")
    resolved_root = root.resolve()
    resolved = (root / relative).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ReleaseManifestError(f"artifact path escapes bundle root: {relative!r}")
    return resolved


def _has_unresolved_metadata(value: Any) -> bool:
    """Required release metadata must contain concrete, non-placeholder data."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().upper() == "UNRESOLVED"
    if isinstance(value, dict):
        return not value or any(_has_unresolved_metadata(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return not value or any(_has_unresolved_metadata(item) for item in value)
    return False


def _validate_checksum_record(label: str, record: Dict[str, Any]) -> None:
    if not record.get("path") or not record.get("sha256"):
        raise ReleaseManifestError(f"{label} requires path and sha256")
    checksum = str(record["sha256"])
    if len(checksum) != 64:
        raise ReleaseManifestError(f"{label} sha256 is not 64 hexadecimal characters")
    try:
        int(checksum, 16)
    except ValueError as exc:
        raise ReleaseManifestError(f"{label} sha256 is not hexadecimal") from exc


def validate_manifest_structure(manifest: Dict[str, Any]) -> None:
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ReleaseManifestError(
            f"manifest_version must be {MANIFEST_VERSION}, got {manifest.get('manifest_version')!r}"
        )
    unresolved_meta = [
        name for name in REQUIRED_METADATA
        if _has_unresolved_metadata(manifest.get(name))
    ]
    if unresolved_meta:
        raise ReleaseManifestError(
            "manifest unresolved metadata: " + ", ".join(unresolved_meta)
        )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ReleaseManifestError("manifest artifacts must be an object")
    missing_artifacts = [name for name in ARTIFACT_NAMES if name not in artifacts]
    if missing_artifacts:
        raise ReleaseManifestError("manifest missing artifacts: " + ", ".join(missing_artifacts))
    for name in ARTIFACT_NAMES:
        record = artifacts[name]
        if not isinstance(record, dict):
            raise ReleaseManifestError(f"artifact {name} must be an object")
        status = record.get("status")
        if status not in ALLOWED_STATUSES:
            raise ReleaseManifestError(f"artifact {name} has invalid status {status!r}")
        if status in {"PRESENT", "EMBEDDED"}:
            _validate_checksum_record(f"artifact {name} {status}", record)
    if artifacts["ensemble"].get("status") not in {"PRESENT", "EMBEDDED"}:
        raise ReleaseManifestError("ensemble artifact must be PRESENT or EMBEDDED")

    auxiliary = manifest.get("auxiliary_artifacts")
    if not isinstance(auxiliary, list):
        raise ReleaseManifestError("manifest auxiliary_artifacts must be a list")
    declared_paths = {
        str(record["path"])
        for record in artifacts.values()
        if isinstance(record, dict)
        and record.get("status") in {"PRESENT", "EMBEDDED"}
    }
    for index, record in enumerate(auxiliary):
        label = f"auxiliary artifact {index}"
        if not isinstance(record, dict):
            raise ReleaseManifestError(f"{label} must be an object")
        _validate_checksum_record(label, record)
        object_key = str(record.get("object_key") or "").strip()
        if not object_key:
            raise ReleaseManifestError(f"{label} requires object_key for remote staging")
        path = str(record["path"])
        if path in declared_paths:
            raise ReleaseManifestError(f"{label} duplicates artifact path {path!r}")
        declared_paths.add(path)


def artifact_downloads(manifest: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """Return unique ``(object_key, relative_path)`` pairs for S3 staging."""
    validate_manifest_structure(manifest)
    downloads = []
    seen = set()
    for name in ARTIFACT_NAMES:
        record = manifest["artifacts"][name]
        if record["status"] not in {"PRESENT", "EMBEDDED"}:
            continue
        object_key = str(record.get("object_key") or "").strip()
        if not object_key:
            raise ReleaseManifestError(f"artifact {name} requires object_key for remote staging")
        pair = (object_key, str(record["path"]))
        if pair not in seen:
            downloads.append(pair)
            seen.add(pair)
    for index, record in enumerate(manifest["auxiliary_artifacts"]):
        pair = (str(record["object_key"]), str(record["path"]))
        if pair in seen:
            raise ReleaseManifestError(
                f"auxiliary artifact {index} duplicates download {pair!r}"
            )
        downloads.append(pair)
        seen.add(pair)
    return tuple(downloads)


def validate_artifact_files(manifest: Dict[str, Any], artifact_root: Path) -> None:
    validate_manifest_structure(manifest)
    for name in ARTIFACT_NAMES:
        record = manifest["artifacts"][name]
        if record["status"] not in {"PRESENT", "EMBEDDED"}:
            continue
        path = _safe_artifact_path(artifact_root, str(record["path"]))
        if not path.is_file():
            raise ReleaseManifestError(f"artifact {name} missing at {path}")
        actual = sha256_file(path)
        if actual.lower() != str(record["sha256"]).lower():
            raise ReleaseManifestError(
                f"artifact {name} checksum mismatch: expected {record['sha256']}, got {actual}"
            )
    for index, record in enumerate(manifest["auxiliary_artifacts"]):
        path = _safe_artifact_path(artifact_root, str(record["path"]))
        if not path.is_file():
            raise ReleaseManifestError(f"auxiliary artifact {index} missing at {path}")
        actual = sha256_file(path)
        if actual.lower() != str(record["sha256"]).lower():
            raise ReleaseManifestError(
                f"auxiliary artifact {index} checksum mismatch: "
                f"expected {record['sha256']}, got {actual}"
            )


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"cannot load release manifest {path}: {exc}") from exc
    validate_manifest_structure(manifest)
    return manifest


def load_and_validate_manifest(path: Path, artifact_root: Path) -> Dict[str, Any]:
    manifest = load_manifest(path)
    validate_artifact_files(manifest, artifact_root)
    return manifest


def release_context_from_env() -> Dict[str, str]:
    return {
        "release_id": os.environ.get("STRIDE_RELEASE_ID", "UNRESOLVED"),
        "manifest_sha256": os.environ.get("STRIDE_RELEASE_MANIFEST_SHA256", "UNRESOLVED"),
        "source_commit": os.environ.get("STRIDE_IMAGE_SHA", "UNRESOLVED"),
        "image_digest": os.environ.get("STRIDE_IMAGE_DIGEST", "UNRESOLVED"),
    }
