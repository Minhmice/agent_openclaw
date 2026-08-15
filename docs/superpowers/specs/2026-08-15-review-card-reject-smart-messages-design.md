# Review Card Reject Button and Smart Result Messages

**Status:** Approved design
**Date:** 2026-08-15
**Scope:** Native Discord review cards and their durable callback bridge

## Decision

Add a one-click `Reject` button to review cards. The button uses a deterministic
default reason and is visible only to Minh, matching the existing workflow
coordinator policy. Enrich callback responses with the action, project, resulting
state, and next step when that information is available.

The change does not add a modal, change the project state machine, grant Wien
reject authority, enable discovery cron, or weaken Discord/OpenClaw or durable
callback authorization.

## Current context

The review card currently renders `Approve`, `View evidence`, and `Refresh`.
The canonical coordinator already supports `lead-reject <project_id> <reason>`
but requires the actor to be Minh. The durable component record for legacy review
delivery also currently lists only `approve`, `view-evidence`, and `refresh` as
allowed actions. The button schema and plugin callback grammar already support
the `reject` action, so the missing work is card construction, durable reason
normalization, and result presentation.

## Goals

1. Let Minh reject a review card with one click and a durable, auditable reason.
2. Keep Wien's current review permissions without exposing a reject button that
   the coordinator would refuse.
3. Make ephemeral responses useful enough to explain what happened and what to
   do next without opening logs or running a fallback command immediately.
4. Preserve idempotency, stale-card protection, actor authorization, and the
   existing rollback/deployment contract.

## Non-goals

- Adding a rejection modal or free-form reason input.
- Changing `cmd_reject` authorization to include Wien.
- Changing project states or adding a new state.
- Changing `/lead-request-change`, page cards, final cards, discovery cron, or
  model/provider configuration.
- Exposing raw IDs, payloads, filesystem paths, or coordinator stderr in Discord.

## Card behavior and authorization

The review card will render these buttons and per-button allowlists:

| Button | Callback | Allowed actors | Style |
| --- | --- | --- | --- |
| Approve | `project:<id>:approve` | Minh, Wien | success |
| Reject | `project:<id>:reject` | Minh | danger |
| View evidence | `project:<id>:view-evidence` | Minh, Wien | secondary |
| Refresh | `project:<id>:refresh` | Minh, Wien | secondary |

The persisted review component set must include all four actions in
`allowed_actions`. The payload's `allowedUsers` remains per button, and the
Python durable service independently recomputes the actor allowlist from the
card type and persisted page assignment context. An actor who forges a reject
callback, or a Wien click routed to reject, receives the normal unauthorized
result and cannot reach the coordinator.

## Default rejection reason

The single canonical reason is:

```text
Không phù hợp với tiêu chí review hiện tại.
```

When a component callback has action `reject` and no reason, the durable service
normalizes that reason before building the fallback command or invoking the
coordinator. The coordinator therefore receives the same non-empty reason on
every one-click reject. Existing explicit CLI rejection reasons remain
unchanged.

## Smart result messages

The callback bridge will keep the existing `status`, `message_vi`, and
`fallback_command` result shape, but generate a more informative Vietnamese
`message_vi` from the durable result and the post-action project snapshot.

| Result | Message contract |
| --- | --- |
| Accepted approve | `Đã duyệt <project_id>. Trạng thái mới: <state>. Bước tiếp theo: Website Brief.` |
| Accepted reject | `Đã từ chối <project_id>. Lý do: Không phù hợp với tiêu chí review hiện tại. Trạng thái mới: rejected.` |
| Read-only refresh/evidence | Identify the project, action, current state, and state version; do not claim a mutation. |
| Already processed | State that the action was already recorded and show the current state when available. |
| Stale/expired | Explain that the card is old or expired and instruct the user to press `Refresh`. |
| Unauthorized | State that the actor cannot perform that action; do not expose policy internals. |
| Blocked | State that checklist/P0/P1 gates prevent the action and point to the relevant next workflow step. |
| Unknown/unavailable | State that the card is unavailable and include the typed fallback command. |

Messages must be bounded, Vietnamese, and free of coordinator stderr or secret
configuration. If the project snapshot cannot be read after an accepted action,
the bridge falls back to a truthful action-specific message rather than
inventing a state or version.

## Data flow

```text
Discord click
  -> OpenClaw component allowedUsers + guild/channel checks
  -> plugin envelope validation
  -> Python callback lookup by durable message_id
  -> canonical project/action/state validation
  -> reject reason normalization (reject only)
  -> workflow coordinator action
  -> SQLite action claim + project state synchronization
  -> bounded smart Vietnamese response
```

The plugin remains a transport/trust boundary. The Python callback remains the
authority for actor, message identity, component state, action, idempotency,
and coordinator mutation.

## Error and idempotency rules

- The default reject reason is applied before the idempotency key is claimed.
- A duplicate reject by Minh returns `already-processed` or the coordinator's
  existing idempotent result; it must not create a second state transition.
- A stale card cannot be refreshed into an implicit reject or approve.
- A Wien reject attempt must fail before coordinator execution.
- The plugin's existing bounded reason-code diagnostics may remain enabled, but
  only fixed reason codes may be logged; no Discord IDs or callback payloads.

## Test strategy

Tests are written first for each behavior and must be observed failing before
implementation:

1. `build_review_card` includes `Reject`, with Minh-only `allowedUsers`, danger
   style, and the canonical callback data.
2. Legacy review delivery persists `reject` in `allowed_actions`.
3. A one-click reject normalizes and passes the default reason to the
   coordinator; a Wien reject is unauthorized and never reaches it.
4. Accepted approve/reject/read-only/stale/blocked/already-processed results
   render the appropriate bounded Vietnamese message.
5. The plugin accepts the new reject callback grammar and continues rejecting
   malformed envelopes.
6. Existing component, runtime, plugin, lint, type, and compile checks remain
   green.

Live acceptance is performed with a real Discord actor after deployment:

- Minh clicks `Reject` once and the project transitions to `rejected` with the
  default reason.
- Wien's reject control is not rendered; if a stale/forged interaction is
  attempted, it is denied without coordinator mutation.
- Approve, View evidence, and Refresh retain their existing behavior.

## Deployment and rollback

Deployment creates a new immutable release and backup, installs the plugin and
Python wheel, validates the config, and restarts the gateway because OpenClaw
reports that plugin entry changes require restart. The discovery timer remains
`disabled/inactive`. No exec policy, credentials, firewall, model timeout, or
cron setting changes are included.

Rollback uses the installer backup manifest and restores the previous release,
plugin pointer, config, service unit, and environment file, followed by gateway
health and Discord probe checks.

## Acceptance criteria

- Minh sees and can use `Reject`; Wien does not receive reject authority.
- A reject click records the canonical default reason and transitions the
  project to `rejected` exactly once.
- Responses identify the action and resulting state without redundant or
  generic `Đã ghi nhận thao tác.` text.
- Approve/Refresh/View evidence continue to work.
- All automated verification and live health checks pass.

## Self-review

- No unresolved design choices remain after confirming the coordinator's
  Minh-only reject policy.
- The default reason is defined once as a product contract and is applied in
  the durable layer, not trusted to Discord or the plugin.
- The design does not imply that `plugins inspect` reloads a running gateway;
  deployment explicitly includes the required restart.
- Scope is limited to review-card action coverage and response copy.
