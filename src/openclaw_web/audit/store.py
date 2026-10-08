"""Transactional audit store for pages, evidence, scores, and issues."""

from __future__ import annotations

import hashlib

from openclaw_web.platform.base import (
    BaseStore,
    _canonical_json,
    _canonical_mapping_json,
    _deserialize,
    _require_nonblank,
    _utc_text,
)
from openclaw_web.models import AuditRecord, Evidence, IssueRecord, PageRecord, ScoreRecord


class AuditStore(BaseStore):
    """Audit persistence for pages, scores, issues, and evidence records."""

    def append_evidence(self, evidence: Evidence) -> None:
        snapshot = _canonical_json(evidence)
        self._append_snapshot(
            table="evidence",
            identity_column="evidence_id",
            record_id=evidence.evidence_id,
            model=evidence,
            model_type=Evidence,
            insert_sql="""
                INSERT INTO evidence (
                    evidence_id, candidate_id, evidence_type, captured_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
            """,
            values=(
                evidence.evidence_id,
                evidence.candidate_id,
                evidence.evidence_type,
                _utc_text(evidence.captured_at),
                snapshot,
            ),
        )

    def append_score(self, score: ScoreRecord) -> None:
        normalized_score = ScoreRecord.model_validate(
            {
                **score.model_dump(mode="python"),
                "evidence_ids": sorted(set(score.evidence_ids)),
            }
        )
        snapshot = _canonical_json(normalized_score)
        identity_json = _canonical_mapping_json(
            {
                "evidence_ids": list(normalized_score.evidence_ids),
                "inputs": normalized_score.model_dump(mode="json")["inputs"],
                "rubric_version": normalized_score.rubric_version,
                "score_name": normalized_score.score_name,
            }
        )
        record_id = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
        self._append_snapshot(
            table="scores",
            identity_column="record_id",
            record_id=record_id,
            model=normalized_score,
            model_type=ScoreRecord,
            insert_sql="""
                INSERT INTO scores (record_id, score_name, rubric_version, snapshot_json)
                VALUES (?, ?, ?, ?)
            """,
            values=(
                record_id,
                normalized_score.score_name,
                normalized_score.rubric_version,
                snapshot,
            ),
        )

    def append_issue(self, issue: IssueRecord) -> None:
        snapshot = _canonical_json(issue)
        self._append_snapshot(
            table="issues",
            identity_column="issue_id",
            record_id=issue.issue_id,
            model=issue,
            model_type=IssueRecord,
            insert_sql="""
                INSERT INTO issues (issue_id, candidate_id, severity, snapshot_json)
                VALUES (?, ?, ?, ?)
            """,
            values=(issue.issue_id, issue.candidate_id, issue.severity.value, snapshot),
        )

    def append_page(self, page: PageRecord) -> None:
        snapshot = _canonical_json(page)
        self._append_snapshot(
            table="pages",
            identity_column="page_id",
            record_id=page.page_id,
            model=page,
            model_type=PageRecord,
            insert_sql="""
                INSERT INTO pages (
                    page_id, candidate_id, page_url, page_type, snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
            """,
            values=(
                page.page_id,
                page.candidate_id,
                str(page.page_url),
                page.page_type,
                snapshot,
            ),
        )

    def get_pages(self, candidate_id: str) -> tuple[PageRecord, ...]:
        identity = _require_nonblank(candidate_id, "candidate_id")
        rows = self.connection.execute(
            "SELECT snapshot_json FROM pages WHERE candidate_id = ? ORDER BY page_id",
            (identity,),
        ).fetchall()
        return tuple(_deserialize(PageRecord, str(row["snapshot_json"])) for row in rows)

    def append_audit(self, audit: AuditRecord) -> None:
        snapshot = _canonical_json(audit)
        self._append_snapshot(
            table="audits",
            identity_column="audit_id",
            record_id=audit.audit_id,
            model=audit,
            model_type=AuditRecord,
            insert_sql="""
                INSERT INTO audits (
                    audit_id, candidate_id, source_run_id, status, snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
            """,
            values=(
                audit.audit_id,
                audit.candidate_id,
                audit.source_run_id,
                str(audit.status),
                snapshot,
            ),
        )

    def get_audit(self, audit_id: str) -> AuditRecord | None:
        identity = _require_nonblank(audit_id, "audit_id")
        row = self.connection.execute(
            "SELECT snapshot_json FROM audits WHERE audit_id = ?",
            (identity,),
        ).fetchone()
        return None if row is None else _deserialize(AuditRecord, str(row["snapshot_json"]))
