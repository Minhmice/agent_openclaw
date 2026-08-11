import hashlib
import json
from pathlib import Path

import pytest

from openclaw_web.artifacts import (
    ArtifactIntegrityError,
    atomic_write_artifact,
    export_schemas,
    read_artifact,
)

EXPECTED_SCHEMA_FILES = {
    "artifact_envelope.json",
    "audit_record.json",
    "candidate.json",
    "candidate_seed.json",
    "component_set.json",
    "delivery_record.json",
    "evidence.json",
    "feedback_event.json",
    "issue_record.json",
    "page_record.json",
    "run_record.json",
    "score_record.json",
    "stage_record.json",
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def test_artifact_write_round_trips_and_adds_hash(tmp_path: Path) -> None:
    target = tmp_path / "candidate.json"
    atomic_write_artifact(target, "candidate-v1", "generator-v1", "run-1", {"id": "x"})
    envelope = read_artifact(target)
    assert envelope.schema_version == "candidate-v1"
    assert envelope.payload == {"id": "x"}
    assert len(envelope.content_hash) == 64
    assert envelope.content_hash == hashlib.sha256(_canonical_json_bytes({"id": "x"})).hexdigest()


def test_artifact_write_is_canonical_utf8_and_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "candidate.json"
    payload = {"z": "Tiếng Việt", "a": [2, 1]}

    atomic_write_artifact(target, "candidate-v1", "generator-v1", "run-1", payload)

    raw = target.read_bytes()
    assert b"\\u" not in raw
    assert b'"a":[2,1]' in raw
    assert read_artifact(target).payload == payload


def test_read_artifact_detects_payload_tampering(tmp_path: Path) -> None:
    target = tmp_path / "candidate.json"
    atomic_write_artifact(target, "candidate-v1", "generator-v1", "run-1", {"id": "x"})
    document = json.loads(target.read_text(encoding="utf-8"))
    document["payload"]["id"] = "tampered"
    target.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="content hash mismatch"):
        read_artifact(target)


def test_failed_artifact_write_preserves_existing_target_and_cleans_temp_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "candidate.json"
    target.write_text("existing", encoding="utf-8")

    with pytest.raises((TypeError, ValueError)):
        atomic_write_artifact(
            target,
            "candidate-v1",
            "generator-v1",
            "run-1",
            {"bad": float("nan")},
        )

    assert target.read_text(encoding="utf-8") == "existing"
    assert list(tmp_path.glob(".candidate.json.*.tmp")) == []


def test_export_schemas_is_complete_valid_idempotent_and_removes_stale_files(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "stale.json").write_text("{}", encoding="utf-8")

    export_schemas(output_dir)
    first = {path.name: path.read_bytes() for path in output_dir.iterdir()}
    export_schemas(output_dir)
    second = {path.name: path.read_bytes() for path in output_dir.iterdir()}

    assert set(first) == EXPECTED_SCHEMA_FILES
    assert second == first
    for filename, raw in second.items():
        schema = json.loads(raw)
        assert schema["title"]
        assert filename.endswith(".json")
