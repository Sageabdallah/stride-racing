"""Atomic release manifest validation and fail-closed behavior."""

from __future__ import annotations

import hashlib
import json

import pytest

from release_manifest import (
    ARTIFACT_NAMES,
    ReleaseManifestError,
    artifact_downloads,
    load_and_validate_manifest,
    validate_manifest_structure,
)


def _manifest(bundle, payload=b"ensemble"):
    artifact = bundle / "racing_ensemble_v2.pkl"
    artifact.write_bytes(payload)
    checksum = hashlib.sha256(payload).hexdigest()
    artifacts = {name: {"status": "NONE"} for name in ARTIFACT_NAMES}
    artifacts.update({
        "ensemble": {
            "status": "PRESENT", "path": artifact.name,
            "object_key": f"releases/r1/{artifact.name}", "sha256": checksum,
        },
        "xgboost": {
            "status": "EMBEDDED", "path": artifact.name,
            "object_key": f"releases/r1/{artifact.name}", "sha256": checksum,
        },
    })
    return {
        "manifest_version": 1,
        "release_id": "r1",
        "created_at": "2026-08-10T00:00:00Z",
        "source_commit": "abc123",
        "image_digest": "sha256:image",
        "training_data_build": {"id": "td1"},
        "feature_schema": {"version": "v2"},
        "artifacts": artifacts,
        "auxiliary_artifacts": [],
        "wrapper": {"version": "v1"},
        "risk_configuration": {"version": "flat"},
        "decision_time_configuration": {"version": "tip-time"},
        "settlement_configuration": {"version": "v1"},
    }


def test_valid_bundle_loads(tmp_path):
    manifest = _manifest(tmp_path)
    path = tmp_path / "release_manifest.json"
    path.write_text(json.dumps(manifest))
    assert load_and_validate_manifest(path, tmp_path)["release_id"] == "r1"


def test_checksum_mismatch_fails_closed(tmp_path):
    manifest = _manifest(tmp_path)
    path = tmp_path / "release_manifest.json"
    path.write_text(json.dumps(manifest))
    (tmp_path / "racing_ensemble_v2.pkl").write_bytes(b"tampered")
    with pytest.raises(ReleaseManifestError, match="checksum mismatch"):
        load_and_validate_manifest(path, tmp_path)


def test_missing_artifact_fails_closed(tmp_path):
    manifest = _manifest(tmp_path)
    path = tmp_path / "release_manifest.json"
    path.write_text(json.dumps(manifest))
    (tmp_path / "racing_ensemble_v2.pkl").unlink()
    with pytest.raises(ReleaseManifestError, match="missing"):
        load_and_validate_manifest(path, tmp_path)


def test_auxiliary_artifacts_are_downloaded_and_checksum_validated(tmp_path):
    manifest = _manifest(tmp_path)
    auxiliary = tmp_path / "sectional_combiner_weights.json"
    auxiliary.write_bytes(b"weights")
    manifest["auxiliary_artifacts"] = [{
        "path": auxiliary.name,
        "object_key": f"releases/r1/{auxiliary.name}",
        "sha256": hashlib.sha256(b"weights").hexdigest(),
    }]
    assert (
        f"releases/r1/{auxiliary.name}", auxiliary.name
    ) in artifact_downloads(manifest)
    path = tmp_path / "release_manifest.json"
    path.write_text(json.dumps(manifest))
    assert load_and_validate_manifest(path, tmp_path)["auxiliary_artifacts"]


@pytest.mark.parametrize("unresolved", ["UNRESOLVED", "", "   "])
def test_unresolved_required_metadata_is_rejected(tmp_path, unresolved):
    manifest = _manifest(tmp_path)
    manifest["wrapper"] = {"status": unresolved}
    with pytest.raises(ReleaseManifestError, match="unresolved metadata: wrapper"):
        validate_manifest_structure(manifest)
