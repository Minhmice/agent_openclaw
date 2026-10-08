# OpenClaw Workflow Quality Gates

This contract is shared by `main`, `curie`, `website-brief`, and `project-pm`. It is intentionally independent of any future coding agent.

## Lead Intelligence flow (P0–P4)

The canonical lead run has these stable stages, in this order:

```text
define_market -> discover -> resolve_entities -> cheap_filter -> business_fit
-> agency_fit -> digital_gap -> deep_audit -> commercial_opportunity -> dealability
-> evidence_verification -> red_team -> score_survivors -> rank -> portfolio_selection
-> human_approval -> redesign_intelligence
```

`redesign_intelligence` is a post-approval boundary. Its sub-stages are
`business_truth`, `content_inventory`, `visual_dna`, `keep_evolve_retire`,
`redesign_mode`, `design_direction`, `prototype`, `design_system`, and
`page_blueprints`.

The qualification gate is conjunctive, never an average:

```text
BusinessStrength >= 60
AND AgencyFit >= 65
AND DigitalGap >= 55
```

Keep independent values for `BusinessStrength`, `AgencyFit`, `DigitalGap`,
`ConversionGap`, `UXGap`, `TrustGap`, `CommercialOpportunity`, `Dealability`,
and `EvidenceConfidence`. Before outreach, `buyer_intent`, `engagement`, and
equivalent intent claims are forbidden. `Dealability` may use only observable
pre-outreach proxies.

Every stage outcome is typed and resumable. It includes `stage_id`, `run_id`,
`producer_role`, `status`, `attempt_count`, timestamps, refs, evidence IDs,
confidence, `error_code`, and an allowlisted `checkpoint`. Checkpoints never
contain credentials, cookies, session content, private keys, raw logs, or raw
configuration.

The supported result statuses are `complete`, `partial`,
`no_candidate_defensible`, `failed_retryable`, and `failed_terminal`, plus
`blocked` when a human/coordinator gate is waiting. Provider failure or missing
evidence returns an explicit partial/terminal result; it never creates a
synthetic candidate to meet a quota.

`RedTeamVerdict` is one of `survive`, `downgrade`, or `reject`. A rejected lead
cannot enter the portfolio. Portfolio selection is bounded to 3–7 entries, with
a default target of 5: keep 5 when available, keep all 3–4, and keep only
defensible 1–2 while marking the run `partial`. Zero survivors is
`no_candidate_defensible`.

## Gate 1 — Curie discovery

Curie may create a review candidate only when:

- exactly one stable `project_id` is produced;
- the website is public and crawlable under robots/rate-limit rules;
- the business-strength case has public evidence;
- the website-weakness case has page-level evidence;
- every money/conversion claim is marked `inferred`, `estimated`, or `unverified` when not measured;
- at least three useful evidence URLs exist, including the candidate website;
- confidence gaps are explicit;
- active/review project duplicates were checked;
- the output is a detailed Vietnamese dossier plus machine-readable handoff.

For a Lead Intelligence run, Gate 1 also requires the conjunctive score gate,
an explicit `EvidenceConfidence`, and a `red_team` verdict. A selected lead
remains waiting for human action; Curie must not infer intent or start redesign
after selection.

No defensible candidate means `no_candidate_defensible`, not a fabricated lead.

## Gate 2 — Website Brief

Website Brief may start only from a Minh- or Wien-approved Curie handoff. Before PM handoff it must:

- independently verify public claims;
- preserve observed/inferred/estimated status;
- produce the complete artifact manifest required by the redesign spec;
- include evidence gaps and unsupported-claim warnings;
- give every page blueprint a role, audience, conversion intent, primary CTA, sections, dependencies, target day, and responsive notes;
- keep source URLs and artifact paths stable.

The handoff is valid only after a portfolio entry has crossed
`human_approval`. Preserve the approved lead's evidence refs, confidence gaps,
and selected redesign mode; do not fill missing evidence with invented claims.

An incomplete package remains `website-brief` and is not passed to PM.

## Gate 3 — Project PM

PM may create task state only from a complete Website Brief handoff. Every page/task needs:

- owner or assignee;
- target day;
- dependencies;
- exact next action;
- strategy, copy, evidence, content, SEO, design, responsive, interaction, and QA checklist items;
- reviewer and approval state;
- message tracking when a Discord card is posted.

PM may remind only when an action is due, blocked, or stale. A page cannot be approved without checklist completion and stakeholder review.

Dashboard actions are another input encoding for the same coordinator
transitions. They carry an actor derived from a valid bearer token,
`expected_state_version`, and an `idempotency_key`; the HTTP layer never writes
project state directly. A stale action is rejected and a repeated idempotency
key returns the existing result.

## Gate 4 — Main coordinator

Main must validate channel and actor before every state mutation, use idempotent project IDs, record bot message IDs, and preserve direct links. A duplicate Curie completion must not create a second project or second review post without an explicit new request.

Main owns user-visible delivery. Child agents return results; they do not own cross-channel posting.

Lead portfolios use one idempotent Discord summary per `portfolio_id`. The
summary lists every selected entry (3–7), rank, URL, key scores, evidence
confidence, red-team verdict, dashboard link, and typed fallback command.

## Failure and retry policy

- Preserve the last valid state and worklog event on failure.
- Retry delivery once when a Discord send fails.
- Treat child cleanup/archive errors separately from child result errors.
- Never retry a state mutation blindly; check the project ID and current state first.
- Keep a manual typed-command fallback for every interactive UI.
