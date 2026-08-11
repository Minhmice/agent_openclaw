import codecs
import hashlib
import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import Any, BinaryIO, Self

import pytest
from pydantic import ValidationError

from openclaw_web import artifacts
from openclaw_web.artifacts import (
    ArtifactIntegrityError,
    ArtifactMalformedError,
    ArtifactReadError,
    ArtifactSchemaError,
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


class _FailingBinaryFile:
    def __init__(self, wrapped: BinaryIO, operation: str) -> None:
        self._wrapped = wrapped
        self._operation = operation

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self._wrapped.close()

    def write(self, data: bytes) -> int:
        if self._operation == "write":
            raise OSError("injected write failure")
        return self._wrapped.write(data)

    def flush(self) -> None:
        if self._operation == "flush":
            raise OSError("injected flush failure")
        self._wrapped.flush()

    def fileno(self) -> int:
        return self._wrapped.fileno()


def _schema_generation_worker(
    output_dir: str,
    documents: dict[str, bytes],
    barrier: Any,
    results: Any,
    iterations: int,
) -> None:
    try:
        barrier.wait(timeout=20)
        for _ in range(iterations):
            artifacts._publish_schema_documents(Path(output_dir), documents)
        results.put(None)
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - sent to parent
        results.put(repr(exc))


def _schema_lock_holder(output_dir: str, ready: Any, release: Any) -> None:
    with artifacts._schema_export_lock(Path(output_dir), timeout=5.0):
        ready.set()
        release.wait(timeout=20)


def _schema_generation_documents(generation: str) -> dict[str, bytes]:
    documents: dict[str, bytes] = {}
    for filename, raw in artifacts._schema_documents().items():
        schema = json.loads(raw)
        schema["$comment"] = generation
        documents[filename] = _canonical_json_bytes(schema)
    return documents


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
    assert not raw.startswith(codecs.BOM_UTF8)
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


def test_payload_hash_is_stable_across_metadata_changes(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    payload = {"id": "candidate-1", "signals": [1, 2]}

    atomic_write_artifact(first, "candidate-v1", "generator-v1", "run-1", payload)
    atomic_write_artifact(second, "candidate-v2", "generator-v2", "run-2", payload)

    first_envelope = read_artifact(first)
    assert first_envelope.content_hash == read_artifact(second).content_hash

    document = json.loads(first.read_text(encoding="utf-8"))
    document["generator_version"] = "metadata-was-not-authenticated"
    first.write_text(json.dumps(document), encoding="utf-8")
    assert read_artifact(first).content_hash == first_envelope.content_hash


def test_read_artifact_translates_malformed_json_with_cause(tmp_path: Path) -> None:
    target = tmp_path / "malformed.json"
    target.write_bytes(b'{"schema_version":')

    with pytest.raises(ArtifactMalformedError) as caught:
        read_artifact(target)

    assert isinstance(caught.value.__cause__, json.JSONDecodeError)


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-32-le", codecs.BOM_UTF32_LE),
        ("utf-32-be", codecs.BOM_UTF32_BE),
    ],
)
def test_read_artifact_classifies_non_utf8_json_as_malformed_with_decode_cause(
    tmp_path: Path,
    encoding: str,
    bom: bytes,
) -> None:
    target = tmp_path / "non-utf8.json"
    target.write_bytes(bom + "{}".encode(encoding))

    with pytest.raises(ArtifactMalformedError) as caught:
        read_artifact(target)

    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


def test_read_artifact_rejects_utf8_bom_as_noncanonical_with_cause(tmp_path: Path) -> None:
    target = tmp_path / "bom.json"
    target.write_bytes(codecs.BOM_UTF8 + b"{}")

    with pytest.raises(ArtifactMalformedError) as caught:
        read_artifact(target)

    assert isinstance(caught.value.__cause__, ValueError)


def test_read_artifact_classifies_invalid_utf8_as_malformed_with_decode_cause(
    tmp_path: Path,
) -> None:
    target = tmp_path / "invalid-utf8.json"
    target.write_bytes(b'{"payload":"\xff"}')

    with pytest.raises(ArtifactMalformedError) as caught:
        read_artifact(target)

    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_read_artifact_classifies_nonstandard_json_constants_as_malformed(
    tmp_path: Path,
    constant: str,
) -> None:
    target = tmp_path / "nonstandard.json"
    target.write_text(f'{{"payload":{constant}}}', encoding="utf-8")

    with pytest.raises(ArtifactMalformedError) as caught:
        read_artifact(target)

    assert isinstance(caught.value.__cause__, ValueError)


def test_read_artifact_translates_schema_errors_with_cause(tmp_path: Path) -> None:
    target = tmp_path / "invalid-envelope.json"
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(ArtifactSchemaError) as caught:
        read_artifact(target)

    assert isinstance(caught.value.__cause__, ValidationError)


def test_artifact_integrity_error_is_a_read_error_compatible_with_value_error() -> None:
    assert issubclass(ArtifactMalformedError, ArtifactReadError)
    assert issubclass(ArtifactSchemaError, ArtifactReadError)
    assert issubclass(ArtifactIntegrityError, ArtifactReadError)
    assert issubclass(ArtifactIntegrityError, ValueError)


def test_read_artifact_rejects_structurally_invalid_metadata_before_integrity(
    tmp_path: Path,
) -> None:
    target = tmp_path / "candidate.json"
    atomic_write_artifact(target, "candidate-v1", "generator-v1", "run-1", {"id": "x"})
    document = json.loads(target.read_text(encoding="utf-8"))
    document["schema_version"] = "   "
    target.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        read_artifact(target)

    assert type(caught.value).__name__ == "ArtifactSchemaError"
    assert isinstance(caught.value.__cause__, ValidationError)


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


@pytest.mark.parametrize("operation", ["write", "flush", "fsync", "replace"])
def test_post_mkstemp_failures_preserve_old_target_and_remove_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    target = tmp_path / "candidate.json"
    target.write_text("existing", encoding="utf-8")
    real_fdopen = artifacts.os.fdopen
    real_replace = artifacts.os.replace

    if operation in {"write", "flush"}:
        monkeypatch.setattr(
            artifacts.os,
            "fdopen",
            lambda descriptor, mode: _FailingBinaryFile(
                real_fdopen(descriptor, mode), operation
            ),
        )
    elif operation == "fsync":
        monkeypatch.setattr(
            artifacts.os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(OSError("injected fsync failure")),
        )
    else:
        monkeypatch.setattr(
            artifacts.os,
            "replace",
            lambda source, destination: (
                (_ for _ in ()).throw(OSError("injected replace failure"))
                if Path(destination) == target
                else real_replace(source, destination)
            ),
        )

    with pytest.raises(OSError, match=f"injected {operation} failure"):
        atomic_write_artifact(target, "candidate-v1", "generator-v1", "run-1", {"id": "new"})

    assert target.read_text(encoding="utf-8") == "existing"
    assert list(tmp_path.glob(".candidate.json.*.tmp")) == []


def test_artifact_publish_fsyncs_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "candidate.json"
    synced: list[Path] = []
    helper = getattr(artifacts, "_fsync_directory", None)
    assert helper is not None
    monkeypatch.setattr(artifacts, "_fsync_directory", lambda path: synced.append(Path(path)))

    atomic_write_artifact(target, "candidate-v1", "generator-v1", "run-1", {"id": "x"})

    assert synced[-1] == tmp_path


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific directory durability contract")
def test_windows_directory_fsync_is_documented_best_effort(tmp_path: Path) -> None:
    helper = getattr(artifacts, "_fsync_directory", None)
    assert helper is not None

    helper(tmp_path)
    assert "best effort" in (helper.__doc__ or "").lower()


def test_posix_directory_fsync_opens_syncs_and_closes_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakePosixOs:
        name = "posix"
        O_RDONLY = os.O_RDONLY

        @staticmethod
        def open(path: Path, flags: int) -> int:
            calls.append(("open", (path, flags)))
            return 42

        @staticmethod
        def fsync(descriptor: int) -> None:
            calls.append(("fsync", descriptor))

        @staticmethod
        def close(descriptor: int) -> None:
            calls.append(("close", descriptor))

    monkeypatch.setattr(artifacts, "os", FakePosixOs)

    artifacts._fsync_directory(tmp_path)

    assert calls == [
        ("open", (tmp_path, os.O_RDONLY)),
        ("fsync", 42),
        ("close", 42),
    ]


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


def test_changed_schema_generations_are_serialized_and_read_as_complete_snapshots(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "generated"
    initial_generation = "generation-initial"
    artifacts._publish_schema_documents(
        output_dir,
        _schema_generation_documents(initial_generation),
    )
    context = multiprocessing.get_context("spawn")
    worker_count = 3
    generations = [f"generation-{index}" for index in range(worker_count)]
    documents = [_schema_generation_documents(generation) for generation in generations]
    barrier = context.Barrier(worker_count + 1)
    results = context.Queue()
    processes = [
        context.Process(
            target=_schema_generation_worker,
            args=(str(output_dir), schema_documents, barrier, results, 4),
        )
        for schema_documents in documents
    ]
    for process in processes:
        process.start()

    deadline = time.monotonic() + 30
    observed_generations = {initial_generation}
    try:
        barrier.wait(timeout=20)
        while any(process.is_alive() for process in processes):
            if time.monotonic() >= deadline:
                pytest.fail("changed-generation publication exceeded its 30-second deadline")
            with artifacts._schema_export_lock(output_dir, timeout=10.0):
                visible = {
                    path.name: json.loads(path.read_bytes())
                    for path in output_dir.glob("*.json")
                }
            assert set(visible) == EXPECTED_SCHEMA_FILES
            snapshot_generations = {
                schema.get("$comment") for schema in visible.values()
            }
            assert len(snapshot_generations) == 1
            observed_generations.update(snapshot_generations)
    finally:
        for process in processes:
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    assert [results.get(timeout=5) for _ in processes] == [None] * worker_count
    with artifacts._schema_export_lock(output_dir, timeout=10.0):
        final_visible = {
            path.name: json.loads(path.read_bytes())
            for path in output_dir.glob("*.json")
        }
    assert set(final_visible) == EXPECTED_SCHEMA_FILES
    final_generations = {schema.get("$comment") for schema in final_visible.values()}
    assert len(final_generations) == 1
    observed_generations.update(final_generations)
    assert observed_generations <= {initial_generation, *generations}
    assert observed_generations & set(generations)
    assert list(tmp_path.glob(".generated.schemas.*")) == []


def test_schema_lock_wait_is_bounded_and_reports_clear_timeout(tmp_path: Path) -> None:
    output_dir = tmp_path / "generated"
    lock_factory = getattr(artifacts, "_schema_export_lock", None)
    assert lock_factory is not None
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_schema_lock_holder,
        args=(str(output_dir), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=10)
        with pytest.raises(TimeoutError, match="timed out waiting for schema export lock"):
            export_schemas(output_dir, lock_timeout=0.05)
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
    assert holder.exitcode == 0


def test_export_recovers_orphan_backup_before_attempting_new_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "generated"
    backup_dir = tmp_path / ".generated.schemas.backup.00000000000000000001.orphan"
    backup_dir.mkdir()
    (backup_dir / "last-known.json").write_text('{"healthy":true}', encoding="utf-8")

    class BrokenModel:
        @classmethod
        def model_json_schema(cls, *, mode: str) -> dict[str, Any]:
            raise RuntimeError(f"injected generation failure in {mode}")

    monkeypatch.setattr(artifacts, "_SCHEMA_MODELS", (("broken.json", BrokenModel),))

    with pytest.raises(RuntimeError, match="injected generation failure"):
        export_schemas(output_dir)

    assert (output_dir / "last-known.json").read_text(encoding="utf-8") == '{"healthy":true}'
    assert not backup_dir.exists()


def test_export_recovers_complete_orphan_backup_without_internal_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "generated"
    backup_dir = tmp_path / ".generated.schemas.backup.00000000000000000001.orphan"
    backup_dir.mkdir()
    (backup_dir / "last-known.json").write_text('{"healthy":true}', encoding="utf-8")
    (backup_dir / ".publication-complete").write_text("complete\n", encoding="utf-8")

    class BrokenModel:
        @classmethod
        def model_json_schema(cls, *, mode: str) -> dict[str, Any]:
            raise RuntimeError(f"injected generation failure in {mode}")

    monkeypatch.setattr(artifacts, "_SCHEMA_MODELS", (("broken.json", BrokenModel),))

    with pytest.raises(RuntimeError, match="injected generation failure"):
        export_schemas(output_dir)

    assert (output_dir / "last-known.json").read_text(encoding="utf-8") == '{"healthy":true}'
    assert not (output_dir / ".publication-complete").exists()
    assert not backup_dir.exists()


def test_export_removes_orphan_staging_directories_before_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "generated"
    staging = tmp_path / ".generated.schemas.staging.orphan"
    backup_staging = tmp_path / ".generated.schemas.backup-staging.orphan"
    staging.mkdir()
    backup_staging.mkdir()

    monkeypatch.setattr(
        artifacts,
        "_schema_documents",
        lambda: (_ for _ in ()).throw(RuntimeError("injected generation failure")),
    )

    with pytest.raises(RuntimeError, match="injected generation failure"):
        export_schemas(output_dir)

    assert not staging.exists()
    assert not backup_staging.exists()


def test_failed_schema_publication_restores_exact_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "generated"
    export_schemas(output_dir)
    before = {path.name: path.read_bytes() for path in output_dir.iterdir()}
    real_replace = artifacts.os.replace
    real_schema_documents = artifacts._schema_documents
    publication_replaces = 0

    monkeypatch.setattr(
        artifacts,
        "_schema_documents",
        lambda: {name: raw + b"\n" for name, raw in real_schema_documents().items()},
    )

    def fail_during_publication(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        nonlocal publication_replaces
        destination_path = Path(destination)
        if destination_path.parent == output_dir and destination_path.suffix == ".json":
            publication_replaces += 1
            if publication_replaces == 3:
                raise OSError("injected schema rename failure")
        real_replace(source, destination)

    monkeypatch.setattr(artifacts.os, "replace", fail_during_publication)

    with pytest.raises(OSError, match="injected schema rename failure"):
        export_schemas(output_dir)

    after = {path.name: path.read_bytes() for path in output_dir.iterdir()}
    assert after == before


def test_post_publish_backup_cleanup_failure_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "generated"
    export_schemas(output_dir)
    real_remove = artifacts._remove_path
    injected = False

    def fail_backup_cleanup_once(path: Path) -> None:
        nonlocal injected
        if ".schemas.backup." in path.name and not injected:
            injected = True
            raise OSError("injected backup cleanup failure")
        real_remove(path)

    monkeypatch.setattr(artifacts, "_remove_path", fail_backup_cleanup_once)
    with pytest.raises(OSError, match="injected backup cleanup failure"):
        export_schemas(output_dir)

    assert {path.name for path in output_dir.glob("*.json")} == EXPECTED_SCHEMA_FILES
    assert list(tmp_path.glob(".generated.schemas.backup.*"))

    monkeypatch.setattr(artifacts, "_remove_path", real_remove)
    export_schemas(output_dir)
    assert list(tmp_path.glob(".generated.schemas.backup.*")) == []


def test_schema_export_fsyncs_staging_output_and_parent_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "generated"
    synced: list[Path] = []
    helper = getattr(artifacts, "_fsync_directory", None)
    assert helper is not None
    monkeypatch.setattr(artifacts, "_fsync_directory", lambda path: synced.append(Path(path)))

    export_schemas(output_dir)

    assert output_dir in synced
    assert tmp_path in synced
    assert any(".generated.schemas.staging." in path.name for path in synced)


def test_remove_path_unlinks_directory_symlink_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = tmp_path / "directory-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")

    artifacts._remove_path(link)

    assert not link.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_export_replaces_expected_schema_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "generated"
    export_schemas(output_dir)
    schema_path = output_dir / "evidence.json"
    external = tmp_path / "external.json"
    original = schema_path.read_bytes()
    external.write_bytes(original)
    schema_path.unlink()
    try:
        schema_path.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks are unavailable on this platform")

    export_schemas(output_dir)

    assert not schema_path.is_symlink()
    assert schema_path.read_bytes() == original
    assert external.read_bytes() == original
