from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from openclaw_web.models import (
    ArtifactEnvelope,
    AuditRecord,
    Candidate,
    CandidateSeed,
    CandidateState,
    ClaimStatus,
    ComponentSet,
    Confidence,
    DeliveryRecord,
    DeliveryState,
    Evidence,
    FeedbackEvent,
    IssueRecord,
    PageRecord,
    PageState,
    ProjectState,
    RunRecord,
    ScoreRecord,
    Severity,
    StageRecord,
    export_schemas,
)

CANONICAL_MODELS = (
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


def _evidence_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_id": "ev-1",
        "candidate_id": "candidate-1",
        "page_url": "https://example.com/",
        "evidence_type": "business-claim",
        "observed_value": "Nhà máy lớn",
        "claim_status": ClaimStatus.OBSERVED,
        "confidence": Confidence.MEDIUM,
        "captured_at": datetime.now(UTC),
        "content_hash": "a" * 64,
        "evidence_urls": [],
        "confidence_gap": None,
    }
    payload.update(overrides)
    return payload


def _score_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "score_name": "technical_pain",
        "score_value": 70,
        "rubric_version": "base-v1",
        "inputs": {},
        "evidence_ids": ["ev-1"],
        "deterministic": True,
        "explanation_vi": "Điểm kỹ thuật.",
    }
    payload.update(overrides)
    return payload


def test_inferred_evidence_requires_source_or_explicit_gap() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev-1",
            candidate_id="candidate-1",
            page_url="https://example.com/",
            evidence_type="business-claim",
            observed_value="Nhà máy lớn",
            claim_status=ClaimStatus.INFERRED,
            confidence="medium",
            captured_at=datetime.now(UTC),
            content_hash="a" * 64,
            evidence_urls=[],
            confidence_gap=None,
        )


def test_deterministic_score_requires_evidence_ids() -> None:
    with pytest.raises(ValidationError):
        ScoreRecord(
            score_name="technical_pain",
            score_value=70,
            rubric_version="base-v1",
            inputs={},
            evidence_ids=[],
            deterministic=True,
            explanation_vi="Điểm kỹ thuật.",
        )


def test_all_workflow_enums_have_canonical_values() -> None:
    assert {item.value for item in ClaimStatus} == {
        "observed",
        "inferred",
        "estimated",
        "unverified",
    }
    assert {item.value for item in Confidence} == {"high", "medium", "low"}
    assert {item.value for item in CandidateState} == {
        "discovered",
        "geofenced",
        "prefiltered",
        "audited",
        "review-ready",
        "geofence-rejected",
        "duplicate",
        "robots-blocked",
        "unqualified",
        "partial",
        "failed-retryable",
        "failed-terminal",
    }
    assert {item.value for item in ProjectState} == {
        "discovered",
        "review",
        "approved",
        "website-brief",
        "task",
        "stakeholder-review",
        "offer-ready",
        "rejected",
    }
    assert {item.value for item in PageState} == {
        "planned",
        "content-draft",
        "content-ready",
        "design-ready",
        "qa-needed",
        "stakeholder-review",
        "approved",
    }
    assert {item.value for item in Severity} == {"P0", "P1", "P2", "P3"}
    assert {item.value for item in DeliveryState} == {"pending", "sending", "sent", "failed"}


def test_candidate_seed_uses_the_discovery_contract() -> None:
    seed = CandidateSeed(
        url="https://example.com/",
        business_name="Công ty Ví dụ",
        address="Hà Nội",
        latitude=21.0285,
        longitude=105.8542,
        industry_hint="manufacturer",
        source_url="https://directory.example/company",
        source_type="public-listing",
        discovered_at=datetime.now(UTC),
    )

    assert str(seed.url) == "https://example.com/"
    assert seed.business_name == "Công ty Ví dụ"


def test_models_module_exposes_schema_export(tmp_path: Path) -> None:
    export_schemas(tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 13


@pytest.mark.parametrize(
    "claim_status", [ClaimStatus.INFERRED, ClaimStatus.ESTIMATED, ClaimStatus.UNVERIFIED]
)
def test_non_observed_evidence_accepts_http_source_or_explicit_gap(
    claim_status: ClaimStatus,
) -> None:
    with_source = Evidence.model_validate(
        _evidence_payload(claim_status=claim_status, evidence_urls=["https://source.example/fact"])
    )
    with_gap = Evidence.model_validate(
        _evidence_payload(claim_status=claim_status, confidence_gap="Chưa có nguồn công khai.")
    )

    assert str(with_source.evidence_urls[0]) == "https://source.example/fact"
    assert with_gap.confidence_gap == "Chưa có nguồn công khai."


@pytest.mark.parametrize("confidence_gap", [None, "", "   "])
def test_non_observed_evidence_rejects_missing_or_blank_gap(confidence_gap: str | None) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            _evidence_payload(
                claim_status=ClaimStatus.INFERRED,
                evidence_urls=[],
                confidence_gap=confidence_gap,
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("page_url", "ftp://example.com/page", id="page-url"),
        pytest.param("evidence_urls", ["file:///tmp/source"], id="evidence-url"),
    ],
)
def test_evidence_rejects_non_http_urls(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(_evidence_payload(**{field: value}))


@pytest.mark.parametrize("evidence_ids", [[], [""], ["  "]])
def test_deterministic_score_rejects_empty_or_blank_evidence_ids(
    evidence_ids: list[str],
) -> None:
    with pytest.raises(ValidationError):
        ScoreRecord.model_validate(_score_payload(evidence_ids=evidence_ids))


def test_non_deterministic_score_can_record_unavailable_inputs_without_evidence() -> None:
    score = ScoreRecord.model_validate(
        _score_payload(
            deterministic=False,
            evidence_ids=[],
            unavailable_inputs=["mobile_lcp"],
            confidence=Confidence.LOW,
        )
    )

    assert score.unavailable_inputs == ("mobile_lcp",)
    assert score.confidence is Confidence.LOW


@pytest.mark.parametrize(
    ("evidence_ids", "recommendation_vi"),
    [
        pytest.param([], "Cải thiện tiêu đề.", id="missing-evidence"),
        pytest.param(["ev-1"], "", id="empty-recommendation"),
        pytest.param(["ev-1"], "   ", id="blank-recommendation"),
    ],
)
def test_issue_requires_evidence_and_nonblank_vietnamese_recommendation(
    evidence_ids: list[str], recommendation_vi: str
) -> None:
    with pytest.raises(ValidationError):
        IssueRecord(
            issue_id="issue-1",
            candidate_id="candidate-1",
            title="Thiếu tiêu đề",
            severity=Severity.P2,
            evidence_ids=evidence_ids,
            recommendation_vi=recommendation_vi,
        )


def test_models_reject_unknown_fields_and_unsafe_primitive_coercion() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(_evidence_payload(unexpected=True))
    with pytest.raises(ValidationError):
        ScoreRecord.model_validate(_score_payload(score_value="70"))
    with pytest.raises(ValidationError):
        ScoreRecord.model_validate(_score_payload(deterministic=1))


@pytest.mark.parametrize("model", CANONICAL_MODELS)
def test_all_canonical_models_enable_strict_python_validation(model: type[Any]) -> None:
    assert model.model_config.get("strict") is True


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        pytest.param(
            CandidateSeed,
            {
                "url": b"https://example.com/",
                "business_name": "Công ty Ví dụ",
                "source_url": "https://directory.example/company",
                "source_type": "public-listing",
                "discovered_at": datetime.now(UTC),
            },
            id="bytes-url",
        ),
        pytest.param(
            Candidate,
            {
                "candidate_id": b"candidate-1",
                "name": "Công ty Ví dụ",
            },
            id="bytes-string",
        ),
        pytest.param(
            Candidate,
            {
                "candidate_id": "candidate-1",
                "name": "Công ty Ví dụ",
                "source_urls": ("https://example.com/",),
            },
            id="tuple-list",
        ),
        pytest.param(
            ScoreRecord,
            _score_payload(evidence_ids={"ev-1"}),
            id="set-list",
        ),
        pytest.param(
            ScoreRecord,
            _score_payload(score_value="70"),
            id="string-float",
        ),
        pytest.param(
            ScoreRecord,
            _score_payload(deterministic="true"),
            id="string-bool",
        ),
        pytest.param(
            FeedbackEvent,
            {
                "event_id": "event-1",
                "event_type": "review",
                "project_id": "project-1",
                "actor_id": "actor-1",
                "action": "approve",
                "created_at": datetime.now(UTC),
                "state_version": "1",
            },
            id="string-int",
        ),
        pytest.param(
            Evidence,
            _evidence_payload(captured_at="2026-08-12T00:00:00Z"),
            id="string-datetime",
        ),
        pytest.param(
            Evidence,
            _evidence_payload(captured_at=datetime.now(UTC).replace(tzinfo=None)),
            id="naive-datetime",
        ),
    ],
)
def test_canonical_models_reject_coercible_python_inputs(
    model: type[Any], payload: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_strict_models_preserve_json_url_and_datetime_parsing() -> None:
    candidate = Candidate.model_validate_json(
        '{"candidate_id":"candidate-1","name":"Công ty Ví dụ",'
        '"website_url":"https://example.com/",'
        '"discovered_at":"2026-08-12T00:00:00Z"}'
    )

    assert str(candidate.website_url) == "https://example.com/"
    assert candidate.discovered_at == datetime(2026, 8, 12, tzinfo=UTC)


def test_evidence_rejects_naive_timestamps_and_invalid_hashes() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            _evidence_payload(captured_at=datetime.now(UTC).replace(tzinfo=None))
        )
    with pytest.raises(ValidationError):
        Evidence.model_validate(_evidence_payload(content_hash="A" * 64))


def test_artifact_envelope_requires_utc_timestamp_and_canonical_metadata() -> None:
    payload = {
        "schema_version": "candidate-v1",
        "generator_version": "generator-v1",
        "source_run_id": "run-1",
        "created_at": datetime.now(UTC),
        "content_hash": "a" * 64,
        "payload": {"id": "candidate-1"},
    }
    envelope = ArtifactEnvelope.model_validate(payload)
    assert envelope.created_at.utcoffset() == timedelta(0)

    offset_envelope = ArtifactEnvelope.model_validate(
        {**payload, "created_at": datetime.now(timezone(timedelta(hours=7)))}
    )
    assert offset_envelope.created_at.utcoffset() == timedelta(0)
    for field in ("schema_version", "generator_version", "source_run_id"):
        with pytest.raises(ValidationError):
            ArtifactEnvelope.model_validate({**payload, field: "   "})


def test_rejected_validated_replacement_does_not_mutate_original() -> None:
    evidence = Evidence.model_validate(_evidence_payload())

    with pytest.raises(ValidationError):
        evidence.validated_replace(
            claim_status=ClaimStatus.INFERRED,
            evidence_urls=[],
            confidence_gap=None,
        )

    assert evidence.claim_status is ClaimStatus.OBSERVED
    assert evidence.evidence_urls == ()
    assert evidence.confidence_gap is None


@pytest.mark.parametrize(
    ("record", "mutation"),
    [
        pytest.param(
            Evidence.model_validate(
                _evidence_payload(observed_value={"claims": [{"text": "observed"}]})
            ),
            lambda record: record.observed_value["claims"][0].__setitem__("text", "changed"),
            id="evidence-json",
        ),
        pytest.param(
            ScoreRecord.model_validate(
                _score_payload(inputs={"signals": [{"name": "mobile_lcp"}]})
            ),
            lambda record: record.inputs["signals"][0].__setitem__("name", "changed"),
            id="score-inputs",
        ),
        pytest.param(
            IssueRecord(
                issue_id="issue-1",
                candidate_id="candidate-1",
                title="Missing heading",
                severity=Severity.P2,
                evidence_ids=["ev-1"],
                recommendation_vi="Them tieu de.",
            ),
            lambda record: record.evidence_ids.append("ev-2"),
            id="issue-evidence-ids",
        ),
        pytest.param(
            ComponentSet(
                component_set_id="component-1",
                message_id="message-1",
                channel_id="channel-1",
                project_id="project-1",
                card_type="review",
                allowed_actions=["approve"],
                expires_at=datetime(2026, 8, 13, tzinfo=UTC),
                state_version=0,
                project_state=ProjectState.REVIEW,
            ),
            lambda record: record.allowed_actions.append("reject"),
            id="component-actions",
        ),
        pytest.param(
            ArtifactEnvelope(
                schema_version="candidate-v1",
                generator_version="generator-v1",
                source_run_id="run-1",
                created_at=datetime(2026, 8, 12, tzinfo=UTC),
                content_hash="a" * 64,
                payload={"items": [{"id": "candidate-1"}]},
            ),
            lambda record: record.payload["items"][0].__setitem__("id", "changed"),
            id="artifact-payload",
        ),
    ],
)
def test_invariant_bearing_collections_are_deeply_immutable(
    record: Any,
    mutation: Any,
) -> None:
    before = record.model_dump(mode="json")

    with pytest.raises((AttributeError, TypeError)):
        mutation(record)

    assert record.model_dump(mode="json") == before


def test_frozen_models_reject_field_assignment_without_partial_mutation() -> None:
    evidence = Evidence.model_validate(_evidence_payload())

    with pytest.raises(ValidationError):
        evidence.claim_status = ClaimStatus.INFERRED

    assert evidence.claim_status is ClaimStatus.OBSERVED


def test_immutable_collections_preserve_constructor_json_and_schema_shapes() -> None:
    score = ScoreRecord.model_validate(_score_payload(inputs={"metrics": [1, {"ok": True}]}))
    score_from_json = ScoreRecord.model_validate_json(
        '{"score_name":"technical_pain","score_value":70,'
        '"rubric_version":"base-v1","inputs":{"metrics":[1,{"ok":true}]},'
        '"evidence_ids":["ev-1"],"deterministic":true,'
        '"explanation_vi":"Diem ky thuat."}'
    )

    assert score.evidence_ids == ("ev-1",)
    assert score_from_json.inputs == score.inputs
    assert score.model_dump(mode="json")["inputs"] == {"metrics": [1, {"ok": True}]}
    schema = ScoreRecord.model_json_schema(mode="serialization")
    assert schema["properties"]["inputs"]["type"] == "object"
    assert schema["properties"]["evidence_ids"]["type"] == "array"


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        pytest.param(UTC, datetime(2026, 8, 12, 0, 0, tzinfo=UTC), id="utc"),
        pytest.param(
            timezone(timedelta(hours=7)),
            datetime(2026, 8, 11, 17, 0, tzinfo=UTC),
            id="bangkok-offset",
        ),
        pytest.param(
            timezone(timedelta(hours=-4)),
            datetime(2026, 8, 12, 4, 0, tzinfo=UTC),
            id="dst-style-offset",
        ),
    ],
)
def test_python_persisted_timestamps_normalize_to_utc(
    offset: timezone,
    expected: datetime,
) -> None:
    candidate = Candidate(
        candidate_id="candidate-1",
        name="Example",
        discovered_at=datetime(2026, 8, 12, 0, 0, tzinfo=offset),
    )

    assert candidate.discovered_at == expected
    assert candidate.discovered_at is not None
    assert candidate.discovered_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        pytest.param("2026-08-12T00:00:00Z", datetime(2026, 8, 12, tzinfo=UTC), id="z"),
        pytest.param(
            "2026-08-12T00:00:00+00:00",
            datetime(2026, 8, 12, tzinfo=UTC),
            id="explicit-utc",
        ),
        pytest.param(
            "2026-08-12T07:00:00+07:00",
            datetime(2026, 8, 12, tzinfo=UTC),
            id="bangkok-offset",
        ),
        pytest.param(
            "2026-08-11T20:00:00-04:00",
            datetime(2026, 8, 12, tzinfo=UTC),
            id="dst-style-offset",
        ),
    ],
)
def test_json_persisted_timestamps_normalize_to_utc(
    timestamp: str,
    expected: datetime,
) -> None:
    candidate = Candidate.model_validate_json(
        '{"candidate_id":"candidate-1","name":"Example",'
        f'"discovered_at":"{timestamp}"}}'
    )

    assert candidate.discovered_at == expected
    assert candidate.model_dump(mode="json")["discovered_at"].endswith("Z")


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        pytest.param(
            Candidate,
            {
                "candidate_id": "candidate-1",
                "name": "Example",
                "discovered_at": datetime(2026, 8, 13, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 12, tzinfo=UTC),
            },
            id="candidate-updated-before-discovered",
        ),
        pytest.param(
            AuditRecord,
            {
                "audit_id": "audit-1",
                "candidate_id": "candidate-1",
                "source_run_id": "run-1",
                "audit_version": "v1",
                "status": "complete",
                "created_at": datetime(2026, 8, 13, tzinfo=UTC),
                "completed_at": datetime(2026, 8, 12, tzinfo=UTC),
            },
            id="audit-completed-before-created",
        ),
        pytest.param(
            StageRecord,
            {
                "stage_id": "stage-1",
                "run_id": "run-1",
                "stage_name": "audit",
                "status": "complete",
                "started_at": datetime(2026, 8, 13, tzinfo=UTC),
                "completed_at": datetime(2026, 8, 12, tzinfo=UTC),
            },
            id="stage-completed-before-started",
        ),
        pytest.param(
            RunRecord,
            {
                "run_id": "run-1",
                "status": "complete",
                "started_at": datetime(2026, 8, 13, tzinfo=UTC),
                "completed_at": datetime(2026, 8, 12, tzinfo=UTC),
            },
            id="run-completed-before-started",
        ),
    ],
)
def test_models_reject_reversed_persisted_intervals(
    model: type[Any],
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="must not be before"):
        model.model_validate(payload)


def test_generated_schemas_declare_draft_2020_12_and_are_valid(tmp_path: Path) -> None:
    output_dir = tmp_path / "schemas"
    export_schemas(output_dir)

    for path in output_dir.glob("*.json"):
        schema = __import__("json").loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    ("model", "filename", "payload"),
    [
        pytest.param(
            Evidence,
            "evidence.json",
            _evidence_payload(
                claim_status=ClaimStatus.INFERRED,
                evidence_urls=[],
                confidence_gap=None,
                captured_at="2026-08-12T00:00:00Z",
            ),
            id="evidence-derived-without-support",
        ),
        pytest.param(
            ScoreRecord,
            "score_record.json",
            _score_payload(evidence_ids=[]),
            id="deterministic-score-without-evidence",
        ),
        pytest.param(
            IssueRecord,
            "issue_record.json",
            {
                "issue_id": "   ",
                "candidate_id": "candidate-1",
                "title": "Missing heading",
                "severity": "P2",
                "evidence_ids": ["ev-1"],
                "recommendation_vi": "Them tieu de.",
            },
            id="blank-trimmed-string",
        ),
        pytest.param(
            Evidence,
            "evidence.json",
            {
                **_evidence_payload(captured_at="2026-08-12T00:00:00Z"),
                "content_hash": "A" * 64,
            },
            id="uppercase-sha256",
        ),
        pytest.param(
            IssueRecord,
            "issue_record.json",
            {
                "issue_id": "issue-1",
                "candidate_id": "candidate-1",
                "title": "Missing heading",
                "severity": "P2",
                "evidence_ids": [],
                "recommendation_vi": "Them tieu de.",
            },
            id="issue-empty-evidence",
        ),
        pytest.param(
            IssueRecord,
            "issue_record.json",
            {
                "issue_id": "issue-1",
                "candidate_id": "candidate-1",
                "title": "Missing heading",
                "severity": "P2",
                "evidence_ids": ["ev-1"],
                "recommendation_vi": "   ",
            },
            id="issue-blank-recommendation",
        ),
        pytest.param(
            ComponentSet,
            "component_set.json",
            {
                "component_set_id": "component-1",
                "message_id": "message-1",
                "channel_id": "channel-1",
                "project_id": "project-1",
                "card_type": "review",
                "allowed_actions": [],
                "expires_at": "2026-08-13T00:00:00Z",
                "state_version": 0,
                "project_state": "review",
            },
            id="component-empty-actions",
        ),
    ],
)
def test_json_schema_and_pydantic_reject_same_representative_invalid_records(
    tmp_path: Path,
    model: type[Any],
    filename: str,
    payload: dict[str, Any],
) -> None:
    output_dir = tmp_path / "schemas"
    export_schemas(output_dir)
    schema = __import__("json").loads((output_dir / filename).read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        model.model_validate(payload)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)
    )
    assert errors


@pytest.mark.parametrize(
    ("filename", "record"),
    [
        pytest.param(
            "evidence.json",
            Evidence.model_validate(
                _evidence_payload(
                    claim_status=ClaimStatus.INFERRED,
                    evidence_urls=["https://source.example/fact"],
                    captured_at=datetime(
                        2026,
                        8,
                        12,
                        7,
                        tzinfo=timezone(timedelta(hours=7)),
                    ),
                )
            ),
            id="evidence",
        ),
        pytest.param(
            "score_record.json",
            ScoreRecord.model_validate(_score_payload()),
            id="score",
        ),
        pytest.param(
            "issue_record.json",
            IssueRecord(
                issue_id="issue-1",
                candidate_id="candidate-1",
                title="Missing heading",
                severity=Severity.P2,
                evidence_ids=["ev-1"],
                recommendation_vi="Them tieu de.",
            ),
            id="issue",
        ),
        pytest.param(
            "artifact_envelope.json",
            ArtifactEnvelope(
                schema_version="candidate-v1",
                generator_version="generator-v1",
                source_run_id="run-1",
                created_at=datetime(2026, 8, 12, tzinfo=UTC),
                content_hash="a" * 64,
                payload={"id": "candidate-1"},
            ),
            id="artifact",
        ),
    ],
)
def test_json_schema_accepts_canonical_model_serialization(
    tmp_path: Path,
    filename: str,
    record: Any,
) -> None:
    output_dir = tmp_path / "schemas"
    export_schemas(output_dir)
    schema = __import__("json").loads(
        (output_dir / filename).read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        record.model_dump(mode="json")
    )


def test_artifact_schema_requires_canonical_utc_serialization(tmp_path: Path) -> None:
    output_dir = tmp_path / "schemas"
    export_schemas(output_dir)
    schema = __import__("json").loads(
        (output_dir / "artifact_envelope.json").read_text(encoding="utf-8")
    )
    document = {
        "schema_version": "candidate-v1",
        "generator_version": "generator-v1",
        "source_run_id": "run-1",
        "created_at": "2026-08-12T07:00:00+07:00",
        "content_hash": "a" * 64,
        "payload": {"id": "candidate-1"},
    }

    assert list(Draft202012Validator(schema).iter_errors(document))
