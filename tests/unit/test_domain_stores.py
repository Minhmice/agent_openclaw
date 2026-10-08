"""Unit tests verifying domain stores operating directly on SQLite connections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import HttpUrl

from openclaw_web.audit.store import AuditStore
from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.delivery.store import DeliveryStore, ReviewProjectRecord
from openclaw_web.discovery.store import (
    DiscoverySeedBatch,
    DiscoverySeedDisposition,
    DiscoveryStore,
)
from openclaw_web.lead_contracts import (
    PortfolioEntry,
    RedTeamVerdict,
    StageOutcome,
    StageStatus,
)
from openclaw_web.lead_intelligence.store import LeadStore
from openclaw_web.models import (
    CandidateSeed,
    ClaimStatus,
    ComponentSet,
    Confidence,
    DeliveryRecord,
    DeliveryState,
    Evidence,
    IssueRecord,
    PageRecord,
    ProjectState,
    ScoreRecord,
    Severity,
)
from openclaw_web.platform.errors import (
    RepositoryConflict,
    RunConfigMismatchError,
)


def _seed(
    url: str = "https://acme.example/about",
    name: str = "Acme Corp",
    *,
    seed_id: str = "seed-1",
) -> CandidateSeed:
    return CandidateSeed(
        seed_id=seed_id,
        source_url=HttpUrl(url),
        source_type="places",
        discovered_at=datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
        url=HttpUrl(url),
        business_name=name,
        address="123 Main St",
    )


def _page(
    page_id: str = "page-1",
    candidate_id: str = "candidate-1",
) -> PageRecord:
    return PageRecord(
        page_id=page_id,
        candidate_id=candidate_id,
        page_url=HttpUrl("https://example.com/"),
        page_type="landing",
    )


def _evidence(
    evidence_id: str = "evidence-1",
    candidate_id: str = "candidate-1",
    observed_value: str = "fast",
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        candidate_id=candidate_id,
        page_url=HttpUrl("https://example.com/"),
        evidence_type="performance",
        observed_value=observed_value,
        claim_status=ClaimStatus.OBSERVED,
        confidence=Confidence.HIGH,
        captured_at=datetime(2026, 8, 12, tzinfo=UTC),
        content_hash="b" * 64,
        evidence_urls=["https://example.com/"],
    )


def _score(
    score_name: str = "website-quality",
    value: float = 85.0,
) -> ScoreRecord:
    return ScoreRecord(
        score_name=score_name,
        score_value=value,
        rubric_version="v1",
        inputs={"lcp_ms": 1200},
        evidence_ids=["evidence-1"],
        deterministic=True,
        explanation_vi="Trang tải nhanh.",
        confidence=Confidence.HIGH,
    )


def _issue(
    issue_id: str = "issue-1",
    candidate_id: str = "candidate-1",
) -> IssueRecord:
    return IssueRecord(
        issue_id=issue_id,
        candidate_id=candidate_id,
        title="CTA chưa rõ ràng",
        severity=Severity.P1,
        evidence_ids=["evidence-1"],
        recommendation_vi="Thêm CTA rõ ràng.",
        page_url=HttpUrl("https://example.com/"),
    )


def _delivery(
    delivery_id: str = "delivery-1",
    project_id: str = "project-1",
) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=delivery_id,
        event_type="review-card",
        project_id=project_id,
        channel_id="channel-1",
        payload_path="artifacts/review.json",
        idempotency_key=f"review-card:{project_id}:v1",
        status=DeliveryState.PENDING,
    )


def _component_set(
    component_set_id: str = "cs-1",
    project_id: str = "project-1",
) -> ComponentSet:
    return ComponentSet(
        component_set_id=component_set_id,
        channel_id="channel-1",
        message_id="msg-1",
        project_id=project_id,
        card_type="review",
        allowed_actions=["approve", "reject"],
        expires_at=datetime(2026, 8, 13, tzinfo=UTC),
        state_version=1,
        project_state=ProjectState.REVIEW,
    )


def _portfolio_entry(
    portfolio_id: str,
    *,
    entry_id: str = "entry-1",
) -> PortfolioEntry:
    return PortfolioEntry(
        entry_id=entry_id,
        portfolio_id=portfolio_id,
        candidate_id=f"candidate-{entry_id}",
        rank=1,
        company_name="Acme Co",
        website_url="https://acme.example",
        business_strength=80,
        agency_fit=80,
        digital_gap=80,
        evidence_confidence=0.9,
        red_team_verdict=RedTeamVerdict.SURVIVE,
    )


class _ReviewProjectStub(ReviewProjectRecord):
    def __init__(
        self,
        project_id: str = "project-1",
        candidate_id: str = "candidate-1",
    ) -> None:
        self.project_id = project_id
        self.candidate_id = candidate_id
        self.artifact_dir = "artifacts/project-1"
        self.market_id = "market-us"
        self.created_at = datetime(2026, 8, 12, tzinfo=UTC)


def _insert_candidate_parent(connection: Any, candidate_id: str = "candidate-1") -> None:
    connection.execute(
        """
        INSERT INTO candidates (
            candidate_id, canonical_domain, normalized_name, normalized_address,
            state, snapshot_json
        ) VALUES (?, ?, ?, NULL, ?, ?)
        """,
        (candidate_id, f"{candidate_id}.example", candidate_id, "discovered", "{}"),
    )


def _insert_project_parent(connection: Any, project_id: str = "project-1") -> None:
    connection.execute(
        """
        INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json)
        VALUES (?, NULL, ?, 0, ?)
        """,
        (project_id, "new", "{}"),
    )


# ==============================================================================
# DiscoveryStore Tests
# ==============================================================================


def test_discovery_store_upsert_and_duplicates(tmp_path: Path) -> None:
    connection = connect(tmp_path / "discovery.sqlite")
    migrate(connection)
    store = DiscoveryStore(connection)

    assert store.count_candidates() == 0

    candidate = store.upsert_candidate(
        url="https://acme.example/page",
        business_name="Acme Corp",
        address="123 Main St",
    )
    assert candidate.canonical_domain == "acme.example"
    assert store.count_candidates() == 1

    # Same domain upsert is idempotent
    second = store.upsert_candidate(
        url="https://acme.example/other",
        business_name="Acme Corp",
        address="123 Main St",
    )
    assert second.candidate_id == candidate.candidate_id
    assert store.count_candidates() == 1

    # Contradictory observation raises conflict
    with pytest.raises(RepositoryConflict, match="contradictory"):
        store.upsert_candidate(
            url="https://acme.example/new",
            business_name="Totally Different Co",
            address="456 Other Way",
        )

    # Find duplicate by seed
    seed = _seed(url="https://acme.example/contact", name="Acme Corp")
    duplicate = store.find_duplicate(seed)
    assert duplicate is not None
    assert duplicate.candidate_id == candidate.candidate_id

    # Seed batch upsert
    batch = DiscoverySeedBatch(
        seed=seed,
        observations=(seed,),
    )
    res = store.upsert_discovery_seed(batch, "cohort-retail")
    assert res.disposition is DiscoverySeedDisposition.DUPLICATE
    assert res.candidate.candidate_id == candidate.candidate_id


# ==============================================================================
# AuditStore Tests
# ==============================================================================


def test_audit_store_records_and_conflict(tmp_path: Path) -> None:
    connection = connect(tmp_path / "audit.sqlite")
    migrate(connection)
    _insert_candidate_parent(connection, "candidate-1")
    store = AuditStore(connection)

    # Append evidence
    ev = _evidence("ev-1", "candidate-1", "fast")
    store.append_evidence(ev)
    # Idempotent append
    store.append_evidence(ev)

    with pytest.raises(RepositoryConflict, match="different immutable content"):
        store.append_evidence(_evidence("ev-1", "candidate-1", "slow"))

    # Append score
    sc = _score("perf-score", 90.0)
    store.append_score(sc)
    store.append_score(sc)

    # Append issue
    iss = _issue("iss-1", "candidate-1")
    store.append_issue(iss)
    store.append_issue(iss)

    # Pages
    page = _page("p-1", "candidate-1")
    store.append_page(page)
    pages = store.get_pages("candidate-1")
    assert len(pages) == 1
    assert pages[0].page_id == "p-1"


# ==============================================================================
# LeadStore Tests
# ==============================================================================


def test_lead_store_runs_locks_and_portfolios(tmp_path: Path) -> None:
    connection = connect(tmp_path / "lead.sqlite")
    migrate(connection)
    store = LeadStore(connection)

    # Create run
    run = store.create_or_resume_run("key-1", "v1")
    assert run.run_id.startswith("run-")
    assert run.config_version == "v1"

    resumed = store.create_or_resume_run("key-1", "v1")
    assert resumed.run_id == run.run_id

    with pytest.raises(RunConfigMismatchError, match="different config version"):
        store.create_or_resume_run("key-1", "v2")

    # Record stage outcome
    outcome = StageOutcome(
        run_id=run.run_id,
        stage_id="discovery:stage-1",
        producer_role="discovery-scout",
        status=StageStatus.COMPLETE,
        attempt_count=1,
        completed_at=datetime.now(UTC),
        input_refs=("ref-1",),
        output_refs=("ref-2",),
        evidence_ids=(),
        checkpoint={"input_hash": "h1", "stage_index": 1},
    )
    store.record_stage_outcome(outcome)
    outcomes = store.get_stage_outcomes(run.run_id)
    assert len(outcomes) == 1
    assert outcomes[0].stage_id == "discovery:stage-1"

    # Completed stage outcome cannot be replaced
    retry_outcome = outcome.validated_replace(checkpoint={"input_hash": "h2", "stage_index": 1})
    with pytest.raises(RepositoryConflict, match="cannot be replaced|completed stage"):
        store.record_stage_outcome(retry_outcome)

    # Run locks
    now = datetime.now(UTC)
    assert store.acquire_run_lock("lock-1", "worker-1", now, 60)
    assert not store.acquire_run_lock("lock-1", "worker-2", now, 60)
    assert store.renew_run_lock("lock-1", "worker-1", now + timedelta(seconds=10), 60)
    store.release_run_lock("lock-1", "worker-1")
    assert store.acquire_run_lock("lock-1", "worker-2", now, 60)

    # Portfolio
    entry = _portfolio_entry("port-1")
    created = store.create_portfolio(
        portfolio_id="port-1",
        run_id=run.run_id,
        status="complete",
        entries=(entry,),
    )
    assert created
    assert not store.create_portfolio(
        portfolio_id="port-1",
        run_id=run.run_id,
        status="complete",
        entries=(entry,),
    )
    entries = store.get_portfolio_entries("port-1")
    assert len(entries) == 1
    assert entries[0].entry_id == "entry-1"

    # Dashboard action receipts
    receipt = store.claim_dashboard_action(
        idempotency_key="action-key-1",
        action="approve",
        target_id="target-1",
        actor_id="actor-1",
        expected_state_version=0,
    )
    assert receipt.status == "pending"
    completed = store.complete_dashboard_action(
        "action-key-1",
        status="succeeded",
        response={"result": "ok"},
    )
    assert completed.status == "succeeded"


# ==============================================================================
# DeliveryStore Tests
# ==============================================================================


def test_delivery_store_outbox_and_components(tmp_path: Path) -> None:
    connection = connect(tmp_path / "delivery.sqlite")
    migrate(connection)
    _insert_project_parent(connection, "project-1")
    store = DeliveryStore(connection)

    # Enqueue delivery
    deliv = _delivery("deliv-1", "project-1")
    inserted = store.enqueue_delivery(deliv)
    assert inserted.delivery_id == "deliv-1"
    assert not store.enqueue_delivery_once(deliv)

    # Claim next delivery
    claimed = store.claim_next_delivery()
    assert claimed is not None
    assert claimed.delivery_id == "deliv-1"
    assert claimed.status is DeliveryState.SENDING

    # Transition delivery
    sent = claimed.validated_replace(status=DeliveryState.SENT)
    transitioned = store.transition_delivery("deliv-1", claimed, sent)
    assert transitioned.status is DeliveryState.SENT

    # Component set
    cs = _component_set("cs-1", "project-1")
    store.insert_component_set(cs)
    loaded_cs = store.get_component_set("channel-1", "msg-1")
    assert loaded_cs is not None
    assert loaded_cs.component_set_id == "cs-1"

    # Review project and state synchronization
    _insert_candidate_parent(connection, "cand-1")
    project = _ReviewProjectStub("proj-sync", "cand-1")
    assert store.ensure_review_project(project)
    assert not store.ensure_review_project(project)

    sync_result = store.synchronize_project_state(
        "proj-sync",
        expected_version=0,
        state=ProjectState.APPROVED,
        state_version=1,
    )
    assert sync_result
    assert store.get_project_state_version("proj-sync") == 1


# ==============================================================================
# Repository Subclassing & Composition Compatibility Tests
# ==============================================================================


def test_repository_composes_all_domain_stores(tmp_path: Path) -> None:
    connection = connect(tmp_path / "repo.sqlite")
    migrate(connection)
    repo = Repository(connection)

    # Verify inheritance
    assert isinstance(repo, DiscoveryStore)
    assert isinstance(repo, AuditStore)
    assert isinstance(repo, LeadStore)
    assert isinstance(repo, DeliveryStore)

    # Verify composite properties
    assert repo.discovery is repo
    assert repo.audit is repo
    assert repo.lead_intelligence is repo
    assert repo.lead is repo
    assert repo.delivery is repo

    # Verify context management
    with repo as active:
        assert active.count_candidates() == 0

    # Ensure connection close works cleanly
    repo.close()
