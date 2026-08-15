# Live Discovery and Acceptance Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Produce a real defensible Hanoi-area review card, validate the native Discord approval path with Minh, Wien, and an unauthorized actor, and close the remaining Linux/browser/documentation acceptance checks before enabling the scheduled discovery timer.

**Architecture:** Use the deployed `openclaw-web` release as the source of truth. Run one bounded production discovery slice at a time, preserve the evidence/artifact/database records, and let the existing safety, evidence, scoring, and idempotency gates decide whether a candidate qualifies. Human Discord clicks remain an external gate; the timer is enabled only after those clicks and state-transition checks are observed.

**Tech Stack:** Python 3.11, SQLite-backed `openclaw-web`, OpenClaw gateway, Discord Components v2, systemd user timer, pytest/ruff, Linux/WSL or compatible container, and installed Chromium/Playwright.

---

### Task 1: Establish live baseline and verify component semantics

**Files:**
- Read: `src/openclaw_web/delivery/components.py`
- Read: `src/openclaw_web/runtime.py`
- Read: `agents/shared/workflow-coordinator.py`
- Read: `deploy/openclaw-web-plugin/index.js`
- Read: remote `~/.config/systemd/user/openclaw-web-discovery.timer`

- [ ] Run the documented live health checks and record only redacted status: gateway, Discord probe, service state, timer state, and web-audit project/delivery/component counts.
- [ ] Confirm the review button allowlist is exactly Minh `620891893659598850` and Wien `859783610625556480`, callback validation binds the real guild/message/component set, and the coordinator records `approved_by` while moving the project out of `review`.
- [ ] Confirm whether one project can accept two approvals. If the coordinator rejects the second approval after the first state transition, use two independent real cards for the two human actor tests and document that the project under each test transitions once.

### Task 2: Run bounded real discovery until a defensible candidate is delivered

**Files/artifacts:**
- Remote deployed release under `/home/minhmice/.local/share/openclaw-web/current`
- Remote state database configured by `OPENCLAW_WEB_STATE_DB`
- Remote workflow project under `~/.openclaw/workflow/projects/<project_id>/`
- Remote review artifact under the configured artifact root

- [ ] Source the web-audit environment without printing its values, then run one production discovery slice with a finite process timeout and no concurrent timer.
- [ ] For each run, inspect only redacted JSON/status and database counts; stop after the first candidate with safe crawl, at least three public evidence URLs, qualified scoring, artifact persistence, and a sent `review-card` delivery.
- [ ] If the default deployed slice is too broad, use a bounded one-off invocation or a narrowly scoped environment/configuration change that limits provider seeds, full audits, and crawl pages without weakening the evidence gate.
- [ ] Verify the real review card has a Discord message ID, component-set ID, state version, expiry, `Approve` Components v2 callback, and a direct Discord link. Do not auto-approve or synthesize a card.

### Task 3: Complete the human button acceptance gate

**Files/artifacts:**
- Remote `projects/<project_id>/project.json`
- Remote SQLite `component_sets`, `component_actions`, `projects`, and `deliveries` rows
- Remote `WORKLOG.md`

- [ ] Send the real review-card link to Minh and wait for Minh to click `Approve` in Discord; verify the recorded actor is `620891893659598850`, the callback is accepted once, and the project leaves `review`.
- [ ] Give Wien an independent real review card if required by the one-transition semantics, then wait for Wien to click `Approve`; verify actor `859783610625556480` and the same state transition on that independent project.
- [ ] Exercise the callback with an actually unauthorized Discord actor only through a controlled real interaction; verify `unauthorized`, no coordinator call/state mutation, and no spoofed identity. Do not impersonate Minh or Wien with a script.
- [ ] Verify the approved project contains the canonical approved handoff and that Website Brief is invoked/created from the approved state, with no direct jump that bypasses the gate.

### Task 4: Enable and verify the discovery timer after the human gate

**Files:**
- Remote `~/.config/systemd/user/openclaw-web-discovery.timer`
- Remote `~/.config/systemd/user/openclaw-web-discovery.service`

- [ ] Only after Task 3 passes, run `systemctl --user enable --now openclaw-web-discovery.timer`.
- [ ] Verify `is-enabled=enabled`, `is-active=active`, next/last elapse at 07:30 Asia/Bangkok, and the service lock/idempotency behavior.
- [ ] Verify a scheduled or manually repeated run cannot create a duplicate delivery for the same project; retain the prior disabled state and unit snapshot for rollback.

### Task 5: Run the full Linux/browser acceptance matrix

**Files/tests:**
- `tests/unit/test_remote_installer_harness.py`
- Full `tests/` suite in Linux/WSL/container
- Real Chromium smoke marker/configuration

- [ ] Detect WSL/container availability before running anything; execute the full suite in a compatible Linux/bash environment and report the installer-harness result separately if the environment still lacks its required isolation.
- [ ] Run the real Chromium-enabled desktop/tablet/mobile smoke test against an installed browser, not only deterministic fixtures. Do not install packages or browsers as an implicit side effect.
- [ ] Preserve screenshots/test output paths without exposing private data and distinguish unavailable-browser/environment failures from application failures.

### Task 6: Update stale market-scope documentation and verify local repositories

**Files:**
- Modify: `agents/curie/OPEN-QUESTIONS.md`
- Check: `agents/shared/VIETNAMESE-LANGUAGE-POLICY.md`

- [ ] Replace the stale “awaiting user answer” market section with the decided scope: `đa ngành`, `quanh Hà Nội`, market ID `hanoi-80km`.
- [ ] Run targeted unit/integration tests, `ruff`, `py_compile`, and `git diff --check` in the affected local repositories.
- [ ] Commit only the documentation/test artifacts belonging to this acceptance task; do not commit secrets or remote state.

### Task 7: Final evidence report and rollback record

- [ ] Re-run redacted health, Discord, timer, project-count, delivery-count, component-count, and workflow-state checks after all authorized mutations.
- [ ] Report the exact candidate/project/message IDs and evidence links, actual human actor IDs, button outcomes, timer state, Linux/browser test results, and any remaining blocker.
- [ ] If a remote mutation must be rolled back, restore only the timestamped backup/unit snapshot for that mutation and re-validate; do not delete backups.
