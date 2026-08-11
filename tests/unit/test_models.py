from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openclaw_web.models import (
    ArtifactEnvelope,
    CandidateSeed,
    CandidateState,
    ClaimStatus,
    Confidence,
    DeliveryState,
    Evidence,
    IssueRecord,
    PageState,
    ProjectState,
    ScoreRecord,
    Severity,
    export_schemas,
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

    assert score.unavailable_inputs == ["mobile_lcp"]
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

    with pytest.raises(ValidationError):
        ArtifactEnvelope.model_validate(
            {**payload, "created_at": datetime.now(timezone(timedelta(hours=7)))}
        )
    for field in ("schema_version", "generator_version", "source_run_id"):
        with pytest.raises(ValidationError):
            ArtifactEnvelope.model_validate({**payload, field: "   "})
