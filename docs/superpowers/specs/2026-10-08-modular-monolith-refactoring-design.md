# Architecture Spec: Modular Monolith Refactoring & Graph Simplification

> **Status:** Approved
> **Target:** `agent_openclaw` core application refactoring

## 1. Goal & Context
Refactor `agent_openclaw` from an entangled repository with monolithic persistence (`Repository` god-node of ~80 KB) into a clean, domain-driven Modular Monolith. Simultaneously streamline knowledge graph indexing (`graphify`) so architectural navigation reflects system boundaries without clutter from test fixtures and micro-utilities.

---

## 2. Domain-Driven Package Layout (`src/openclaw_web/`)

Package structure migrates to clear domain boundaries:

```text
src/openclaw_web/
├── platform/               # Foundation layer
│   ├── db/
│   │   ├── connection.py   # SQLite connection, WAL pragmas, run locks
│   │   └── migrations.py   # DDL migrations and schema evolution
│   └── errors.py           # Base domain exceptions
│
├── discovery/              # Lead sourcing & geo-filtering
│   ├── base.py             # Provider protocols & exceptions
│   ├── overpass.py         # OSM Overpass integration
│   ├── nominatim.py        # OSM Nominatim bounded fallback
│   ├── places.py           # Google Places adapter
│   ├── serper.py           # Serper search adapter
│   ├── service.py          # Discovery orchestrator
│   └── store.py            # DiscoveryStore (seeds, candidate raw storage)
│
├── audit/                  # Technical & UX website evaluation
│   ├── crawl/              # Extraction, crawler client, URL safety
│   ├── screenshots/        # Playwright runner, viewport captures
│   ├── lighthouse.py       # Performance & audit runner
│   ├── rules.py            # Scoring rules and rubrics
│   ├── stages.py           # Audit stage runner (uses standard StageOutcome)
│   └── store.py            # AuditStore (pages, screenshots, scores)
│
├── lead_intelligence/      # Business grading, gates & rankings
│   ├── contracts.py        # Strict Pydantic models (GateResult, PortfolioEntry)
│   ├── gates.py            # Independent AND-gate evaluations
│   ├── pipeline.py         # Multi-agent stage evaluation loop
│   ├── topology.py         # Topological stage sorting
│   └── store.py            # LeadStore (runs, stage outcomes, portfolios)
│
├── delivery/               # Notification & Discord interactions
│   ├── components.py       # Discord Components v2 button schemas & callbacks
│   ├── outbox.py           # Durable delivery outbox worker
│   ├── transport.py        # OpenClaw agent message transport
│   └── store.py            # DeliveryStore (outbox queue, action receipts)
│
├── dashboard/              # Loopback operational control surface
│   ├── auth.py             # Bearer/token authentication
│   ├── server.py           # stdlib ThreadingHTTPServer & routing
│   ├── projection.py       # Read-model snapshots
│   └── coordinator.py      # Action verification & dispatch
│
└── cli.py                  # Single Typer CLI entrypoint
```

---

## 3. Data Persistence Decoupling (Eradicate God-Node)

1. **Delete monolithic `Repository`:**
   - Remove single-file 80 KB persistence class.
   - Replace with focused stores: `DiscoveryStore`, `AuditStore`, `LeadStore`, `DeliveryStore`.
2. **Transactional Boundaries:**
   - Stores share the SQLite connection provided by `platform.db.connection`.
   - Each store is strictly responsible for its own domain tables.
   - Cross-domain interactions must pass through data models, not raw cross-table mutations.

---

## 4. Unified Stage & Checkpoint Model

1. **Single Resumable Outcome Definition:**
   - All pipeline steps record progress via `openclaw_web.lead_intelligence.contracts.StageOutcome`.
   - Attributes: `stage_id`, `run_id`, `producer_role`, `status`, `input_refs`, `output_refs`, `evidence_ids`, `checkpoint`, `reused`.
   - Remove fragmented definitions (`ArtifactStageOutcome` merged into domain audit runner).
2. **Immutable Gate Invariants:**
   - Independent AND-gates strictly preserved (no average compensations).
   - Discord action confirmations remain idempotent with monotonic `state_version` enforcement.
   - Minh & Wien approval roles remain hardcoded and non-bypassable.

---

## 5. Clean Knowledge Graph Architecture (`graphify`)

1. **Profile `architecture` (Default Clean View):**
   - Update `.graphifyignore`:
     ```text
     tests/
     dashboard/*.js
     dashboard/*.css
     schemas/generated/
     docs/archive/
     .playwright-mcp/
     ```
   - Target nodes: ~300 - 400 core entities instead of 2,500+ cluttered nodes.
2. **Domain Community Labeling:**
   - Automatically categorize communities into 6 named domains:
     1. `Platform & Storage`
     2. `Discovery & Sources`
     3. `Audit & Inspection`
     4. `Lead Intelligence`
     5. `Delivery & Discord Outbox`
     6. `Operational Dashboard`
   - Zero unlabelled `Community N` artifacts in `GRAPH_REPORT.md`.

---

## 6. Verification Criteria

1. **Unit & Integration Suite:** 100% of affected tests migrated and passing (`pytest tests/`).
2. **CLI Determinism:** `openclaw-web --help`, `health`, `cron-run --dry-run` run cleanly with zero import errors.
3. **Graph Validation:** `graphify-out/graph.html` and `GRAPH_REPORT.md` reflect the 6 clean domain communities without dangling dependencies or test pollution.
