#!/usr/bin/env python3
"""Small, dependency-free state coordinator for the OpenClaw workflow."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(os.environ.get("OPENCLAW_WORKFLOW_ROOT", "/home/minhmice/.openclaw/workflow"))
PROJECTS = ROOT / "projects"
STATE = ROOT / "state"
WORKLOG = ROOT / "WORKLOG.md"
MINH_ID = "620891893659598850"
WIEN_ID = "859783610625556480"
REVIEW_CHANNEL = "1536658476288450630"
TASK_CHANNEL = "1533643473486348458"
OFFER_CHANNEL = "1536659097649422356"
PROJECT_STATES = {
    "discovered",
    "review",
    "approved",
    "website-brief",
    "task",
    "stakeholder-review",
    "offer-ready",
    "rejected",
    "archived",
}
PAGE_STATES = {
    "planned",
    "content-draft",
    "content-ready",
    "design-ready",
    "qa-needed",
    "stakeholder-review",
    "approved",
    "blocked",
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def safe_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not value or len(value) > 80:
        raise ValueError("project_id must contain 1-80 safe slug characters")
    return value


def project_path(project_id: str) -> Path:
    return PROJECTS / safe_id(project_id) / "project.json"


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
        os.chmod(path, 0o640)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load(project_id: str) -> dict[str, Any]:
    path = project_path(project_id)
    if not path.exists():
        raise ValueError(f"unknown project: {project_id}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def log(event: str, project_id: str = "", detail: str = "") -> None:
    WORKLOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"- {now()} event={event}"
    if project_id:
        line += f" project={project_id}"
    if detail:
        line += f" detail={detail.replace(chr(10), ' ')[:500]}"
    with WORKLOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def save(project: dict[str, Any]) -> None:
    project["last_update"] = now()
    atomic_write(project_path(project["project_id"]), project)


def require_actor(actor: str, allowed: set[str]) -> None:
    if actor not in allowed:
        raise PermissionError("actor is not authorized for this command")


def find_page(project: dict[str, Any], page_slug: str) -> dict[str, Any]:
    page = next((item for item in project.get("pages", []) if item.get("slug") == page_slug), None)
    if page is None:
        raise ValueError("unknown page slug")
    return page


def actor_is_assigned(page: dict[str, Any], actor: str) -> bool:
    """Accept stable actor IDs, or the human-readable owner labels PM may emit."""
    owner_id = str(page.get("owner_id") or page.get("assignee_id") or "")
    owner = str(page.get("owner") or page.get("assignee") or "").strip().lower()
    if owner_id:
        return owner_id == actor
    if owner in {"minh", "minhmice"}:
        return actor == MINH_ID
    if owner in {"wien", "859783610625556480"}:
        return actor == WIEN_ID
    return False


def require_assigned_actor(page: dict[str, Any], actor: str) -> None:
    if not actor_is_assigned(page, actor):
        raise PermissionError("actor is not assigned to this page")


def checklist_is_complete(page: dict[str, Any]) -> bool:
    if page.get("checklist_complete") is True:
        return True
    checklist = page.get("checklist")
    if not isinstance(checklist, list) or not checklist:
        return False
    complete_states = {"done", "complete", "completed", "approved", "pass", "passed"}
    return all(
        isinstance(item, dict) and str(item.get("status", "")).lower() in complete_states
        for item in checklist
    )


def cmd_init(args: argparse.Namespace) -> None:
    with Path(args.input).open(encoding="utf-8") as handle:
        project = json.load(handle)
    project_id = safe_id(project.get("project_id", ""))
    project["project_id"] = project_id
    project.setdefault("status", "review")
    project.setdefault("pages", [])
    project.setdefault("final_confirmations", {})
    project.setdefault("created_at", now())
    project.setdefault("last_update", now())
    if project["status"] not in PROJECT_STATES:
        raise ValueError("invalid project status")
    atomic_write(project_path(project_id), project)
    log("project-init", project_id)
    print(json.dumps(project, ensure_ascii=False, indent=2))


def cmd_approve(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID})
    project = load(args.project_id)
    if project.get("status") != "review":
        raise ValueError("project must be in review state")
    project["status"] = "approved"
    project["approved_by"] = args.actor
    project["approved_at"] = now()
    save(project)
    log("review-approved", project["project_id"], f"actor={args.actor}")
    print(json.dumps(project, ensure_ascii=False, indent=2))


def cmd_reject(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID})
    project = load(args.project_id)
    if project.get("status") != "review":
        raise ValueError("project must be in review state")
    reason = args.reason.strip()
    if not reason:
        raise ValueError("rejection reason is required")
    project["status"] = "rejected"
    project["rejected_by"] = args.actor
    project["rejected_at"] = now()
    project["rejection_reason"] = reason
    save(project)
    log("review-rejected", project["project_id"], f"actor={args.actor} reason={reason}")
    print(json.dumps(project, ensure_ascii=False, indent=2))


def cmd_request_change(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID})
    project = load(args.project_id)
    if project.get("status") in {"rejected", "archived", "offer-ready"}:
        raise ValueError("cannot request changes on a terminal project")
    note = args.note.strip()
    if not note:
        raise ValueError("change note is required")
    project.setdefault("change_requests", []).append({"actor": args.actor, "at": now(), "note": note})
    project["status"] = "review"
    save(project)
    log("change-requested", project["project_id"], f"actor={args.actor} note={note}")
    print(json.dumps(project, ensure_ascii=False, indent=2))


def cmd_page_status(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID, WIEN_ID})
    project = load(args.project_id)
    page = find_page(project, args.page_slug)
    require_assigned_actor(page, args.actor)
    print(json.dumps(page, ensure_ascii=False, indent=2))


def cmd_page_done(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID, WIEN_ID})
    project = load(args.project_id)
    page = find_page(project, args.page_slug)
    require_assigned_actor(page, args.actor)
    if page.get("status") in {"approved", "blocked"}:
        raise ValueError("page cannot be marked done from its current state")
    page["owner_done_by"] = args.actor
    page["owner_done_at"] = now()
    page["status"] = "stakeholder-review"
    save(project)
    log("page-owner-done", project["project_id"], f"page={args.page_slug} actor={args.actor}")
    print(json.dumps(page, ensure_ascii=False, indent=2))


def cmd_page_approve(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID, WIEN_ID})
    project = load(args.project_id)
    page = find_page(project, args.page_slug)
    require_assigned_actor(page, args.actor)
    if page.get("status") != "stakeholder-review":
        raise ValueError("page must be in stakeholder-review state")
    if not checklist_is_complete(page):
        raise ValueError("page checklist is not complete")
    if page.get("unresolved_priority") in {"P0", "P1"} or page.get("unresolved_p0") or page.get("unresolved_p1"):
        raise ValueError("page has unresolved P0/P1 issues")
    page["status"] = "approved"
    page["approved_by"] = args.actor
    page["approved_at"] = now()
    save(project)
    log("page-approved", project["project_id"], f"page={args.page_slug} actor={args.actor}")
    print(json.dumps(page, ensure_ascii=False, indent=2))


def cmd_block(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID, WIEN_ID})
    project = load(args.project_id)
    page = find_page(project, args.page_slug)
    require_assigned_actor(page, args.actor)
    reason = args.reason.strip()
    if not reason:
        raise ValueError("block reason is required")
    page["status"] = "blocked"
    page["blocked_by"] = args.actor
    page["blocked_at"] = now()
    page["blocked_reason"] = reason
    save(project)
    log("page-blocked", project["project_id"], f"page={args.page_slug} actor={args.actor} reason={reason}")
    print(json.dumps(page, ensure_ascii=False, indent=2))


def cmd_final_confirm(args: argparse.Namespace) -> None:
    require_actor(args.actor, {MINH_ID, WIEN_ID})
    project = load(args.project_id)
    confirmations = project.setdefault("final_confirmations", {})
    confirmations[args.actor] = now()
    pages = project.get("pages", [])
    all_pages_approved = bool(pages) and all(page.get("status") == "approved" for page in pages)
    both_confirmed = MINH_ID in confirmations and WIEN_ID in confirmations
    if all_pages_approved and both_confirmed:
        project["status"] = "offer-ready"
        project["offer_ready_at"] = now()
        project["offer_channel"] = OFFER_CHANNEL
        log("offer-ready", project["project_id"], "both final confirmations received")
    else:
        project["status"] = "stakeholder-review"
        log("final-confirmation", project["project_id"], f"actor={args.actor}")
    save(project)
    print(json.dumps(project, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(load(args.project_id), ensure_ascii=False, indent=2))


def cmd_due(args: argparse.Namespace) -> None:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=args.stale_minutes)
    due: list[dict[str, Any]] = []
    if not PROJECTS.exists():
        print("[]")
        return
    for path in sorted(PROJECTS.glob("*/project.json")):
        with path.open(encoding="utf-8") as handle:
            project = json.load(handle)
        if project.get("status") in {"offer-ready", "rejected", "archived"}:
            continue
        last = project.get("last_update")
        stale = True
        if last:
            try:
                stale = dt.datetime.fromisoformat(last).replace(tzinfo=dt.timezone.utc) <= cutoff
            except ValueError:
                stale = True
        pending = [
            {"slug": page.get("slug"), "status": page.get("status"), "owner": page.get("owner"), "next_action": page.get("next_action"), "blocked_reason": page.get("blocked_reason")}
            for page in project.get("pages", [])
            if page.get("status") != "approved"
        ]
        if stale or pending:
            due.append({"project_id": project.get("project_id"), "status": project.get("status"), "last_update": last, "pending_pages": pending})
    print(json.dumps(due, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--input", required=True)
    init.set_defaults(func=cmd_init)
    approve = sub.add_parser("approve")
    approve.add_argument("project_id")
    approve.add_argument("--actor", required=True)
    approve.set_defaults(func=cmd_approve)
    reject = sub.add_parser("reject")
    reject.add_argument("project_id")
    reject.add_argument("reason")
    reject.add_argument("--actor", required=True)
    reject.set_defaults(func=cmd_reject)
    request_change = sub.add_parser("request-change")
    request_change.add_argument("project_id")
    request_change.add_argument("note")
    request_change.add_argument("--actor", required=True)
    request_change.set_defaults(func=cmd_request_change)
    page_status = sub.add_parser("page-status")
    page_status.add_argument("project_id")
    page_status.add_argument("page_slug")
    page_status.add_argument("--actor", required=True)
    page_status.set_defaults(func=cmd_page_status)
    done = sub.add_parser("page-done")
    done.add_argument("project_id")
    done.add_argument("page_slug")
    done.add_argument("--actor", required=True)
    done.set_defaults(func=cmd_page_done)
    page_approve = sub.add_parser("page-approve")
    page_approve.add_argument("project_id")
    page_approve.add_argument("page_slug")
    page_approve.add_argument("--actor", required=True)
    page_approve.set_defaults(func=cmd_page_approve)
    block = sub.add_parser("block")
    block.add_argument("project_id")
    block.add_argument("page_slug")
    block.add_argument("reason")
    block.add_argument("--actor", required=True)
    block.set_defaults(func=cmd_block)
    final = sub.add_parser("final-confirm")
    final.add_argument("project_id")
    final.add_argument("--actor", required=True)
    final.set_defaults(func=cmd_final_confirm)
    status = sub.add_parser("status")
    status.add_argument("project_id")
    status.set_defaults(func=cmd_status)
    due = sub.add_parser("due-reminders")
    due.add_argument("--stale-minutes", type=int, default=30)
    due.set_defaults(func=cmd_due)
    return root


if __name__ == "__main__":
    try:
        arguments = parser().parse_args()
        arguments.func(arguments)
    except (ValueError, PermissionError, FileNotFoundError) as error:
        raise SystemExit(f"workflow error: {error}")
