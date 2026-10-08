# Curie Current Task

## Current Lead Intelligence contract

Curie is the discovery and evidence producer for the bounded 17-stage Lead
Intelligence flow. The stage order is:

```text
define_market -> discover -> resolve_entities -> cheap_filter -> business_fit
-> agency_fit -> digital_gap -> deep_audit -> commercial_opportunity -> dealability
-> evidence_verification -> red_team -> score_survivors -> rank -> portfolio_selection
-> human_approval -> redesign_intelligence
```

The qualification gate is:

```text
BusinessStrength >= 60
AND AgencyFit >= 65
AND DigitalGap >= 55
```

Keep all score dimensions and `EvidenceConfidence` separate. `Dealability`
before outreach is an observable proxy only; never output `buyer_intent`,
`engagement`, or another unobserved intent claim. `red_team` must return
`survive`, `downgrade`, or `reject`, and rejected candidates never enter the
portfolio.

The portfolio target is 5 with a hard maximum of 7. Keep 3–4 survivors as-is;
keep only defensible 1–2 with run status `partial`; return
`no_candidate_defensible` when none survive. A selected lead remains waiting
for human action. Curie never starts redesign without a coordinator-accepted
human approval.

## Assignment

Turn [IDEA.md](IDEA.md) into an implementation-ready product spec and phased plan for the lead-mining engine.

## Required first pass

Before writing code:

1. Inspect the repository and root runbook.
2. Propose a narrow MVP boundary.
3. Define phases small enough to build and verify independently.
4. Define JSON schemas and the lead/evidence data model.
5. Define deterministic scoring rules and the boundary between deterministic checks and AI judgment.
6. Define the discovery, crawl, technical-audit, screenshot, review, ranking, and report interfaces.
7. Compare tools/APIs, rate limits, cost assumptions, failure modes, and robots.txt/rate-limit handling.
8. Define acceptance criteria for one qualified lead per day.
9. Identify the minimum user decisions needed before implementation.

## Current user-facing behavior

The MVP market scope is decided and must not be asked again: `đa ngành`, `quanh Hà Nội`, market
ID `hanoi-80km`. Continue with bounded discovery/audit implementation and ask only later unresolved
product decisions one at a time when they affect the next phase.

The current implementation lives under `src/openclaw_web/` and is deployed through `deploy/`; its
production gate still requires public evidence, geofence, deterministic scoring, artifact persistence,
and a real Discord review action before approval or timer enablement.

The live product dashboard is a read projection over workflow state and a
write-capable coordinator adapter. Dashboard actions use bearer-token actor
mapping, state-version compare-and-swap, and idempotency receipts; they do not
bypass the same review gates used by Discord commands.

## Hard constraints

- Discussion and planning first.
- No code until the user approves the plan.
- No cron creation until the user approves the plan.
- No remote OpenClaw modification until explicitly approved.
- No password, token, key, cookie, provider secret, or session data in files or output.
- No unsupported claims based only on the dated baseline.
- Provider failure must produce an explicit partial/terminal result, never a
  fabricated candidate used to fill a portfolio.
