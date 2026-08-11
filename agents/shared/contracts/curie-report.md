# Curie Detailed Report Contract

Curie discovery output has two layers:

1. A detailed project artifact under the project directory.
2. A compact Discord review message that links or points to the artifact.

## Required report sections

```text
report_status: candidate_found | no_candidate_defensible | partial
project_id
business_identity
executive_summary
business_strength_evidence
website_scope
website_audit_by_page
conversion_opportunity
top_issues
recommended_redesign_angle
evidence_matrix
image_evidence
confidence_gaps
next_action
```

Every claim must be marked as `observed`, `inferred`, `estimated`, or `unverified` and must include a source URL when one exists.

## Issue detail

Each issue should include:

```yaml
id: ISSUE-01
severity: P0|P1|P2|P3
page_url: https://example.com/page
observation: Vietnamese description of what is visible/fetched
business_impact: Vietnamese conversion or trust hypothesis
evidence_urls: []
confidence: high|medium|low
recommended_direction: Vietnamese action
```

Do not claim a measured conversion or revenue loss without analytics. Use `suy luận` or `ước tính` explicitly.

## Image evidence

Prefer first-party public assets from the candidate website. Include up to 5 useful images when available:

```yaml
asset_id: image-01
type: hero|product|project|logo|team|screenshot|og-image
page_url: https://example.com/page
image_url: https://example.com/image.jpg
alt_text_vi: Mô tả ngắn bằng tiếng Việt
what_it_shows: Vietnamese description
why_relevant: Vietnamese explanation of business/design relevance
source_owner: first-party|third-party|unknown
rights_status: source-public-unverified|licensed|unknown
confidence: high|medium|low
```

Rules:

- Never invent an image URL or claim ownership.
- Do not download or attach private, login-gated, cookie-backed, or sensitive images.
- If the agent cannot inspect a screenshot, use first-party public image URLs and state that a visual screenshot audit is still pending.
- If no defensible image exists, return `image_evidence: []` with the reason; do not force decorative images.

## Handoff and Discord delivery

- Save the full dossier and an `image-inventory.json` under the project directory.
- The review-channel message must stay concise enough for Discord, include project ID, summary, top issues, confidence gaps, evidence links, and the approval commands.
- `main` owns delivery to `shit-that-could-cooking` (`1536658476288450630`) and the completion acknowledgment to `discuss` (`1533645084229369996`). Curie must not assume its sub-agent announce is the final channel handoff.
