
# Review Card Reject Button and Smart Result Messages Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a Minh-only one-click Reject action to review cards and replace generic component responses with truthful action/state-aware Vietnamese messages.

**Architecture:** Keep Discord/OpenClaw component authorization and Python durable callback authority unchanged. Extend the review-card contract with a Minh-only reject button, normalize one deterministic reason in the durable service, and format the final callback response from the action result plus the canonical workflow project snapshot.

**Tech Stack:** Python 3.11, pytest, SQLite repository, workflow coordinator subprocess, Node.js OpenClaw plugin, native Discord Components v2.

---

## File map

- Modify src/openclaw_web/delivery/components.py: review-card button contract, default reason, and reason normalization.
- Modify src/openclaw_web/runtime.py: legacy allowed_actions and smart callback messages.
- Modify tests/unit/test_components.py: card allowlists, default reason, and authorization.
- Modify tests/unit/test_runtime.py: callback response/state tests and legacy payload coverage.
- Modify tests/plugin/openclaw-web-plugin.test.mjs: reject callback transport coverage.
- Do not modify agents/shared/workflow-coordinator.py authorization; it already enforces Minh-only reject.
- Do not modify discovery timer, exec policy, credentials, or model/provider configuration.

## Task 1: Add the review-card Reject contract and persisted action

**Files:** src/openclaw_web/delivery/components.py, src/openclaw_web/runtime.py, tests/unit/test_components.py, tests/unit/test_runtime.py

- [x] Step 1: Write a failing test named test_review_card_includes_min_only_reject_button. Assert the card has a danger Reject button, callbackData openclaw-web:project:project-1:reject, and allowedUsers [MINH_ID].
- [x] Step 2: Run: rtk python -m pytest tests/unit/test_components.py::test_review_card_includes_min_only_reject_button -q. Expected: failure because Reject is absent.
- [x] Step 3: Add DEFAULT_REVIEW_REJECTION_REASON = "Không phù hợp với tiêu chí review hiện tại." and add Button("Reject", "reject", (MINH_ID,)) to build_review_card. Update the legacy review payload allowed_actions to ["approve", "reject", "view-evidence", "refresh"].
- [x] Step 4: Add a legacy delivery regression asserting review-card.json persists reject and the rendered button is Minh-only.
- [x] Step 5: Run: rtk python -m pytest tests/unit/test_components.py tests/unit/test_runtime.py -q. Expected: all pass.
- [x] Step 6: Commit the card contract with rtk git add src/openclaw_web/delivery/components.py src/openclaw_web/runtime.py tests/unit/test_components.py tests/unit/test_runtime.py followed by rtk git commit -m "feat: add Minh-only review reject button".

## Task 2: Normalize the one-click reject reason and enforce authorization

**Files:** src/openclaw_web/delivery/components.py, tests/unit/test_components.py

- [x] Step 1: Write a failing test that calls ComponentActionService.execute with action reject, actor Minh, and no reason; assert coordinator.calls[0]["reason"] equals DEFAULT_REVIEW_REJECTION_REASON and fallback is /lead-reject project-1 followed by the default reason.
- [x] Step 2: Write a failing test that calls the same action as Wien; assert status unauthorized and coordinator.calls remains empty.
- [x] Step 3: In ComponentActionService.execute, compute effective_reason before typed_fallback: use DEFAULT_REVIEW_REJECTION_REASON only when action is reject and reason is blank; pass effective_reason to the coordinator.
- [x] Step 4: Run: rtk python -m pytest tests/unit/test_components.py -q. Expected: all pass.
- [x] Step 5: Commit the component reason change with rtk git add src/openclaw_web/delivery/components.py tests/unit/test_components.py followed by rtk git commit -m "feat: normalize review reject reason".

## Task 3: Add smart durable callback messages

**Files:** src/openclaw_web/runtime.py, tests/unit/test_runtime.py

- [x] Step 1: Write failing temporary-workflow tests for accepted approve, accepted reject, refresh, stale, blocked, and already-processed results. Reuse the existing setup pattern in tests/unit/test_runtime.py: create a temporary workflow/projects/<id>/project.json, set OPENCLAW_WEB_STATE_DB and OPENCLAW_WORKFLOW_ROOT, migrate a SQLite database, insert one ComponentSet row with the matching message_id/channel_id/project_id/state_version, then call run_component_callback. Assert messages identify project/action/state and next step; reject includes the canonical reason; no result is only the generic Vietnamese acknowledgement.
- [x] Step 2: Run: rtk python -m pytest tests/unit/test_runtime.py -k smart_reject_or_smart_approve_or_smart_refresh -q. Expected: failure with the current generic message.
- [x] Step 3: Add private _component_result_message(envelope, result, project) in runtime.py. For accepted approve return "Đã duyệt <id>. Trạng thái mới: <state>. Bước tiếp theo: Website Brief." For accepted reject return the project, canonical reason, and rejected state. For read-only preserve truthful state/version output. For stale/expired say to press Refresh; for blocked mention checklist/P0/P1; for duplicate include current state; preserve fallback commands.
- [x] Step 4: In run_component_callback, call the formatter only after service.execute_envelope and after defensively reading the canonical project snapshot.
- [x] Step 5: Run: rtk python -m pytest tests/unit/test_runtime.py tests/unit/test_components.py -q. Expected: all pass.
- [x] Step 6: Commit smart result messages with rtk git add src/openclaw_web/runtime.py tests/unit/test_runtime.py followed by rtk git commit -m "feat: explain component action outcomes".

## Task 4: Verify plugin callback coverage

**Files:** tests/plugin/openclaw-web-plugin.test.mjs; deploy/openclaw-web-plugin/index.js is inspected and remains unchanged when the existing reject grammar test passes.

- [x] Step 1: Write a test that callbackContext("project:project-1:reject") reaches runCallback and sends its Vietnamese message ephemerally.
- [x] Step 2: Run: rtk node --test tests/plugin/openclaw-web-plugin.test.mjs. Expected: all pass because the callback grammar already includes reject.
- [x] Step 3: Commit the plugin coverage with rtk git add tests/plugin/openclaw-web-plugin.test.mjs deploy/openclaw-web-plugin/index.js followed by rtk git commit -m "test: cover review reject callback".

## Task 5: Full verification and immutable release

- [x] Step 1: Run rtk node --test tests/plugin/openclaw-web-plugin.test.mjs; rtk python -m pytest tests/unit/test_components.py tests/unit/test_runtime.py tests/unit/test_cli.py -q; rtk ruff check .; rtk python -m mypy src/openclaw_web; and rtk python -m compileall -q src. Expected: zero failures and zero lint/type errors.
- [x] Step 2: Run rtk python -m pytest tests/unit --ignore=tests/unit/test_remote_installer_harness.py -q; document only known Windows symlink skips.
- [x] Step 3: Run rtk python -m build and verify the wheel is created.
- [x] Step 4: Run rtk git diff --check, rtk git status --short --branch, and inspect the intended commit list.

## Task 6: Remote deployment and live acceptance

Remote mutation needs separate approval at execution time. The timer remains disabled/inactive.

Execution note: the current legacy delivery used a v0 component/delivery identity. The
implementation emits a v1 identity and idempotency key so an existing v0 card is
preserved while one replacement card is sent with Reject; the old delivery is not
deleted or overwritten. If the canonical project has already left `review` or its
state version is no longer `0`, the bridge returns the existing delivery instead of
attempting to persist an invalid review component against the newer state.

- [x] Step 1: Explain backup, immutable release, plugin/config update, required gateway restart, no timer/policy/credential changes, and rollback.
- [x] Step 2: Upload through the existing in-memory .env/Paramiko workflow and run deploy/install-remote.sh. Require install_verified=offline.
- [x] Step 3: Verify config, health, Discord probe, gateway status, plugin runtime source/status, and timer state.
- [ ] Step 4: With a real Minh click, verify Reject transitions the project to rejected once with the default reason. Wien must not see Reject; a real unauthorized attempt must not mutate state. Verify Approve, View evidence, and Refresh.
- [x] Step 5: Record release SHA, backup path, gateway PID, timer state, and rollback instructions.

Deployment evidence (2026-08-15): release
`100ed0a02b197e57cd0e7d07f6f9ebd2a3435bed1338dd8fdc749fb8986c98bb`, backup
`~/.openclaw/backups/web-audit-reject-smart-20260815-141153`, gateway PID
`3986718`, and timer `disabled`/`inactive`. Live actor acceptance remains pending
because the only existing review cards predate v1 (NTQ is already `approved`, and
the other review card has no Reject); no actor identity was simulated.

## Plan self-review

- Every spec section maps to a task: card/allowlist, default reason, smart messages, plugin transport, tests/build, and remote acceptance.
- No unresolved placeholder remains; every implementation step names a file, command, and expected result.
- Names are consistent: DEFAULT_REVIEW_REJECTION_REASON, ActionResult, ComponentActionEnvelope, and _component_result_message.
- Scope excludes coordinator authorization, cron enablement, and unrelated security/config changes.
