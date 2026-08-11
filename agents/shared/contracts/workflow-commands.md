# Workflow Commands and State Contract

Discord native buttons are not available in the current OpenClaw Discord capability report. Use typed commands as the primary control surface and optional reactions only after actor/message checks pass.

## Commands

```text
/approve <project_id>
/reject <project_id> <reason>
/request-change <project_id> <note>
/status <project_id>
/page-status <project_id> <page_slug>
/page-done <project_id> <page_slug>
/block <project_id> <page_slug> <reason>
/final-confirm <project_id>
```

## Authorization

- `/approve`, `/reject`, `/request-change`, and `/final-confirm`: Minh only (`620891893659598850`).
- `/page-status`, `/page-done`, and `/block`: Minh or Wien (`859783610625556480`) when acting on an assigned task.
- The final transition to `offer-ready` requires both final confirmations unless Minh explicitly overrides it.

## Project state

```text
discovered
→ review
→ approved
→ website-brief
→ task
→ stakeholder-review
→ offer-ready
```

Reject path:

```text
review → rejected
```

## Page state

```text
planned
→ content-draft
→ content-ready
→ design-ready
→ qa-needed
→ stakeholder-review
→ approved
```

## Reminder rule

The reminder job runs every 30 minutes but only sends when an active page has a missing next action, a blocker, or a stale update. It must include project, page, owner, exact missing checklist items, next action, and last update time.
