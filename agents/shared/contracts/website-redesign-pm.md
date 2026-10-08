# Website Redesign — Project PM Contract

This contract is for turning an approved Website Brief package into executable page work. Read it with `website-redesign-policy.md`, `QUALITY-GATES.md`, `VIETNAMESE-LANGUAGE-POLICY.md`, `website-to-pm.yaml`, and `CHECKLIST-TEMPLATE.md`.

## Mission

Track every page, its content/design/QA state, owner, blocker, next action, and reminder without silently marking work complete. The PM agent coordinates delivery; it does not rewrite evidence or design decisions.

## Project record

```yaml
project_id:
business_name:
website_url:
status: planned|active|blocked|review|offer-ready|archived
approved_by:
approved_at:
design_brief_path:
design_genome_path:
page_blueprints_path:
pages: []
owners:
milestones: []
last_update:
next_reminder:
final_confirmations:
```

## Workflow

```text
approved redesign brief
  → create project record
  → split page blueprints into page tasks
  → assign owner and target day
  → track content/design/QA states
  → remind Minh/Wien with exact missing items
  → require page approvals
  → require Minh + Wien final confirmation
  → package evidence and send offer-ready handoff
```

Each page task tracks strategy, copy, claims/evidence, content sections, SEO, visual/design, responsive, interaction/CTA, QA, owner, target day, dependencies, reviewers, evidence links, and current status.

## Reminder and approval rules

- A reminder states project/page, status, owner, exact missing checklist items, next action, last update, and how to mark it done.
- Do not remind pages already `approved` or projects already `offer-ready`.
- Review and page callbacks must be checked against `channel_id`, `message_id`, `project_id`, `state_version`, expiry, and actor allowlist. If components are unavailable, use the typed fallbacks defined in `workflow-commands.md`.
- Narrative, reminders, and handoffs are Vietnamese; preserve command syntax, IDs, URLs, state names, and schema keys.

## Page and final gates

A page is complete only when content, evidence labels, CTA/contact path, SEO structure, responsive behavior, assets, accessibility, QA, owner completion, and required stakeholder approval are present.

Move to `offer-ready` only when every page is `approved`, no unresolved P0/P1 issue remains, design artifacts and before/after evidence are included, claim URLs/confidence are present, and Minh plus Wien have confirmed completion (unless Minh explicitly overrides that rule).
