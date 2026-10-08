"""Read-only SQLite access for dashboard projections."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from openclaw_web.lead_contracts import (
    ALLOWLISTED_SCREENSHOT_ASSETS,
    PortfolioEntry,
    PortfolioEntryState,
)

from .models import DashboardEvent, DashboardLead, DashboardPage, DashboardRun


class DashboardDataUnavailable(RuntimeError):
    """Raised when the dashboard cannot read an existing state database."""


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("limit must be an integer")
    return max(1, min(value, 100))


_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_SAFE_TIMESTAMP = re.compile(r"^[0-9T:+.Z-]{1,64}$")
_PROJECT_LEAD_STATES = frozenset(state.value for state in PortfolioEntryState)


def _safe_code(value: object, *, fallback: str = "unavailable") -> str:
    candidate = str(value)
    return candidate if _SAFE_CODE.fullmatch(candidate) else fallback


def _safe_timestamp(value: object) -> str:
    candidate = str(value)
    return candidate if _SAFE_TIMESTAMP.fullmatch(candidate) else "unavailable"


class DashboardReadRepository:
    """Open the workflow database read-only and expose allowlisted projections."""

    def __init__(self, db_path: Path, *, asset_root: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.asset_root = (asset_root or self.db_path.parent / "artifacts").resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.is_file():
            raise DashboardDataUnavailable("state database is unavailable")
        resolved = self.db_path.resolve()
        uri = f"file:{resolved.as_posix()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error as error:
            raise DashboardDataUnavailable("state database cannot be opened read-only") from error
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        except sqlite3.Error as error:
            raise DashboardDataUnavailable("state database read failed") from error
        finally:
            connection.close()

    def schema_ready(self) -> bool:
        """Return whether the existing database has the dashboard read schema."""

        required = {
            "runs",
            "candidates",
            "audits",
            "projects",
            "portfolios",
            "portfolio_entries",
            "portfolio_deliveries",
            "dashboard_action_receipts",
            "worklog_events",
        }
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        return required <= {str(row[0]) for row in rows}

    @staticmethod
    def _safe_json(value: object) -> dict[str, Any] | None:
        try:
            payload = json.loads(str(value))
        except (TypeError, ValueError, RecursionError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _safe_stage(item: dict[str, Any]) -> dict[str, Any]:
        stage: dict[str, Any] = {}
        if "stage_id" in item:
            stage_id = _safe_code(item["stage_id"])
            if stage_id != "unavailable":
                stage["stage_id"] = stage_id
        if "stage_name" in item:
            stage_name = _safe_code(item["stage_name"])
            if stage_name != "unavailable":
                stage["stage_name"] = stage_name
        if "status" in item:
            status = _safe_code(item["status"])
            if status != "unavailable":
                stage["status"] = status
        attempt_count = item.get("attempt_count")
        if isinstance(attempt_count, int) and not isinstance(attempt_count, bool) and 0 <= attempt_count <= 5:
            stage["attempt_count"] = attempt_count
        for key in ("started_at", "completed_at"):
            if key in item and item[key] is not None:
                timestamp = _safe_timestamp(item[key])
                if timestamp != "unavailable":
                    stage[key] = timestamp
        if "error" in item and item["error"] is not None:
            error_code = _safe_code(item["error"], fallback="stage_error")
            stage["error"] = error_code
        return stage

    @classmethod
    def _safe_pages(cls, snapshot: dict[str, Any] | None) -> tuple[DashboardPage, ...]:
        if snapshot is None or not isinstance(snapshot.get("pages"), list):
            return ()
        pages: list[DashboardPage] = []
        for item in snapshot["pages"][:50]:
            if not isinstance(item, dict):
                continue
            page_slug = item.get("slug")
            if not isinstance(page_slug, str) or _SAFE_CODE.fullmatch(page_slug) is None:
                continue
            status = _safe_code(item.get("status"), fallback="unavailable")
            owner_value = item.get("owner_id") or item.get("assignee_id") or item.get("owner")
            assigned_actor = None
            if owner_value is not None:
                assigned_actor = _safe_code(owner_value, fallback="unassigned")
                if assigned_actor == "unavailable":
                    assigned_actor = "unassigned"
            checklist_complete = item.get("checklist_complete")
            if not isinstance(checklist_complete, bool):
                checklist_complete = None
            raw_next_action = item.get("next_action")
            next_action = (
                _safe_code(raw_next_action) if isinstance(raw_next_action, str) else None
            )
            pages.append(
                DashboardPage(
                    page_slug=page_slug,
                    status=status,
                    assigned_actor=assigned_actor,
                    checklist_complete=checklist_complete,
                    next_action=next_action,
                )
            )
        return tuple(pages)

    @classmethod
    def _run_from_row(cls, row: sqlite3.Row) -> DashboardRun:
        snapshot = DashboardReadRepository._safe_json(row["snapshot_json"])
        stages: list[dict[str, Any]] = []
        evaluated = 0
        failures: tuple[str, ...] = ()
        if snapshot is not None:
            raw_stages = snapshot.get("stages")
            if not isinstance(raw_stages, list):
                metadata = snapshot.get("metadata")
                raw_stages = metadata.get("lead_stage_outcomes") if isinstance(metadata, dict) else None
            if isinstance(raw_stages, list):
                for item in raw_stages[:100]:
                    if not isinstance(item, dict):
                        continue
                    stage_item = dict(item)
                    stage_id = stage_item.get("stage_id")
                    if "stage_name" not in stage_item and isinstance(stage_id, str):
                        stage_item["stage_name"] = stage_id.split(":", 1)[0]
                    if "error" not in stage_item and "error_code" in stage_item:
                        stage_item["error"] = stage_item["error_code"]
                    stages.append(cls._safe_stage(stage_item))
            raw_evaluated = snapshot.get("evaluated_candidates")
            if isinstance(raw_evaluated, int) and raw_evaluated >= 0:
                evaluated = raw_evaluated
            raw_failures = snapshot.get("provider_failures")
            if isinstance(raw_failures, list):
                failures = tuple(
                    _safe_code(item, fallback="provider_failure")
                    for item in raw_failures[:20]
                    if isinstance(item, str)
                )
        return DashboardRun(
            run_id=_safe_code(row["run_id"]),
            status=_safe_code(row["status"]),
            current_stage=None if row["current_stage"] is None else _safe_code(row["current_stage"]),
            started_at=_safe_timestamp(row["started_at"]),
            completed_at=None if row["completed_at"] is None else _safe_timestamp(row["completed_at"]),
            evaluated_candidates=evaluated,
            provider_failures=failures,
            stages=tuple(stages),
        )

    def list_runs(self, *, limit: int = 20) -> tuple[DashboardRun, ...]:
        bounded = _limit(limit)
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id, status, started_at, completed_at, current_stage, snapshot_json
                FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return tuple(self._run_from_row(row) for row in rows)

    def get_run(self, run_id: str) -> DashboardRun | None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be non-blank")
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, status, started_at, completed_at, current_stage, snapshot_json
                FROM runs WHERE run_id = ?
                """,
                (run_id.strip(),),
            ).fetchone()
        return None if row is None else self._run_from_row(row)

    def _asset_available(self, candidate_id: str, asset_name: str | None) -> bool:
        if asset_name not in ALLOWLISTED_SCREENSHOT_ASSETS:
            return False
        if not _SAFE_CODE.fullmatch(candidate_id):
            return False
        candidate_dir = self.asset_root / candidate_id
        asset = candidate_dir / asset_name
        if candidate_dir.is_symlink() or asset.is_symlink() or not asset.is_file():
            return False
        try:
            root = self.asset_root.resolve(strict=True)
            resolved = asset.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            return False
        return resolved.is_file()

    def _lead_from_row(self, row: sqlite3.Row) -> DashboardLead | None:
        try:
            entry = PortfolioEntry.model_validate_json(str(row["entry_snapshot_json"]))
        except (ValueError, TypeError, RecursionError):
            return None
        row_keys = set(row.keys())
        project_snapshot = (
            self._safe_json(row["project_snapshot_json"])
            if "project_snapshot_json" in row_keys and row["project_snapshot_json"] is not None
            else None
        )
        project_id_value = row["project_id"] if "project_id" in row_keys else None
        project_id = (
            _safe_code(project_id_value)
            if isinstance(project_id_value, str) and _SAFE_CODE.fullmatch(project_id_value)
            else None
        )
        project_version_value = row["project_state_version"] if "project_state_version" in row_keys else None
        project_state_version = (
            project_version_value
            if isinstance(project_version_value, int) and not isinstance(project_version_value, bool) and project_version_value >= 0
            else None
        )
        project_state_value = row["project_state"] if "project_state" in row_keys else None
        project_state = (
            _safe_code(project_state_value)
            if isinstance(project_state_value, str) and _SAFE_CODE.fullmatch(project_state_value)
            else None
        )
        project_lead_state = None
        if project_snapshot is not None:
            raw_lead_state = project_snapshot.get("lead_state")
            if isinstance(raw_lead_state, str) and raw_lead_state in _PROJECT_LEAD_STATES:
                project_lead_state = raw_lead_state
        effective_state = (
            project_state
            if project_state in {"approved", "rejected"}
            else project_lead_state or entry.state.value
        )
        effective_version = (
            project_state_version
            if project_id is not None and project_state_version is not None
            else entry.state_version
        )
        return DashboardLead(
            entry_id=entry.entry_id,
            portfolio_id=entry.portfolio_id,
            candidate_id=entry.candidate_id,
            project_id=project_id,
            project_state_version=project_state_version,
            rank=entry.rank,
            company_name=entry.company_name,
            website_url=entry.website_url,
            score_breakdown={
                "BusinessStrength": entry.business_strength,
                "AgencyFit": entry.agency_fit,
                "DigitalGap": entry.digital_gap,
                "ConversionGap": entry.conversion_gap,
                "UXGap": entry.ux_gap,
                "TrustGap": entry.trust_gap,
                "CommercialOpportunity": entry.commercial_opportunity,
                "Dealability": entry.dealability,
            },
            evidence_confidence=entry.evidence_confidence,
            red_team_verdict=entry.red_team_verdict.value,
            top_issues=entry.top_issues,
            public_contact=entry.public_contact,
            screenshot_url=(
                f"/api/v1/assets/{entry.candidate_id}/{entry.screenshot_asset}"
                if self._asset_available(entry.candidate_id, entry.screenshot_asset)
                else None
            ),
            pages=self._safe_pages(project_snapshot),
            state=effective_state,
            state_version=effective_version,
            next_action=entry.next_action,
        )

    def list_leads(self, *, state: str | None = None, limit: int = 50) -> tuple[DashboardLead, ...]:
        bounded = _limit(limit)
        query = (
            "SELECT pe.snapshot_json AS entry_snapshot_json, project.project_id, "
            "project.state AS project_state, "
            "project.state_version AS project_state_version, "
            "project.snapshot_json AS project_snapshot_json "
            "FROM portfolio_entries AS pe JOIN portfolios AS p ON p.portfolio_id = pe.portfolio_id "
            "LEFT JOIN projects AS project ON project.project_id = ("
            "SELECT project_match.project_id FROM projects AS project_match "
            "WHERE project_match.candidate_id = pe.candidate_id "
            "ORDER BY project_match.state_version DESC, project_match.project_id DESC LIMIT 1) "
        )
        parameters: list[object] = []
        if state:
            query += (
                "WHERE CASE "
                "WHEN project.state IN ('approved', 'rejected') THEN project.state "
                "WHEN json_extract(project.snapshot_json, '$.lead_state') IN "
                "('ranked', 'selected', 'watching', 'rejected', 'awaiting-command', "
                "'approved', 'in-progress', 'completed') "
                "THEN json_extract(project.snapshot_json, '$.lead_state') "
                "ELSE pe.state END = ? "
            )
            parameters.append(state)
        query += "ORDER BY pe.rank, pe.entry_id LIMIT ?"
        parameters.append(bounded)
        with self.connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        values = [self._lead_from_row(row) for row in rows]
        return tuple(value for value in values if value is not None)

    def get_lead(self, target_id: str) -> DashboardLead | None:
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError("target_id must be non-blank")
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT pe.snapshot_json AS entry_snapshot_json, project.project_id,
                       project.state AS project_state,
                       project.state_version AS project_state_version,
                       project.snapshot_json AS project_snapshot_json
                FROM portfolio_entries AS pe
                LEFT JOIN projects AS project ON project.project_id = (
                    SELECT project_match.project_id FROM projects AS project_match
                    WHERE project_match.candidate_id = pe.candidate_id
                    ORDER BY project_match.state_version DESC, project_match.project_id DESC LIMIT 1
                )
                WHERE pe.entry_id = ? OR pe.candidate_id = ?
                ORDER BY pe.rank LIMIT 1
                """,
                (target_id.strip(), target_id.strip()),
            ).fetchone()
        return None if row is None else self._lead_from_row(row)

    @staticmethod
    def _event_from_row(row: sqlite3.Row, *, source: str) -> DashboardEvent | None:
        if source == "receipt":
            return DashboardEvent(
                event_id=str(row["event_id"]),
                event_type="dashboard-action",
                target_id=str(row["target_id"]),
                action=str(row["action"]),
                actor=str(row["actor_id"]),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
            )
        return DashboardEvent(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            target_id=str(row["project_id"]),
            action=None,
            actor=None,
            status=None,
            created_at=str(row["created_at"]),
        )

    def list_events(self, *, limit: int = 50) -> tuple[DashboardEvent, ...]:
        bounded = _limit(limit)
        with self.connection() as connection:
            receipts = connection.execute(
                """
                SELECT event_id, target_id, action, actor_id, status, created_at
                FROM dashboard_action_receipts WHERE event_id IS NOT NULL
                ORDER BY created_at DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
            worklog = connection.execute(
                """
                SELECT event_id, project_id, event_type, created_at
                FROM worklog_events ORDER BY created_at DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        values: list[DashboardEvent] = []
        for row in receipts:
            event = self._event_from_row(row, source="receipt")
            if event is not None:
                values.append(event)
        for row in worklog:
            event = self._event_from_row(row, source="worklog")
            if event is not None:
                values.append(event)
        values.sort(key=lambda value: value.created_at, reverse=True)
        return tuple(values[:bounded])

    def counts(self) -> dict[str, int]:
        with self.connection() as connection:
            counts = {
                "discovered": int(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]),
                "resolved": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM candidates WHERE state NOT IN "
                        "('discovered', 'geofence-rejected', 'duplicate')"
                    ).fetchone()[0]
                ),
                "cheap-filtered": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM candidates WHERE state IN "
                        "('prefiltered', 'audited', 'review-ready', 'unqualified', 'partial', "
                        "'robots-blocked', 'failed-retryable', 'failed-terminal')"
                    ).fetchone()[0]
                ),
                "deep-audited": int(connection.execute("SELECT COUNT(*) FROM audits").fetchone()[0]),
                "ranked": int(connection.execute("SELECT COUNT(*) FROM portfolio_entries").fetchone()[0]),
                "portfolio": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM portfolio_entries WHERE state NOT IN ('rejected')"
                    ).fetchone()[0]
                ),
                "human-approved": int(
                    connection.execute("SELECT COUNT(*) FROM projects WHERE state = 'approved'").fetchone()[0]
                ),
            }
            counts["gate-survivors"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM portfolio_entries WHERE state != 'rejected'"
                ).fetchone()[0]
            )
        return counts


__all__ = ["DashboardDataUnavailable", "DashboardReadRepository"]
