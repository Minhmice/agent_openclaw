"""Atomic, deterministic JSON artifact input/output and schema export."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue, TypeAdapter

from openclaw_web.models import (
    ArtifactEnvelope,
    AuditRecord,
    Candidate,
    CandidateSeed,
    ComponentSet,
    DeliveryRecord,
    Evidence,
    FeedbackEvent,
    IssueRecord,
    PageRecord,
    RunRecord,
    ScoreRecord,
    StageRecord,
)

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_SCHEMA_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("artifact_envelope.json", ArtifactEnvelope),
    ("audit_record.json", AuditRecord),
    ("candidate.json", Candidate),
    ("candidate_seed.json", CandidateSeed),
    ("component_set.json", ComponentSet),
    ("delivery_record.json", DeliveryRecord),
    ("evidence.json", Evidence),
    ("feedback_event.json", FeedbackEvent),
    ("issue_record.json", IssueRecord),
    ("page_record.json", PageRecord),
    ("run_record.json", RunRecord),
    ("score_record.json", ScoreRecord),
    ("stage_record.json", StageRecord),
)


class ArtifactIntegrityError(ValueError):
    """Raised when an artifact payload no longer matches its recorded digest."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload_hash(payload: JsonValue) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _verify_content_hash(envelope: ArtifactEnvelope) -> None:
    actual_hash = _payload_hash(envelope.payload)
    if actual_hash != envelope.content_hash:
        raise ArtifactIntegrityError(
            f"artifact content hash mismatch: expected {envelope.content_hash}, got {actual_hash}"
        )


def atomic_write_artifact(
    target: Path,
    schema_version: str,
    generator_version: str,
    source_run_id: str,
    payload: JsonValue,
) -> None:
    """Validate and atomically publish one deterministic JSON artifact."""

    validated_payload = _JSON_VALUE_ADAPTER.validate_python(payload)
    envelope = ArtifactEnvelope(
        schema_version=schema_version,
        generator_version=generator_version,
        source_run_id=source_run_id,
        created_at=datetime.now(UTC),
        content_hash=_payload_hash(validated_payload),
        payload=validated_payload,
    )
    serialized = _canonical_json_bytes(envelope.model_dump(mode="json"))

    # Validate the exact bytes that will be published, including the digest.
    serialized_envelope = ArtifactEnvelope.model_validate_json(serialized)
    _verify_content_hash(serialized_envelope)

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, target)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def read_artifact(path: Path) -> ArtifactEnvelope:
    """Read and validate an artifact, rejecting payload tampering."""

    envelope = ArtifactEnvelope.model_validate_json(Path(path).read_bytes())
    _verify_content_hash(envelope)
    return envelope


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def export_schemas(output_dir: Path) -> None:
    """Publish a complete deterministic set of canonical model JSON Schemas."""

    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.schemas.", dir=output_dir.parent)
    )
    backup_dir = output_dir.parent / f".{output_dir.name}.backup.{uuid.uuid4().hex}"
    moved_existing = False
    published = False

    try:
        for filename, model in _SCHEMA_MODELS:
            schema_bytes = _canonical_json_bytes(model.model_json_schema(mode="serialization"))
            json.loads(schema_bytes)
            (staging_dir / filename).write_bytes(schema_bytes)

        if output_dir.exists():
            os.replace(output_dir, backup_dir)
            moved_existing = True
        os.replace(staging_dir, output_dir)
        published = True
        if moved_existing:
            _remove_path(backup_dir)
    except BaseException:
        if moved_existing and not published and backup_dir.exists() and not output_dir.exists():
            os.replace(backup_dir, output_dir)
        raise
    finally:
        if staging_dir.exists():
            _remove_path(staging_dir)
        if published and backup_dir.exists():
            _remove_path(backup_dir)
