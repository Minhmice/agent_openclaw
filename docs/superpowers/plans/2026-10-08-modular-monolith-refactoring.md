# Implementation Plan: Modular Monolith Refactoring & Clean Graphify

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `agent_openclaw` from an entangled repository with monolithic persistence into a domain-driven Modular Monolith and generate a clean, unpolluted architecture graph.

**Architecture:** Split `src/openclaw_web/` into 6 bounded domains (`platform`, `discovery`, `audit`, `lead_intelligence`, `delivery`, `dashboard`). Replace monolithic `Repository` (80 KB) with focused domain stores sharing the SQLite connection. Unify pipeline checkpoints under a single `StageOutcome` contract. Clean `.graphifyignore` and generate domain-labeled knowledge graphs.

**Tech Stack:** Python 3.11, SQLite (WAL mode), Pydantic v2, Typer CLI, Playwright, vis.js, NetworkX (`graphify`).

**Spec:** `docs/superpowers/specs/2026-10-08-modular-monolith-refactoring-design.md`

## Global Constraints
- Preserve SQLite WAL, table schemas, run lock lease, and idempotency guarantees.
- Preserve Discord Components v2 contracts and authorization rules (Minh/Wien parity, Minh-only reject).
- Independent AND-gates in Lead Intelligence must remain strictly non-compensatory.
- Keep CLI entry point `openclaw-web` command names, arguments, and JSON output formats identical.
- Zero compatibility shims or obsolete wrapper facades retained post-cutover.

---

### Task 1: Domain Stores & Platform Decoupling (Eradicate Monolithic Repository)

**Files:**
- Create:
  - `src/openclaw_web/platform/errors.py`
  - `src/openclaw_web/discovery/store.py`
  - `src/openclaw_web/audit/store.py`
  - `src/openclaw_web/lead_intelligence/store.py`
  - `src/openclaw_web/delivery/store.py`
- Modify: `src/openclaw_web/db/connection.py`, `src/openclaw_web/db/repository.py`
- Test: `tests/unit/test_repository.py`, `tests/unit/test_repository_boundaries.py`

**Interfaces:**
- Consumes: `platform.db.connection.connect`, `platform.db.migrations.migrate`
- Produces: `DiscoveryStore`, `AuditStore`, `LeadStore`, `DeliveryStore` sharing a single SQLite connection.

- [ ] **Step 1: Write failing unit test for domain stores**
  Verify each store interacts cleanly with its assigned SQLite tables independently.

- [ ] **Step 2: Run test to verify RED**
  Run: `pytest tests/unit/test_domain_stores.py -q`
  Expected: FAIL (modules do not exist).

- [ ] **Step 3: Extract domain stores from `Repository`**
  Implement `DiscoveryStore`, `AuditStore`, `LeadStore`, `DeliveryStore`. Migrate callers from monolithic `Repository`.

- [ ] **Step 4: Run test to verify GREEN**
  Run: `pytest tests/unit/test_domain_stores.py tests/unit/test_repository_boundaries.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add src/openclaw_web/ tests/
  git commit -m "feat(storage): extract domain stores from monolithic repository"
  ```

---

### Task 2: Package Structure Migration & Domain Reorganization

**Files:**
- Move & Reorganize:
  - `src/openclaw_web/crawl/` & `src/openclaw_web/screenshots/` -> `src/openclaw_web/audit/`
  - `src/openclaw_web/lead_contracts.py` & `src/openclaw_web/topology.py` -> `src/openclaw_web/lead_intelligence/`
  - `src/openclaw_web/runtime_discovery.py` -> `src/openclaw_web/discovery/service.py`
  - `src/openclaw_web/runtime_components.py` & `src/openclaw_web/runtime_legacy.py` -> `src/openclaw_web/delivery/`
- Modify: `src/openclaw_web/cli.py`, `pyproject.toml`
- Test: `tests/unit/test_cli.py`, `tests/unit/test_runtime.py`, `tests/integration/test_legacy_review_runtime.py`

**Interfaces:**
- Consumes: Domain stores from Task 1.
- Produces: Direct imports from domain packages (`discovery`, `audit`, `lead_intelligence`, `delivery`, `dashboard`).

- [ ] **Step 1: Update CLI imports to domain paths**
  Update `src/openclaw_web/cli.py` to import directly from domain services.

- [ ] **Step 2: Run CLI tests to verify failures/needs**
  Run: `pytest tests/unit/test_cli.py -q`
  Verify paths requiring migration.

- [ ] **Step 3: Move files to domain folders and update imports**
  Complete file reorganization and adjust internal import statements.

- [ ] **Step 4: Run test suite to verify GREEN**
  Run: `pytest tests/unit/test_cli.py tests/unit/test_runtime.py tests/integration/test_legacy_review_runtime.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add src/openclaw_web/ tests/
  git commit -m "refactor(structure): reorganize modules into domain-bounded packages"
  ```

---

### Task 3: Unify Pipeline & Stage Outcomes

**Files:**
- Modify:
  - `src/openclaw_web/audit/stages.py`
  - `src/openclaw_web/pipeline/production.py`
  - `src/openclaw_web/lead_intelligence/pipeline.py`
  - `src/openclaw_web/lead_intelligence/contracts.py`
- Test: `tests/unit/test_lead_stages.py`, `tests/integration/test_audit_pipeline.py`

**Interfaces:**
- Consumes: `openclaw_web.lead_intelligence.contracts.StageOutcome`
- Produces: Unified stage runner and persistence checkpoints.

- [ ] **Step 1: Write regression test ensuring audit stages produce standard StageOutcome**
  Assert `audit/stages.py` outputs instances compatible with `LeadStore.record_stage_outcome`.

- [ ] **Step 2: Run test to verify RED**
  Run: `pytest tests/unit/test_stage_unification.py -q`
  Expected: FAIL.

- [ ] **Step 3: Unify stage outcome serialization**
  Adopt the single `StageOutcome` across audit and lead intelligence pipelines.

- [ ] **Step 4: Run tests to verify GREEN**
  Run: `pytest tests/unit/test_lead_stages.py tests/integration/test_audit_pipeline.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit**
  ```bash
  git add src/openclaw_web/ tests/
  git commit -m "refactor(pipeline): unify stage checkpoints under standard StageOutcome"
  ```

---

### Task 4: Clean Graphify Architecture & Domain Visualizer

**Files:**
- Modify: `.graphifyignore`, `README.md`
- Output: `graphify-out/graph.json`, `graphify-out/graph.html`, `graphify-out/GRAPH_REPORT.md`

**Interfaces:**
- Consumes: Reorganized `src/openclaw_web/` codebase.
- Produces: High-signal architecture graph (~300-400 nodes) with 6 domain communities.

- [ ] **Step 1: Configure `.graphifyignore` for architectural focus**
  Add exclusions for `tests/`, `dashboard/*.js`, `dashboard/*.css`, `schemas/generated/`, `docs/archive/`.

- [ ] **Step 2: Run AST extraction & community labeling**
  Extract code nodes, assign labels to the 6 core domains, and generate clean `GRAPH_REPORT.md`.

- [ ] **Step 3: Generate clean interactive HTML graph**
  Export `graphify-out/graph.html` using the clustered domain communities.

- [ ] **Step 4: Verify graph metrics and query ability**
  Run: `graphify query "DiscoveryStore" --budget 300`
  Verify query hits the new domain nodes cleanly.

- [ ] **Step 5: Commit**
  ```bash
  git add .graphifyignore README.md
  git commit -m "chore(graphify): configure architecture profile and regenerate clean graph"
  ```
