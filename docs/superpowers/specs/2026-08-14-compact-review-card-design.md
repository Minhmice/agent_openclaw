# Compact Review Card Design

## Decision

Review delivery uses one Discord Components v2 message with a compact Vietnamese summary and native actions. The summary follows option A: one title, one proof line, at most three top opportunities, one evidence line, and one confidence note. Detailed dossier content remains in the canonical workflow project and is exposed through the existing `View evidence` action.

The legacy Curie project path is bridged into the same durable web-audit repository and outbox used by production discovery. This gives legacy projects a `projects` row, idempotent `deliveries` row, and bot-owned `component_sets` row before a callback can execute. Existing text messages are retained during the first rollout; the new card is additive and can be verified before any cleanup.

## Message contract

The review delivery envelope may contain a bounded `message` string. `OpenClawAgentTransport` uses it as the Discord message text and retains the existing fallback `Duyệt lead <project_id>` for older payloads. Presentation components continue to carry only the action row, so the summary is rendered once and cannot be duplicated by a second text block.

Review actions remain:

- `Approve`: native button, allowed for Minh `620891893659598850` and Wien `859783610625556480`.
- `View evidence`: native read-only button, same allowlist.
- `Refresh`: native read-only button, same allowlist.
- `Reject` and `Request change`: typed fallbacks until a reason modal is supported.

The server-side callback validation remains authoritative for actor, channel, message identity, project, state version, expiry, and idempotency.

## Legacy bridge

`openclaw-web legacy-review --project-id <id> --json` reads only the canonical project directory under `OPENCLAW_WORKFLOW_ROOT` (default `~/.openclaw/workflow`). It validates that the project is in `review`, parses the existing dossier into the compact summary, upserts a candidate and review project in the web state DB, writes a bounded `review-card.json` artifact, enqueues `review:<project_id>` once, and dispatches through `OutboxWorker(OpenClawAgentTransport)`. The command returns only project, delivery, component, and Discord identity fields; it never prints dossier or credential content.

Legacy evidence lookup falls back to the canonical project directory when no generated `artifact_dir/evidence` tree exists. This keeps `View evidence` useful for a Curie dossier without inventing audit metrics.

## Failure and idempotency behavior

- Missing project, non-review state, invalid website URL, or malformed dossier returns a bounded non-zero CLI result without sending.
- A sent `review:<project_id>` delivery is returned without another Discord send.
- A failed/pending delivery is handled by the existing outbox retry policy.
- Existing workflow text messages are not deleted or edited by this change.

## Verification

Unit tests cover compact rendering, message selection/fallback, legacy payload construction, idempotent enqueue, and legacy evidence lookup. Integration tests exercise a temporary workflow root and SQLite repository with a fake transport, then verify the stored component set and callback authorization path. Remote verification checks the new card message, `deliveries`/`component_sets` rows, and no duplicate delivery; human button clicks remain a separate acceptance gate.
