"""Atomic, deterministic JSON artifact input/output and schema export."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

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


class ArtifactReadError(ValueError):
    """Base class for safe, classified artifact read failures."""


class ArtifactMalformedError(ArtifactReadError):
    """Raised when artifact bytes are not a UTF-8 JSON document."""


class ArtifactSchemaError(ArtifactReadError):
    """Raised when a JSON document is not a valid artifact envelope."""


class ArtifactIntegrityError(ArtifactReadError):
    """Raised when the payload-only SHA-256 does not match ``content_hash``."""


class SchemaExportLockTimeout(TimeoutError):
    """Raised when another schema writer holds the publication lock too long."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload_sha256(payload: JsonValue) -> str:
    """Return SHA-256 of only the canonical payload, not envelope metadata.

    This digest is an integrity checksum. It does not authenticate either the
    payload or the surrounding envelope metadata.
    """

    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _verify_content_hash(envelope: ArtifactEnvelope) -> None:
    actual_hash = _payload_sha256(envelope.payload)
    if actual_hash != envelope.content_hash:
        raise ArtifactIntegrityError(
            "artifact payload content hash mismatch: "
            f"expected {envelope.content_hash}, got {actual_hash}"
        )


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant: {value}")


def _fsync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; Windows is documented best effort.

    Portable Python cannot open directory handles suitable for ``fsync`` on
    Windows. File contents are still flushed before replacement there, but the
    final directory-entry durability is necessarily weaker across power loss.
    """

    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_file_durably(path: Path, data: bytes) -> None:
    with path.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def atomic_write_artifact(
    target: Path,
    schema_version: str,
    generator_version: str,
    source_run_id: str,
    payload: JsonValue,
) -> None:
    """Validate and atomically publish one deterministic JSON artifact.

    ``content_hash`` is SHA-256 of the canonical payload only. It detects
    accidental or uncoordinated payload changes; it is not metadata
    authentication and is not a cryptographic signature.
    """

    validated_payload = _JSON_VALUE_ADAPTER.validate_python(payload)
    envelope = ArtifactEnvelope(
        schema_version=schema_version,
        generator_version=generator_version,
        source_run_id=source_run_id,
        created_at=datetime.now(UTC),
        content_hash=_payload_sha256(validated_payload),
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
    descriptor_open = True
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            descriptor_open = False
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, target)
        _fsync_directory(target.parent)
    except BaseException:
        if descriptor_open:
            os.close(file_descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def read_artifact(path: Path) -> ArtifactEnvelope:
    """Read an envelope and verify its payload-only integrity checksum."""

    raw = Path(path).read_bytes()
    try:
        json.loads(raw, parse_constant=_reject_nonstandard_json_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactMalformedError("artifact is not a valid UTF-8 JSON document") from exc
    try:
        envelope = ArtifactEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        raise ArtifactSchemaError("artifact envelope failed schema validation") from exc
    _verify_content_hash(envelope)
    return envelope


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _lock_file_nonblocking(file: Any) -> None:
    file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(file: Any) -> None:
    file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _schema_export_lock(output_dir: Path, *, timeout: float) -> Iterator[None]:
    """Serialize cross-process schema writers with a bounded portable lock."""

    if timeout < 0:
        raise ValueError("schema export lock timeout must be non-negative")
    lock_root = Path(tempfile.gettempdir()) / "openclaw-web-schema-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(str(output_dir.resolve()).encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{lock_name}.lock"
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock_file_nonblocking(lock_file)
                break
            except (BlockingIOError, OSError):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SchemaExportLockTimeout(
                        f"timed out waiting for schema export lock: {lock_path}"
                    ) from None
                time.sleep(min(0.025, remaining))
        try:
            yield
        finally:
            _unlock_file(lock_file)


def _add_nonblank_patterns(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "string" and node.get("minLength", 0) >= 1:
            node.setdefault("pattern", r"\S")
        for value in node.values():
            _add_nonblank_patterns(value)
    elif isinstance(node, list):
        for value in node:
            _add_nonblank_patterns(value)


def _schema_document(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    _add_nonblank_patterns(schema)

    if model is Evidence:
        schema.setdefault("allOf", []).append(
            {
                "if": {
                    "properties": {"claim_status": {"not": {"const": "observed"}}},
                    "required": ["claim_status"],
                },
                "then": {
                    "anyOf": [
                        {
                            "properties": {"evidence_urls": {"minItems": 1}},
                            "required": ["evidence_urls"],
                        },
                        {
                            "properties": {
                                "confidence_gap": {"type": "string", "pattern": r"\S"}
                            },
                            "required": ["confidence_gap"],
                        },
                    ]
                },
            }
        )
    elif model is ScoreRecord:
        schema.setdefault("allOf", []).append(
            {
                "if": {
                    "properties": {"deterministic": {"const": True}},
                    "required": ["deterministic"],
                },
                "then": {
                    "properties": {"evidence_ids": {"minItems": 1}},
                    "required": ["evidence_ids"],
                },
            }
        )
    elif model is ArtifactEnvelope:
        schema["properties"]["created_at"]["pattern"] = r"Z$"

    if model is IssueRecord:
        schema["properties"]["evidence_ids"]["minItems"] = 1
    elif model is ComponentSet:
        schema["properties"]["allowed_actions"]["minItems"] = 1
    return schema


def _schema_documents() -> dict[str, bytes]:
    return {
        filename: _canonical_json_bytes(_schema_document(model))
        for filename, model in _SCHEMA_MODELS
    }


def _fsync_existing_file(path: Path) -> None:
    mode = "rb+" if os.name == "nt" else "rb"
    with path.open(mode) as file:
        os.fsync(file.fileno())


def _fsync_tree(directory: Path) -> None:
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            _fsync_existing_file(path)
    for path in sorted(
        (item for item in directory.rglob("*") if item.is_dir() and not item.is_symlink()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        _fsync_directory(path)
    _fsync_directory(directory)


def _copy_file_for_replace(source: Path, destination: Path) -> None:
    temporary = destination.parent / f".{destination.name}.restore.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copyfile(source, temporary)
        _fsync_existing_file(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_backup(output_dir: Path, backup_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {
        path.name for path in backup_dir.iterdir() if path.name != ".publication-complete"
    }
    for current in output_dir.iterdir():
        if current.name not in expected_names:
            _remove_path(current)
    for source in backup_dir.iterdir():
        if source.name == ".publication-complete":
            continue
        destination = output_dir / source.name
        if source.is_symlink():
            temporary = output_dir / f".{source.name}.restore.{uuid.uuid4().hex}.tmp"
            temporary.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
            os.replace(temporary, destination)
        elif source.is_file():
            _copy_file_for_replace(source, destination)
        else:
            if _path_exists(destination):
                _remove_path(destination)
            shutil.copytree(source, destination, symlinks=True)
            _fsync_tree(destination)
    _fsync_directory(output_dir)
    _remove_path(backup_dir)
    _fsync_directory(output_dir.parent)


def _recover_schema_publication(output_dir: Path) -> None:
    parent = output_dir.parent
    transient_prefixes = (
        f".{output_dir.name}.schemas.staging.",
        f".{output_dir.name}.schemas.backup-staging.",
    )
    for path in parent.iterdir():
        if path.name.startswith(transient_prefixes):
            _remove_path(path)

    backups = sorted(
        (
            path
            for path in parent.iterdir()
            if path.name.startswith(f".{output_dir.name}.schemas.backup.")
        ),
        key=lambda path: path.name,
    )
    for backup in backups:
        complete = backup / ".publication-complete"
        if complete.exists() and output_dir.exists():
            _remove_path(backup)
            continue
        if not output_dir.exists():
            os.replace(backup, output_dir)
            (output_dir / ".publication-complete").unlink(missing_ok=True)
            _fsync_directory(output_dir)
            _fsync_directory(parent)
        else:
            _restore_backup(output_dir, backup)


def _create_backup(output_dir: Path, transaction_id: str) -> Path:
    parent = output_dir.parent
    building = parent / f".{output_dir.name}.schemas.backup-staging.{transaction_id}"
    backup = parent / f".{output_dir.name}.schemas.backup.{time.time_ns():020d}.{transaction_id}"
    shutil.copytree(output_dir, building, symlinks=True)
    _fsync_tree(building)
    os.replace(building, backup)
    _fsync_directory(parent)
    return backup


def _publish_over_existing(output_dir: Path, staging_dir: Path, backup_dir: Path) -> None:
    expected_names = {path.name for path in staging_dir.iterdir()}
    for source in sorted(staging_dir.iterdir(), key=lambda path: path.name):
        destination = output_dir / source.name
        if (
            destination.is_file()
            and not destination.is_symlink()
            and destination.read_bytes() == source.read_bytes()
        ):
            source.unlink()
        else:
            os.replace(source, destination)
    for stale in output_dir.iterdir():
        if stale.name not in expected_names:
            _remove_path(stale)
    _fsync_directory(output_dir)
    marker = backup_dir / ".publication-complete"
    _write_file_durably(marker, b"complete\n")
    _fsync_directory(backup_dir)
    _remove_path(backup_dir)
    _fsync_directory(output_dir.parent)


def export_schemas(output_dir: Path, *, lock_timeout: float = 10.0) -> None:
    """Publish a durable deterministic schema set with serialized writers.

    Existing readers retain complete, individually atomically replaced JSON
    files throughout publication. A lock and recoverable backup transaction
    prevent concurrent writers or interrupted replacement from losing the last
    complete generation.
    """

    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with _schema_export_lock(output_dir, timeout=lock_timeout):
        _recover_schema_publication(output_dir)
        transaction_id = uuid.uuid4().hex
        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{output_dir.name}.schemas.staging.{transaction_id}.",
                dir=output_dir.parent,
            )
        )
        backup_dir: Path | None = None
        try:
            for filename, schema_bytes in _schema_documents().items():
                _write_file_durably(staging_dir / filename, schema_bytes)
            _fsync_directory(staging_dir)

            if output_dir.exists():
                backup_dir = _create_backup(output_dir, transaction_id)
                try:
                    _publish_over_existing(output_dir, staging_dir, backup_dir)
                except BaseException:
                    if _path_exists(backup_dir) and not (
                        backup_dir / ".publication-complete"
                    ).exists():
                        _restore_backup(output_dir, backup_dir)
                    raise
            else:
                os.replace(staging_dir, output_dir)
                _fsync_directory(output_dir)
                _fsync_directory(output_dir.parent)
        finally:
            if _path_exists(staging_dir):
                _remove_path(staging_dir)
