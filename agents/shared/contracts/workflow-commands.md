# Workflow Commands and State Contract

Discord Components v2 are the preferred control surface for review, page, and final cards when the installed OpenClaw capability exposes components. Typed commands remain the canonical fallback and are accepted only after the same actor, project, state, and gate checks.

## Commands

```text
/approve <project_id>
/reject <project_id> <reason>
/request-change <project_id> <note>
/status <project_id>
/page-status <project_id> <page_slug>
/page-done <project_id> <page_slug>
/page-approve <project_id> <page_slug>
/block <project_id> <page_slug> <reason>
/final-confirm <project_id>
```

## Authorization

- `/approve`: Minh or Wien (`620891893659598850`, `859783610625556480`).
- `/reject` and `/request-change`: Minh only (`620891893659598850`).
- `/page-status`, `/page-done`, and `/block`: Minh or Wien (`859783610625556480`) when acting on an assigned task.
- `/page-approve` and `/final-confirm`: Minh or Wien when acting on an assigned page/project. The final transition to `offer-ready` requires both final confirmations unless Minh explicitly overrides it.

## Components v2 button allowlists

Every bot-owned card records `channel_id`, `message_id`, `project_id`, `state_version`, `expires_at`, and `component_set_id`. A callback is rejected before coordinator execution when any identity, state, expiry, or gate check fails.

| Card | Button | Allowed actors | Typed fallback |
|---|---|---|---|
| review | Approve | Minh `620891893659598850`, Wien `859783610625556480` | `/approve <project_id>` |
| review | Reject | Minh `620891893659598850` | `/reject <project_id> <reason>` |
| review | Request changes | Minh `620891893659598850` | `/request-change <project_id> <note>` |
| page | Page approve | Assigned Minh/Wien | `/page-approve <project_id> <page_slug>` |
| final | Final confirm | Minh/Wien, one confirmation per actor | `/final-confirm <project_id>` |

When components are unsupported or stale, refresh the card if possible and show the exact typed fallback. Never treat a reaction or an unverified button payload as approval.

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

## Natural-language discovery

The Vietnamese intent contract for `discuss` is [discuss-intents.md](discuss-intents.md). It is not an approval command; it only requests one fresh Curie discovery run and leaves the resulting project in `review`.

The detailed Curie output and image rules are in [curie-report.md](curie-report.md). Delivery is a two-message sequence: full/compact review post to `1536658476288450630`, then Vietnamese acknowledgment in `1533645084229369996`.
