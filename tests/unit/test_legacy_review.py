from __future__ import annotations

import json
from pathlib import Path

import pytest

from openclaw_web.review.legacy import (
    LegacyReviewError,
    load_legacy_review_project,
    render_legacy_review_message,
)

PROJECT = {
    "project_id": "vn-ntq-test",
    "business_name": "NTQ Solution",
    "status": "review",
    "state_version": 0,
    "industry": "Software development / IT outsourcing / AI / cloud",
    "target_market": "B2B enterprise buyers in Vietnam and global markets",
    "website": "https://ntq.com.vn/",
    "dossier_file": "curie-dossier.md",
}

DOSSIER = """# Hồ sơ review

Tóm tắt: lead B2B IT services / outsourcing / AI / cloud. Proof dày: 1500+ workforce, 760+ projects, 380+ clients. Site hiện loãng vì nhiều CTA.

Top issues:
- P1: homepage có quá nhiều service/product/case blocks + CTA song song
- P1: contact page trộn form với office/global presence information
- P2: service taxonomy quá rộng cho một conversion story
- P2: location/trust layer nặng hơn mức cần thiết

Evidence:
- https://ntq.com.vn/
- https://ntq.com.vn/vi/lien-he/

Confidence gaps: chưa có screenshot audit, performance, SEO, analytics, conversion data.

Ảnh first-party: 5 asset trong image inventory.

Action:
- /lead-approve vn-ntq-test
"""


def test_render_legacy_review_message_is_compact_and_non_repeating() -> None:
    message = render_legacy_review_message(PROJECT, DOSSIER)

    assert message.count("NTQ Solution") == 1
    assert "**TOP OPPORTUNITIES**" in message
    assert message.count("P1") == 2
    assert "760+ projects" in message
    assert "5 asset" in message
    assert "**CONFIDENCE**" in message
    assert "/lead-approve" not in message
    assert "NTQ review" not in message


def test_load_legacy_review_project_reads_dossier_without_path_escape(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "vn-ntq-test"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(json.dumps(PROJECT), encoding="utf-8")
    (project_dir / "curie-dossier.md").write_text(DOSSIER, encoding="utf-8")

    project, dossier = load_legacy_review_project(project_dir, "vn-ntq-test")

    assert project["project_id"] == "vn-ntq-test"
    assert dossier.startswith("# Hồ sơ review")


@pytest.mark.parametrize(
    ("project", "dossier", "match"),
    [
        ({**PROJECT, "status": "approved"}, DOSSIER, "review state"),
        ({**PROJECT, "website": "file:///tmp/site"}, DOSSIER, "website"),
        (PROJECT, "", "dossier"),
    ],
)
def test_legacy_project_validation_is_bounded(
    tmp_path: Path,
    project: dict[str, object],
    dossier: str,
    match: str,
) -> None:
    project_dir = tmp_path / "projects" / "vn-ntq-test"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (project_dir / "curie-dossier.md").write_text(dossier, encoding="utf-8")

    with pytest.raises(LegacyReviewError, match=match):
        load_legacy_review_project(project_dir, "vn-ntq-test")
