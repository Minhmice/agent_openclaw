# OpenClaw Workflow Quality Gates

This contract is shared by `main`, `curie`, `website-brief`, and `project-pm`. It is intentionally independent of any future coding agent.

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

No defensible candidate means `no_candidate_defensible`, not a fabricated lead.

## Gate 2 — Website Brief

Website Brief may start only from a Minh-approved Curie handoff. Before PM handoff it must:

- independently verify public claims;
- preserve observed/inferred/estimated status;
- produce the complete artifact manifest required by the redesign spec;
- include evidence gaps and unsupported-claim warnings;
- give every page blueprint a role, audience, conversion intent, primary CTA, sections, dependencies, target day, and responsive notes;
- keep source URLs and artifact paths stable.

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

## Gate 4 — Main coordinator

Main must validate channel and actor before every state mutation, use idempotent project IDs, record bot message IDs, and preserve direct links. A duplicate Curie completion must not create a second project or second review post without an explicit new request.

Main owns user-visible delivery. Child agents return results; they do not own cross-channel posting.

## Failure and retry policy

- Preserve the last valid state and worklog event on failure.
- Retry delivery once when a Discord send fails.
- Treat child cleanup/archive errors separately from child result errors.
- Never retry a state mutation blindly; check the project ID and current state first.
- Keep a manual typed-command fallback for every interactive UI.
