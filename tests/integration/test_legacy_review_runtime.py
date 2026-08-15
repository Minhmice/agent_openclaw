from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from openclaw_web.db import Repository, connect, migrate
from openclaw_web.delivery.components import ComponentSetRecord
from openclaw_web.delivery.openclaw_transport import SentMessage
from openclaw_web.models import ComponentSet, DeliveryRecord, DeliveryState, ProjectState
from openclaw_web.runtime import _component_read_only, _MutableReviewProject, run_legacy_review

GUILD_ID = "1446612692910739637"
CHANNEL_ID = "1536658476288450630"
MESSAGE_ID = "1537000000000000000"


DOSSIER = """# Hồ sơ review

Tóm tắt: lead B2B IT services. Proof dày: 1500+ workforce, 760+ projects, 380+ clients.

Top issues:
- P1: homepage có quá nhiều CTA song song
- P1: contact page trộn form với office info
- P2: service taxonomy quá rộng

Evidence:
- https://example.com/

Confidence gaps: chưa có screenshot audit.

Ảnh first-party: 1 asset trong image inventory.
"""


class FakeTransport:
    calls = 0

    def __init__(self, *, guild_id: str) -> None:
        assert guild_id == GUILD_ID

    def send(self, _record):
        type(self).calls += 1
        return SentMessage(
            MESSAGE_ID,
            f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{MESSAGE_ID}",
        )


def _write_project(root: Path) -> None:
    project_dir = root / "projects" / "vn-ntq-test"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "project_id": "vn-ntq-test",
                "business_name": "NTQ Solution",
                "status": "review",
                "state_version": 0,
                "industry": "IT / AI / Cloud",
                "target_market": "B2B",
                "website": "https://example.com/",
                "dossier_file": "curie-dossier.md",
                "image_inventory_file": "image-inventory.json",
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "curie-dossier.md").write_text(DOSSIER, encoding="utf-8")
    (project_dir / "image-inventory.json").write_text("[]", encoding="utf-8")


def test_legacy_review_is_idempotent_and_persists_component_identity(
    tmp_path: Path, monkeypatch
) -> None:
    workflow_root = tmp_path / "workflow"
    state_db = tmp_path / "state.sqlite"
    _write_project(workflow_root)
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setattr("openclaw_web.runtime.OpenClawAgentTransport", FakeTransport)

    first = run_legacy_review(
        "vn-ntq-test",
        workflow_root=workflow_root,
        review_channel=CHANNEL_ID,
        guild_id=GUILD_ID,
        artifact_root=tmp_path / "artifacts",
    )
    second = run_legacy_review(
        "vn-ntq-test",
        workflow_root=workflow_root,
        review_channel=CHANNEL_ID,
        guild_id=GUILD_ID,
        artifact_root=tmp_path / "artifacts",
    )

    assert first["status"] == "sent"
    assert second["message_id"] == MESSAGE_ID
    assert FakeTransport.calls == 1
    connection = connect(state_db)
    try:
        migrate(connection)
        repository = Repository(connection)
        assert repository.connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert repository.connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
        assert repository.connection.execute("SELECT COUNT(*) FROM component_sets").fetchone()[0] == 1
        component = repository.get_component_set(CHANNEL_ID, MESSAGE_ID)
        assert component is not None
        assert component.allowed_actions == ["approve", "reject", "view-evidence", "refresh"]
        payload = json.loads(
            (tmp_path / "artifacts" / "vn-ntq-test" / "review-card.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload["component_set"]["allowed_actions"] == [
            "approve",
            "reject",
            "view-evidence",
            "refresh",
        ]
        rendered = {
            button["label"]: button
            for button in payload["components"]["blocks"][0]["buttons"]
        }
        assert rendered["Reject"]["allowedUsers"] == ["620891893659598850"]
    finally:
        connection.close()


def test_legacy_review_reissues_v1_when_only_an_old_card_exists(
    tmp_path: Path, monkeypatch
) -> None:
    workflow_root = tmp_path / "workflow"
    state_db = tmp_path / "state.sqlite"
    artifact_root = tmp_path / "artifacts"
    _write_project(workflow_root)
    old_payload = tmp_path / "old-review-card.json"
    old_payload.write_text("{}", encoding="utf-8")

    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    candidate = repository.upsert_candidate("https://example.com/", "NTQ Solution", None)
    repository.ensure_review_project(
        _MutableReviewProject(
            project_id="vn-ntq-test",
            candidate_id=candidate.candidate_id,
            market_id="hanoi-80km",
            artifact_dir=str(artifact_root / "vn-ntq-test"),
            created_at=datetime.now(UTC),
        )
    )
    repository.enqueue_delivery(
        DeliveryRecord(
            delivery_id="delivery-v0",
            event_type="review-card",
            project_id="vn-ntq-test",
            channel_id=CHANNEL_ID,
            payload_path=str(old_payload),
            idempotency_key="review:vn-ntq-test",
            status=DeliveryState.SENT,
            message_id="1537000000000000001",
            message_url=f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/1537000000000000001",
        )
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="component-v0",
            message_id="1537000000000000001",
            channel_id=CHANNEL_ID,
            project_id="vn-ntq-test",
            card_type="review",
            allowed_actions=["approve", "view-evidence", "refresh"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()

    class VersionedFakeTransport:
        calls = 0

        def __init__(self, *, guild_id: str) -> None:
            assert guild_id == GUILD_ID

        def send(self, _record):
            type(self).calls += 1
            message_id = "1537000000000000002"
            return SentMessage(
                message_id,
                f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{message_id}",
            )

    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setattr("openclaw_web.runtime.OpenClawAgentTransport", VersionedFakeTransport)

    result = run_legacy_review(
        "vn-ntq-test",
        workflow_root=workflow_root,
        review_channel=CHANNEL_ID,
        guild_id=GUILD_ID,
        artifact_root=artifact_root,
    )

    assert result["status"] == "sent"
    assert result["message_id"] == "1537000000000000002"
    assert VersionedFakeTransport.calls == 1
    verification = connect(state_db)
    try:
        assert verification.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 2
        assert verification.execute("SELECT COUNT(*) FROM component_sets").fetchone()[0] == 2
        old = Repository(verification).get_component_set(CHANNEL_ID, "1537000000000000001")
        current = Repository(verification).get_component_set(CHANNEL_ID, "1537000000000000002")
        assert old is not None and old.allowed_actions == ["approve", "view-evidence", "refresh"]
        assert current is not None and current.allowed_actions == [
            "approve",
            "reject",
            "view-evidence",
            "refresh",
        ]
    finally:
        verification.close()


def test_legacy_review_evidence_falls_back_to_canonical_project_files(tmp_path: Path, monkeypatch) -> None:
    workflow_root = tmp_path / "workflow"
    _write_project(workflow_root)
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    component = ComponentSetRecord(
        component_set_id="set-legacy",
        channel_id=CHANNEL_ID,
        message_id=MESSAGE_ID,
        project_id="vn-ntq-test",
        card_type="review",
        allowed_actions=("approve", "view-evidence", "refresh"),
        expires_at=datetime.now(UTC),
        state_version=0,
    )

    result = _component_read_only(component, "view-evidence")

    assert "curie-dossier.md" in result
    assert "image-inventory.json" in result
