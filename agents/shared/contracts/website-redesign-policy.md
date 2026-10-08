# Website Redesign — Shared Policy

This is the small policy layer shared by Curie, Website Brief, and Project PM. Read the role contract for the current stage; do not load every role contract for every task.

## Evidence and truth

- Use public, attributable evidence only. Obey `robots.txt`, rate limits, access boundaries, and provider terms.
- Preserve the source URL, page URL, capture time, and artifact path for every material observation.
- Label claims as `observed`, `inferred`, or `estimated` whenever the evidence does not fully establish them.
- Never invent revenue, testimonials, clients, awards, certifications, conversion metrics, or brand claims.
- Separate business facts from an opportunity hypothesis. A hypothesis is not proof of lost revenue.
- If evidence is unavailable or contradictory, record the gap or conflict instead of filling it with plausible text.

## Language and identifiers

- Narrative, status, review messages, reminders, and handoffs to Minh/Wien are written in Vietnamese.
- Preserve `project_id`, URLs, evidence URLs, file names, artifact names, command syntax, state names, channel IDs, actor IDs, and JSON/YAML keys exactly.
- Never place a password, token, private key, cookie, provider secret, or session content in an artifact, prompt, log, or message.

## Operating boundary

- Work only on an approved project and only within the role's stage.
- Do not modify the remote OpenClaw host, create cron jobs, publish messages, or change infrastructure without explicit approval for that action.
- Keep discovery and extraction bounded. On provider failure, return a bounded `partial` or `no_candidate_defensible` result with the reason.
- Keep raw evidence separate from generated interpretation so a reviewer can trace a decision back to a source.

## Anti-drift rules

- Every visual or delivery decision must point back to business truth, evidence, or an explicit stakeholder decision.
- Keep the distinction between `KEEP`, `EVOLVE`, and `RETIRE` decisions; do not erase valuable brand invariants for visual novelty.
- Do not use generic gradients, random blobs, endless identical card grids, or decorative motion without a user or business purpose.
- Do not mark an artifact, page, or project complete while required evidence, approvals, or QA gates are missing.

## Handoff minimum

Every cross-agent handoff includes the project slug/ID, website URL, current state, artifact paths, evidence/confidence gaps, owner of the next action, and the exact next transition or command.
