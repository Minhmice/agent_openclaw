# Checklist / Project PM Agent Task

## Primary workflow

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

## Project record

Each project needs:

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

## Page task decomposition

Each page becomes an independent task with:

- Strategy checklist.
- Message/copy checklist.
- Evidence/claim checklist.
- Content section checklist.
- SEO structure checklist.
- Visual/design checklist.
- Responsive checklist.
- Interaction/CTA checklist.
- QA checklist.
- Owner.
- Target day.
- Dependencies.
- Reviewers.
- Evidence links.
- Current status.

## Suggested default schedule

Adapt to the number and complexity of pages; do not force dates when the brief requires a different order.

```text
Day 0 — intake, confirm scope, import design brief, create page matrix
Day 1 — homepage content and primary conversion path
Day 2 — flagship product/service page
Day 3 — proof/case/capability page
Day 4 — about/trust, FAQ, or supporting page
Day 5 — contact/quote/booking page
Day 6 — cross-page consistency, SEO, mobile, CTA and link QA
Day 7 — Minh + Wien review, fixes, final package
```

## Page completion gate

A page cannot be marked complete until:

- Content is present and approved.
- Every factual claim has evidence or an explicit estimate label.
- Primary CTA and contact path are clear.
- SEO title/meta/H1/heading structure is complete.
- Desktop/tablet/mobile behavior is checked.
- Images/assets have source or owner.
- Accessibility basics are checked.
- QA issues are resolved or explicitly accepted.
- Page owner marks it done.
- Required stakeholder reviewer approves it.

## Final handoff gate

Move to `offer-ready` only when:

1. Every page is `approved`.
2. No unresolved P0/P1 issue remains.
3. Design system and page blueprint artifacts are included.
4. Before/after screenshots exist.
5. Evidence URLs and claim confidence are included.
6. Minh confirms final completion.
7. Wien confirms final completion.

Then send a compact package to channel `1536659097649422356` with the offer summary, screenshots, page status, opportunity hypothesis, and next CTA.
