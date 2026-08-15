# Compact Review Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route legacy Curie review projects through the durable native Components v2 delivery path and render one compact option-A card with a visible `Approve` button.

**Architecture:** Add a bounded legacy-review service that reads the canonical workflow project, creates the minimum web-audit candidate/project/delivery records, and dispatches through the existing outbox transport. Extend the delivery envelope with an optional message string so both production and legacy cards can show one compact summary while the action row remains unchanged.

**Tech Stack:** Python 3.11, Pydantic, SQLite repository, Typer CLI, OpenClaw message transport, pytest.

---

### Task 1: Add the compact message field to transport

**Files:**
- Modify: `src/openclaw_web/delivery/openclaw_transport.py:100-130`
- Test: `tests/unit/test_openclaw_transport.py`

- [ ] **Step 1: Write the failing test**

Add a transport test payload containing `"message": "**NTQ Solution · Website review**"` and assert the generated argv uses that exact message instead of `Duyệt lead project-1`.

- [ ] **Step 2: Run the focused test and verify RED**

Run `pytest tests/unit/test_openclaw_transport.py -q`. It must fail because `send()` currently hardcodes `Duyệt lead project-1`.

- [ ] **Step 3: Implement the minimal contract**

Read `envelope.get("message")`; accept a non-empty string up to 4,000 UTF-8 bytes; otherwise preserve the existing fallback. Pass the selected value as the existing `--message` argument and do not add a second presentation text block.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run `pytest tests/unit/test_openclaw_transport.py -q`; all tests must pass.

- [ ] **Step 5: Commit**

Run `git add src/openclaw_web/delivery/openclaw_transport.py tests/unit/test_openclaw_transport.py && git commit -m "feat: allow compact review message"`.

### Task 2: Build and test the legacy compact summary

**Files:**
- Create: `src/openclaw_web/review/legacy.py`
- Modify: `src/openclaw_web/review/__init__.py`
- Test: `tests/unit/test_legacy_review.py`

- [ ] **Step 1: Write the failing tests**

Cover one fixture with the NTQ-style dossier and assert `render_legacy_review_message()`:

```python
assert message.count("NTQ Solution") == 1
assert "TOP OPPORTUNITIES" in message
assert message.count("P1") == 2
assert "/lead-approve" not in message
assert "Confidence" in message
```

Also assert a missing dossier raises `LegacyReviewError` and a malformed project without a valid `website` raises the same bounded error.

- [ ] **Step 2: Run the focused tests and verify RED**

Run `pytest tests/unit/test_legacy_review.py -q`; it must fail because the module does not exist.

- [ ] **Step 3: Implement deterministic rendering**

Parse only the known Vietnamese dossier markers (`Tóm tắt`, `Top issues`, `Evidence`, `Ảnh first-party`, `Confidence gaps`). Emit one title, one metadata line, a proof line, the first three priority issues, one evidence line, and one confidence line. Preserve URLs and project IDs exactly; truncate each free-text section to a fixed bounded length.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run `pytest tests/unit/test_legacy_review.py -q`; all tests must pass.

- [ ] **Step 5: Commit**

Run `git add src/openclaw_web/review/legacy.py src/openclaw_web/review/__init__.py tests/unit/test_legacy_review.py && git commit -m "feat: render compact legacy review cards"`.

### Task 3: Add the idempotent legacy delivery service and CLI

**Files:**
- Modify: `src/openclaw_web/runtime.py`
- Modify: `src/openclaw_web/cli.py`
- Modify: `src/openclaw_web/runtime.py` evidence fallback near `_component_read_only`
- Test: `tests/unit/test_runtime.py`, `tests/unit/test_cli.py`, `tests/integration/test_legacy_review_runtime.py`

- [ ] **Step 1: Write failing service/integration tests**

Use a temporary workflow root, temporary SQLite state DB, a project JSON in `projects/vn-ntq-test/`, and a fake `DeliveryTransport`. Assert the service creates exactly one project and pending delivery, dispatch persists one `component_sets` row with `Approve`, and a second call returns the existing sent identity without a second transport call. Assert evidence lookup returns the legacy dossier/image inventory names when no `artifact_dir` exists.

- [ ] **Step 2: Run focused tests and verify RED**

Run `pytest tests/unit/test_runtime.py tests/unit/test_cli.py tests/integration/test_legacy_review_runtime.py -q`; it must fail because `legacy-review` and `run_legacy_review()` do not exist.

- [ ] **Step 3: Implement the service**

Add `run_legacy_review(project_id, workflow_root=None, review_channel=None, guild_id=None)` that:

1. Loads `/projects/<project_id>/project.json` and its `dossier_file`.
2. Requires `status == "review"` and a valid HTTPS website.
3. Calls `Repository.upsert_candidate()` and `ensure_review_project()` with deterministic legacy artifact identity.
4. Writes a compact `review-card.json` containing the deterministic component metadata, existing `build_review_card(project_id).payload`, and the rendered `message`.
5. Enqueues `DeliveryRecord(event_type="review-card", idempotency_key=f"review:{project_id}")` once.
6. Dispatches through `OutboxWorker` and returns only redacted IDs/status/URL fields.

Use `OPENCLAW_WEB_REVIEW_CHANNEL` with the documented review-channel default `1536658476288450630`, and the existing guild default `1446612692910739637` when the environment does not provide them. Do not read or print secrets.

Add the `legacy-review` Typer command with `--project-id` and `--json`; map bounded validation errors to exit code 2.

In `_component_read_only`, if `artifact_dir/evidence` is absent, list only known files (`dossier_file`, `image_inventory_file`) from the canonical project directory.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run `pytest tests/unit/test_runtime.py tests/unit/test_cli.py tests/integration/test_legacy_review_runtime.py -q`; all tests must pass.

- [ ] **Step 5: Commit**

Run `git add src/openclaw_web/runtime.py src/openclaw_web/cli.py tests/unit/test_runtime.py tests/unit/test_cli.py tests/integration/test_legacy_review.py && git commit -m "feat: deliver legacy reviews as native cards"`.

### Task 4: Put the compact summary into production review artifacts and update the coordinator contract

**Files:**
- Modify: `src/openclaw_web/pipeline/production.py:674-691`
- Modify: `agents/shared/COORDINATOR.md`
- Modify: `agents/shared/contracts/curie-handoff.md`
- Test: `tests/integration/test_production_pipeline.py`, `tests/unit/test_deploy_contract.py`

- [ ] **Step 1: Write the failing assertions**

Assert production `review-card.json` contains a bounded `message` with one title and `TOP OPPORTUNITIES`, and assert the coordinator contract names the exact command `openclaw-web legacy-review --project-id <project_id> --json` before sending the review-channel acknowledgement.

- [ ] **Step 2: Run the focused tests and verify RED**

Run `pytest tests/integration/test_production_pipeline.py tests/unit/test_deploy_contract.py -q`; the message and contract assertions must fail.

- [ ] **Step 3: Implement minimal production/docs changes**

Generate the same compact shape from audited candidate/findings, persist it under the envelope `message`, and update the Vietnamese handoff contract so `main` never sends the dossier as two plain messages. It must call `legacy-review`, send only the returned direct link to `review`, then run `workflow-coordinator.py record-messages` for the returned bot message ID.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the focused commands above, then `ruff check src tests` and `python -m compileall -q src`.

- [ ] **Step 5: Commit**

Run `git add src/openclaw_web/pipeline/production.py agents/shared/COORDINATOR.md agents/shared/contracts/curie-handoff.md tests/integration/test_production_pipeline.py tests/unit/test_deploy_contract.py && git commit -m "docs: route legacy review delivery through cards"`.

### Task 5: Deploy and verify the real NTQ card

**Files/services:**
- Remote release under `/home/minhmice/.local/share/openclaw-web/releases/<hash>`
- Remote extension `openclaw-web-components`
- Canonical workflow project `/home/minhmice/.openclaw/workflow/projects/vn-ntq-20260814/`

- [ ] **Step 1: Run the full local suite**

Run `pytest -q --ignore=tests/unit/test_remote_installer_harness.py`, `ruff check src tests`, `python -m compileall -q src`, and `git diff --check`.

- [ ] **Step 2: Deploy with the existing installer**

Create a timestamped backup through the documented installer, install the tested release, run `openclaw config validate`, and do not restart the gateway or enable the discovery timer.

- [ ] **Step 3: Send the NTQ bridge card**

Run the installed CLI with `legacy-review --project-id vn-ntq-20260814 --json` using the configured non-secret workflow/channel environment. Keep the two old text messages unchanged.

- [ ] **Step 4: Verify live state**

Read only selected fields: returned project/delivery/component IDs, one `sent` delivery, one `component_sets` row, Discord message components with `Approve/View evidence/Refresh`, and workflow state still `review`/version `0`. Do not simulate a Minh/Wien click.

- [ ] **Step 5: Report remaining human gate**

Provide the direct card link and ask Minh to click `Approve`; separately ask Wien to click an independent real card when available. Do not claim the approval path is closed until the real actor callbacks are observed.
