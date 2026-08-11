# Web Audit, Discovery, Website Brief, and Discord Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved evidence-first website discovery, audit, scoring, Website Brief, Discord project-button, feedback, and daily discovery workflow for the Hanoi 80 km multi-industry market.

**Architecture:** Add a Python modular monolith under `src/openclaw_web` backed by SQLite and versioned JSON artifacts. Keep the existing workflow coordinator as the single project-state mutation authority; new button and delivery adapters call that coordinator rather than duplicating its rules. Run discovery through an overlap-safe `systemd --user` timer and route user-visible delivery through OpenClaw.

**Tech Stack:** Python 3.11+, Pydantic 2, HTTPX, Beautiful Soup 4, tldextract, Playwright Chromium, Jinja2, Typer, PyYAML, SQLite, pytest, respx, Ruff, mypy, Lighthouse CLI, OpenClaw Discord Components v2.

---

## Scope and execution rules

Implement every P0/P1 requirement and the explicitly requested project-action buttons and discovery timer. Do not build a deployed dashboard, CRM, automated outreach, CMS, auth, billing, or production website deployment.

All code tasks use strict red-green-refactor:

1. add one focused failing test;
2. run it and confirm the expected failure;
3. add the minimum implementation;
4. run the focused test and full relevant suite;
5. commit the completed slice.

Remote deployment is a separate approval-gated task after all local verification succeeds.

## Target file structure

```text
pyproject.toml
src/openclaw_web/
├── __init__.py
├── cli.py
├── settings.py
├── artifacts.py
├── models.py
├── observability.py
├── db/
│   ├── __init__.py
│   ├── connection.py
│   ├── migrations.py
│   └── repository.py
├── discovery/
│   ├── __init__.py
│   ├── base.py
│   ├── batch.py
│   ├── places.py
│   ├── scheduler.py
│   ├── serper.py
│   └── service.py
├── geofence/
│   ├── __init__.py
│   ├── distance.py
│   └── service.py
├── crawl/
│   ├── __init__.py
│   ├── extract.py
│   ├── robots.py
│   ├── safety.py
│   └── service.py
├── screenshots/
│   ├── __init__.py
│   └── playwright_runner.py
├── audit/
│   ├── __init__.py
│   ├── builtin.py
│   ├── lighthouse.py
│   └── service.py
├── scoring/
│   ├── __init__.py
│   ├── engine.py
│   ├── priority.py
│   └── rules.py
├── review/
│   ├── __init__.py
│   ├── ai.py
│   ├── openclaw_model.py
│   └── dossier.py
├── pipeline/
│   ├── __init__.py
│   ├── audit.py
│   ├── cron.py
│   └── stages.py
├── brief/
│   ├── __init__.py
│   ├── generator.py
│   ├── stages.py
│   └── validator.py
├── render/
│   ├── __init__.py
│   ├── homepage.py
│   └── templates/homepage.html.j2
├── feedback/
│   ├── __init__.py
│   └── calibration.py
├── delivery/
│   ├── __init__.py
│   ├── components.py
│   ├── openclaw_transport.py
│   ├── outbox.py
│   └── workflow_adapter.py
└── health.py
config/
├── markets/hanoi-80km.yaml
├── scoring/base-v1.yaml
└── scoring/cohorts/*.yaml
schemas/
└── generated/*.schema.json
deploy/
├── openclaw-web-discovery.service
├── openclaw-web-discovery.timer
├── install-local.ps1
├── install-remote.sh
└── rollback-remote.sh
tests/
├── fixtures/sites/
├── integration/
├── unit/
└── test_workflow_coordinator.py
```

### Task 1: Package scaffold, settings, and CLI contract

**Files:**
- Create: `pyproject.toml`
- Create: `src/openclaw_web/__init__.py`
- Create: `src/openclaw_web/settings.py`
- Create: `src/openclaw_web/cli.py`
- Create: `config/markets/hanoi-80km.yaml`
- Test: `tests/unit/test_settings.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing settings and CLI tests**

```python
from pathlib import Path

from typer.testing import CliRunner

from openclaw_web.cli import app
from openclaw_web.settings import load_market


def test_hanoi_market_is_multi_industry_and_80km() -> None:
    market = load_market(Path("config/markets/hanoi-80km.yaml"))
    assert market.market_id == "hanoi-80km"
    assert market.radius_km == 80
    assert market.industries == ["*"]
    assert market.timezone == "Asia/Bangkok"


def test_cli_exposes_approved_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "health", "audit", "discover", "brief", "validate",
        "feedback", "calibration", "delivery", "cron-run",
        "component-action",
    ):
        assert command in result.stdout
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
python -m pytest tests/unit/test_settings.py tests/unit/test_cli.py -q
```

Expected: collection fails because `openclaw_web` does not exist.

- [ ] **Step 3: Add the package and dependency configuration**

Create `pyproject.toml` with:

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "agent-openclaw-web"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "beautifulsoup4>=4.12,<5",
  "httpx>=0.28,<1",
  "jinja2>=3.1,<4",
  "playwright>=1.54,<2",
  "pydantic>=2.10,<3",
  "pyyaml>=6,<7",
  "tldextract>=5.1,<6",
  "typer>=0.16,<1",
]

[project.optional-dependencies]
dev = [
  "build>=1.2,<2",
  "coverage[toml]>=7.10,<8",
  "mypy>=1.17,<2",
  "pytest>=8.4,<9",
  "pytest-asyncio>=1.1,<2",
  "respx>=0.22,<1",
  "ruff>=0.12,<1",
]

[project.scripts]
openclaw-web = "openclaw_web.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/openclaw_web"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true
packages = ["openclaw_web"]
```

Create the approved market file:

```yaml
market_id: hanoi-80km
center:
  name: "Hà Nội"
  latitude: 21.0285
  longitude: 105.8542
radius_km: 80
industries: ["*"]
timezone: Asia/Bangkok
```

Implement `MarketConfig` and `load_market()` with Pydantic validation. Add Typer command groups with help text and a `ServiceRegistry` protocol. The default registry raises a typed `ServiceUnavailable` error when a command's configured dependency is absent; the CLI renders the dependency name and exits with code 2. Each owning task replaces that unavailable dependency with its concrete service while preserving the tested error path for genuinely missing runtime dependencies.

- [ ] **Step 4: Install the editable package and run GREEN**

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/unit/test_settings.py tests/unit/test_cli.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit the scaffold**

```powershell
git add pyproject.toml src/openclaw_web config/markets tests/unit/test_settings.py tests/unit/test_cli.py
git commit -m "feat: scaffold web audit package"
```

### Task 2: Canonical models, artifact envelopes, and JSON schemas

**Files:**
- Create: `src/openclaw_web/models.py`
- Create: `src/openclaw_web/artifacts.py`
- Create: `schemas/generated/`
- Test: `tests/unit/test_models.py`
- Test: `tests/unit/test_artifacts.py`

- [ ] **Step 1: Write failing model tests**

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from openclaw_web.artifacts import atomic_write_artifact, read_artifact
from openclaw_web.models import ClaimStatus, Evidence, ScoreRecord


def test_inferred_evidence_requires_source_or_explicit_gap() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev-1",
            candidate_id="candidate-1",
            page_url="https://example.com/",
            evidence_type="business-claim",
            observed_value="Nhà máy lớn",
            claim_status=ClaimStatus.INFERRED,
            confidence="medium",
            captured_at=datetime.now(UTC),
            content_hash="a" * 64,
            evidence_urls=[],
            confidence_gap=None,
        )


def test_deterministic_score_requires_evidence_ids() -> None:
    with pytest.raises(ValidationError):
        ScoreRecord(
            score_name="technical_pain",
            score_value=70,
            rubric_version="base-v1",
            inputs={},
            evidence_ids=[],
            deterministic=True,
            explanation_vi="Điểm kỹ thuật.",
        )


def test_artifact_write_round_trips_and_adds_hash(tmp_path: Path) -> None:
    target = tmp_path / "candidate.json"
    atomic_write_artifact(target, "candidate-v1", "generator-v1", "run-1", {"id": "x"})
    envelope = read_artifact(target)
    assert envelope.schema_version == "candidate-v1"
    assert envelope.payload == {"id": "x"}
    assert len(envelope.content_hash) == 64
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_models.py tests/unit/test_artifacts.py -q
```

Expected: imports fail because the model and artifact modules do not exist.

- [ ] **Step 3: Implement canonical Pydantic models**

Define string enums for claim status, confidence, candidate state, project state, page state, severity, and delivery state. Define strict models for `CandidateSeed`, `Candidate`, `PageRecord`, `Evidence`, `AuditRecord`, `ScoreRecord`, `IssueRecord`, `ArtifactEnvelope`, `FeedbackEvent`, `DeliveryRecord`, and `ComponentSet`.

Define strict models for `RunRecord` and `StageRecord` in addition to the candidate, page, evidence, audit, score, issue, artifact, feedback, delivery, and component models. Use Pydantic model validators so:

- non-observed evidence requires evidence URL or `confidence_gap`;
- deterministic scores require evidence IDs;
- every issue requires evidence and Vietnamese recommendation;
- every artifact has schema/generator/run/hash metadata;
- severity is one of P0–P3;
- URLs use HTTP or HTTPS.

Implement `atomic_write_artifact()` by serializing canonical JSON, hashing the payload, writing to a sibling temporary file, flushing and `os.fsync()`, validating via `ArtifactEnvelope`, then `os.replace()`.

- [ ] **Step 4: Export generated JSON schemas and verify GREEN**

Add `export_schemas(output_dir: Path)` that writes each Pydantic model schema. Run:

```powershell
python -m pytest tests/unit/test_models.py tests/unit/test_artifacts.py -q
python -c "from pathlib import Path; from openclaw_web.models import export_schemas; export_schemas(Path('schemas/generated'))"
```

Expected: all tests pass and generated schemas exist.

- [ ] **Step 5: Commit models and schemas**

```powershell
git add src/openclaw_web/models.py src/openclaw_web/artifacts.py schemas/generated tests/unit/test_models.py tests/unit/test_artifacts.py
git commit -m "feat: add canonical audit models"
```

### Task 3: SQLite migrations, repositories, run locks, and deduplication

**Files:**
- Create: `src/openclaw_web/db/__init__.py`
- Create: `src/openclaw_web/db/connection.py`
- Create: `src/openclaw_web/db/migrations.py`
- Create: `src/openclaw_web/db/repository.py`
- Test: `tests/unit/test_repository.py`
- Test: `tests/unit/test_run_lock.py`

- [ ] **Step 1: Write failing persistence tests**

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository


def test_candidate_domain_is_deduplicated(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    first = repo.upsert_candidate("https://www.example.com", "Example Co", "Hà Nội")
    second = repo.upsert_candidate("https://example.com/", "Example Co", "Hà Nội")
    assert first.candidate_id == second.candidate_id
    assert repo.count_candidates() == 1


def test_schedule_lock_rejects_overlap_and_reclaims_expired_lease(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    now = datetime.now(UTC)
    assert repo.acquire_run_lock("hanoi-80km:2026-08-12", "owner-a", now, 300)
    assert not repo.acquire_run_lock("hanoi-80km:2026-08-12", "owner-b", now, 300)
    assert repo.acquire_run_lock(
        "hanoi-80km:2026-08-12", "owner-b", now + timedelta(seconds=301), 300
    )
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_repository.py tests/unit/test_run_lock.py -q
```

Expected: database modules are missing.

- [ ] **Step 3: Implement migrations and repository transactions**

Create migrations for the tables listed in the design spec. Use WAL mode, foreign keys, a five-second busy timeout, explicit `BEGIN IMMEDIATE` around mutations, and unique indexes for canonical domain, schedule idempotency key, delivery idempotency key, and component message identity.

Repository methods must include:

```python
upsert_candidate(url: str, business_name: str, address: str | None) -> Candidate
find_duplicate(candidate: CandidateSeed) -> Candidate | None
create_or_resume_run(idempotency_key: str, config_version: str) -> RunRecord
acquire_run_lock(key: str, owner: str, now: datetime, lease_seconds: int) -> bool
renew_run_lock(key: str, owner: str, now: datetime, lease_seconds: int) -> bool
release_run_lock(key: str, owner: str) -> None
append_evidence(evidence: Evidence) -> None
append_score(score: ScoreRecord) -> None
append_issue(issue: IssueRecord) -> None
enqueue_delivery(delivery: DeliveryRecord) -> DeliveryRecord
append_feedback(feedback: FeedbackEvent) -> None
```

- [ ] **Step 4: Run repository tests and existing coordinator tests**

```powershell
python -m pytest tests/unit/test_repository.py tests/unit/test_run_lock.py tests/test_workflow_coordinator.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit persistence**

```powershell
git add src/openclaw_web/db tests/unit/test_repository.py tests/unit/test_run_lock.py
git commit -m "feat: add workflow persistence"
```

### Task 4: URL safety, redirect validation, and robots policy

**Files:**
- Create: `src/openclaw_web/crawl/__init__.py`
- Create: `src/openclaw_web/crawl/safety.py`
- Create: `src/openclaw_web/crawl/robots.py`
- Test: `tests/unit/test_url_safety.py`
- Test: `tests/unit/test_robots.py`

- [ ] **Step 1: Write failing security tests**

```python
import ipaddress

import pytest

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url, validate_resolved_ips
from openclaw_web.crawl.robots import RobotsPolicy


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ],
)
def test_unsafe_targets_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeTarget):
        normalize_url(url)


def test_resolved_private_ip_is_rejected() -> None:
    with pytest.raises(UnsafeTarget):
        validate_resolved_ips([ipaddress.ip_address("10.1.2.3")])


def test_robots_disallow_and_delay_are_honored() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private\nCrawl-delay: 3\n")
    assert not policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/private/a")
    assert policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/public")
    assert policy.crawl_delay("AgentOpenClawAudit/1.0") == 3
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_url_safety.py tests/unit/test_robots.py -q
```

Expected: imports fail.

- [ ] **Step 3: Implement URL and robots policy**

Use `urllib.parse`, `ipaddress`, and an injected DNS resolver. Reject non-web schemes, credentials, unsafe literal hosts, and unsafe resolved addresses. Normalize host, port, path, query, and fragment. Implement `validate_redirect(source, target, resolver)` to repeat DNS validation on each destination and never forward sensitive headers. Treat these checks as the crawler's SSRF boundary and keep them mandatory for HTTPX, Playwright navigation, asset probes, and every redirect.

Wrap `urllib.robotparser` with explicit crawl-delay parsing and a safe default that treats an unavailable robots file as allow-with-rate-limit while recording the fetch error. A robots response that explicitly disallows the homepage is terminal.

- [ ] **Step 4: Run focused and security regression tests**

```powershell
python -m pytest tests/unit/test_url_safety.py tests/unit/test_robots.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit crawl security**

```powershell
git add src/openclaw_web/crawl tests/unit/test_url_safety.py tests/unit/test_robots.py
git commit -m "feat: enforce safe crawl policy"
```

### Task 5: Hanoi geofence, cohort assignment, and candidate scheduler

**Files:**
- Create: `src/openclaw_web/geofence/__init__.py`
- Create: `src/openclaw_web/geofence/distance.py`
- Create: `src/openclaw_web/geofence/service.py`
- Create: `src/openclaw_web/discovery/scheduler.py`
- Create: `config/scoring/cohorts/*.yaml`
- Test: `tests/unit/test_geofence.py`
- Test: `tests/unit/test_cohort_scheduler.py`

- [ ] **Step 1: Write failing geofence and allocation tests**

```python
from openclaw_web.geofence.distance import haversine_km
from openclaw_web.geofence.service import GeofenceService, LocationEvidence
from openclaw_web.discovery.scheduler import allocate_cohort_budget


def test_hanoi_center_is_inside_and_haiphong_is_outside_80km() -> None:
    service = GeofenceService(21.0285, 105.8542, 80)
    assert service.evaluate(LocationEvidence(latitude=21.0285, longitude=105.8542)).inside
    assert not service.evaluate(LocationEvidence(latitude=20.8449, longitude=106.6881)).inside


def test_equal_cohort_budget_is_deterministic() -> None:
    cohorts = ["manufacturer", "local-service", "ecommerce", "other"]
    assert allocate_cohort_budget(cohorts, 10) == {
        "manufacturer": 3,
        "local-service": 3,
        "ecommerce": 2,
        "other": 2,
    }
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_geofence.py tests/unit/test_cohort_scheduler.py -q
```

Expected: imports fail.

- [ ] **Step 3: Implement geofence and cohort assignment**

Implement Haversine distance. Prefer coordinates, then injected geocoder, then versioned normalized province/district fallback. Return `inside`, `distance_km`, `confidence`, and `reason`. Add deterministic keyword-based cohort assignment with `other` fallback; AI may suggest a cohort only when marked inferred.

Create cohort files for the ten approved cohorts. Each file contains business and money scoring overrides, required evidence categories, and conversion-intent vocabulary.

- [ ] **Step 4: Verify focused tests**

```powershell
python -m pytest tests/unit/test_geofence.py tests/unit/test_cohort_scheduler.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit geofence and cohorts**

```powershell
git add src/openclaw_web/geofence src/openclaw_web/discovery/scheduler.py config/scoring/cohorts tests/unit/test_geofence.py tests/unit/test_cohort_scheduler.py
git commit -m "feat: add Hanoi market geofence"
```

### Task 6: Discovery adapters and candidate service

**Files:**
- Create: `src/openclaw_web/discovery/__init__.py`
- Create: `src/openclaw_web/discovery/base.py`
- Create: `src/openclaw_web/discovery/batch.py`
- Create: `src/openclaw_web/discovery/serper.py`
- Create: `src/openclaw_web/discovery/places.py`
- Create: `src/openclaw_web/discovery/service.py`
- Test: `tests/unit/test_discovery.py`
- Test: `tests/unit/test_discovery_providers.py`
- Test: `tests/fixtures/discovery/seeds.csv`

- [ ] **Step 1: Write failing discovery tests**

```python
import json
from pathlib import Path

import httpx

from openclaw_web.discovery.batch import CsvDiscoverySource
from openclaw_web.discovery.places import GooglePlacesDiscoverySource
from openclaw_web.discovery.serper import SerperDiscoverySource
from openclaw_web.discovery.service import DiscoveryService


def test_csv_discovery_normalizes_and_deduplicates_urls(tmp_path: Path) -> None:
    path = tmp_path / "seeds.csv"
    path.write_text(
        "url,business_name,address\n"
        "https://www.example.com,Example,Hà Nội\n"
        "https://example.com/,Example,Hà Nội\n",
        encoding="utf-8",
    )
    seeds = list(CsvDiscoverySource(path).discover())
    assert len(seeds) == 2
    result = DiscoveryService().normalize_unique(seeds)
    assert len(result) == 1
    assert result[0].url == "https://example.com/"


async def test_serper_provider_builds_hanoi_cohort_queries(respx_mock) -> None:
    route = respx_mock.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200,
            json={"organic": [{"link": "https://example.com", "title": "Example"}]},
        )
    )
    seeds = await SerperDiscoverySource(api_key="secret-ref-value").discover(
        market_fixture(), cohort="manufacturer", limit=10
    )
    assert seeds[0].url == "https://example.com/"
    request_json = json.loads(route.calls[0].request.content)
    assert "Hà Nội" in request_json["q"]
    assert "nhà sản xuất" in request_json["q"]


async def test_places_provider_keeps_coordinates_and_source_url(respx_mock) -> None:
    mock_google_places_response(respx_mock, latitude=21.03, longitude=105.85)
    seeds = await GooglePlacesDiscoverySource(api_key="secret-ref-value").discover(
        market_fixture(), cohort="local-service", limit=10
    )
    assert seeds[0].latitude == 21.03
    assert seeds[0].longitude == 105.85
    assert seeds[0].source_url.startswith("https://")
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_discovery.py tests/unit/test_discovery_providers.py -q
```

Expected: discovery classes are missing.

- [ ] **Step 3: Implement provider protocol and batch sources**

Define `DiscoverySource.discover(market)`. Implement manual URL, CSV, and JSON sources. Implement concrete Serper Search and Google Places adapters with injected HTTPX clients, bounded pagination, per-provider rate limits, cohort-specific Vietnamese query templates, evidence source URLs, and typed provider errors. Keep credentials behind environment-backed settings or approved secret-provider resolution and never include keys in logs or command arguments. The service canonicalizes, geofences, cohort-tags, and repository-deduplicates seeds before returning candidates.

If no automatic provider is configured, `health` reports discovery as not ready while manual audit remains healthy.

- [ ] **Step 4: Verify discovery tests**

```powershell
python -m pytest tests/unit/test_discovery.py tests/unit/test_discovery_providers.py tests/unit/test_geofence.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit discovery**

```powershell
git add src/openclaw_web/discovery tests/unit/test_discovery.py tests/unit/test_discovery_providers.py tests/fixtures/discovery
git commit -m "feat: add bounded discovery sources"
```

### Task 7: HTML extraction, page priority, and bounded crawl service

**Files:**
- Create: `src/openclaw_web/crawl/extract.py`
- Create: `src/openclaw_web/crawl/service.py`
- Test: `tests/unit/test_extract.py`
- Test: `tests/integration/test_crawler.py`
- Create: `tests/fixtures/sites/legacy/`

- [ ] **Step 1: Write failing extraction and crawl tests**

```python
from openclaw_web.crawl.extract import extract_page


def test_extracts_headings_ctas_forms_and_evidence() -> None:
    html = """
    <html><head><title>Báo giá</title><meta name="description" content="Mô tả"></head>
    <body><h1>Nội thất văn phòng</h1><a href="/quote">Yêu cầu báo giá</a>
    <form><input name="name"><input name="phone"></form></body></html>
    """
    page = extract_page("https://example.com/", html)
    assert page.title == "Báo giá"
    assert page.headings[0].text == "Nội thất văn phòng"
    assert page.ctas[0].text == "Yêu cầu báo giá"
    assert page.forms[0].field_count == 2


async def test_crawler_stops_at_page_budget(fixture_server) -> None:
    result = await fixture_server.crawl(max_pages=3)
    assert len(result.pages) == 3
    assert result.budget_exhausted
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_extract.py tests/integration/test_crawler.py -q
```

Expected: extraction and crawler are missing.

- [ ] **Step 3: Implement extraction and crawl frontier**

Use Beautiful Soup for semantic extraction. Add CTA vocabulary for quote, consultation, booking, contact, call, Zalo, purchase, and catalog actions. Store normalized evidence excerpts, not arbitrary full HTML.

Implement an async HTTPX crawl frontier with robots checks, per-domain token-bucket rate limiting, depth/page/body/redirect limits, same-site eTLD+1 checks, URL safety revalidation, deterministic priority queue, and one inner-page failure record per failed page. Instantiate `tldextract.TLDExtract(suffix_list_urls=())` so the crawler never performs an implicit network update of the public-suffix list.

- [ ] **Step 4: Run unit and local-server integration tests**

```powershell
python -m pytest tests/unit/test_extract.py tests/integration/test_crawler.py tests/unit/test_url_safety.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit crawler**

```powershell
git add src/openclaw_web/crawl tests/unit/test_extract.py tests/integration/test_crawler.py tests/fixtures/sites/legacy
git commit -m "feat: add bounded website crawler"
```

### Task 8: Playwright screenshots and deterministic browser observations

**Files:**
- Create: `src/openclaw_web/screenshots/__init__.py`
- Create: `src/openclaw_web/screenshots/playwright_runner.py`
- Test: `tests/integration/test_screenshots.py`

- [ ] **Step 1: Write failing browser test**

```python
from pathlib import Path

from openclaw_web.screenshots.playwright_runner import ScreenshotRunner


async def test_captures_three_viewports_without_submitting_forms(
    fixture_server, tmp_path: Path
) -> None:
    result = await ScreenshotRunner(tmp_path).capture(fixture_server.url("/form"))
    assert {shot.viewport for shot in result.screenshots} == {"desktop", "tablet", "mobile"}
    assert all(shot.path.exists() for shot in result.screenshots)
    assert fixture_server.form_submissions == 0
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/integration/test_screenshots.py -q
```

Expected: screenshot runner is missing.

- [ ] **Step 3: Implement isolated browser capture**

Create one fresh context per candidate. Block downloads and permission grants. Never fill or submit forms. Capture desktop, tablet, and mobile viewport images plus bounded full-page images. Record console errors, failed requests, horizontal overflow, visible CTA observations, and obstruction candidates. Retry once with a clean browser context, then return a structured partial result.

- [ ] **Step 4: Install Chromium and verify browser tests**

```powershell
python -m playwright install chromium
python -m pytest tests/integration/test_screenshots.py -q
```

Expected: all pass and screenshots are generated under pytest temp storage only.

- [ ] **Step 5: Commit screenshot engine**

```powershell
git add src/openclaw_web/screenshots tests/integration/test_screenshots.py
git commit -m "feat: capture responsive audit evidence"
```

### Task 9: Built-in technical audit and Lighthouse adapter

**Files:**
- Create: `src/openclaw_web/audit/__init__.py`
- Create: `src/openclaw_web/audit/builtin.py`
- Create: `src/openclaw_web/audit/lighthouse.py`
- Create: `src/openclaw_web/audit/service.py`
- Test: `tests/unit/test_builtin_audit.py`
- Test: `tests/unit/test_lighthouse.py`

- [ ] **Step 1: Write failing audit tests**

```python
from openclaw_web.audit.builtin import audit_page
from openclaw_web.audit.lighthouse import parse_lighthouse


def test_builtin_audit_flags_missing_metadata_and_long_form(extracted_page) -> None:
    extracted_page.meta_description = None
    extracted_page.forms[0].field_count = 12
    result = audit_page(extracted_page)
    assert {finding.rule_id for finding in result.findings} >= {
        "META-DESCRIPTION-MISSING",
        "FORM-LONG",
    }


def test_lighthouse_parser_normalizes_core_metrics() -> None:
    result = parse_lighthouse(
        {"categories": {"performance": {"score": 0.42}},
         "audits": {"largest-contentful-paint": {"numericValue": 5100}}}
    )
    assert result.performance_score == 42
    assert result.lcp_ms == 5100
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_builtin_audit.py tests/unit/test_lighthouse.py -q
```

Expected: audit modules are missing.

- [ ] **Step 3: Implement audit adapters**

Implement built-in checks for transport, redirects, metadata, headings, alt coverage, broken assets/links, mixed content, CTA presence, form length, mobile viewport, stale dates, DOM/request size, and browser errors.

Implement a Lighthouse subprocess adapter with an argument list rather than shell interpolation, bounded timeout, isolated output file, JSON parse validation, and structured unavailable status when the executable is missing or fails.

- [ ] **Step 4: Run audit tests**

```powershell
python -m pytest tests/unit/test_builtin_audit.py tests/unit/test_lighthouse.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit audit adapters**

```powershell
git add src/openclaw_web/audit tests/unit/test_builtin_audit.py tests/unit/test_lighthouse.py
git commit -m "feat: add technical audit adapters"
```

### Task 10: Versioned scoring and upgrade prioritization

**Files:**
- Create: `src/openclaw_web/scoring/__init__.py`
- Create: `src/openclaw_web/scoring/rules.py`
- Create: `src/openclaw_web/scoring/engine.py`
- Create: `src/openclaw_web/scoring/priority.py`
- Create: `config/scoring/base-v1.yaml`
- Test: `tests/unit/test_scoring.py`
- Test: `tests/unit/test_upgrade_priority.py`

- [ ] **Step 1: Write failing scoring tests**

```python
from openclaw_web.scoring.engine import calculate_lead_score
from openclaw_web.scoring.priority import rank_issues


def test_lead_score_uses_approved_weights() -> None:
    score = calculate_lead_score(80, 90, 70, 60)
    assert score == 79.0


def test_p0_always_precedes_higher_numeric_p1() -> None:
    issues = [
        {"issue_id": "p1", "severity": "P1", "priority": 99},
        {"issue_id": "p0", "severity": "P0", "priority": 60},
    ]
    assert [item["issue_id"] for item in rank_issues(issues)] == ["p0", "p1"]


def test_missing_metric_reduces_confidence_not_points_as_pass(rule_engine) -> None:
    result = rule_engine.evaluate({})
    assert "lighthouse.lcp_ms" in result.unavailable_inputs
    assert result.confidence < 1.0
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_scoring.py tests/unit/test_upgrade_priority.py -q
```

Expected: scoring modules are missing.

- [ ] **Step 3: Implement rule loader, engine, and formulas**

Parse strict YAML rules with Pydantic. Support explicit operators `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `contains`, `missing`, and `present`. Reject unknown inputs/operators and score overflow. Apply base rules then the assigned cohort override. Emit matched rule IDs, evidence IDs, unavailable inputs, confidence, and rubric version.

Implement the approved LeadScore and UpgradePriority formulas. Mark threshold 75 as provisional in score metadata.

- [ ] **Step 4: Verify deterministic scoring**

```powershell
python -m pytest tests/unit/test_scoring.py tests/unit/test_upgrade_priority.py -q
```

Expected: all pass. Add an assertion that two evaluations of the same input serialize identically.

- [ ] **Step 5: Commit scoring**

```powershell
git add src/openclaw_web/scoring config/scoring/base-v1.yaml tests/unit/test_scoring.py tests/unit/test_upgrade_priority.py
git commit -m "feat: add reproducible lead scoring"
```

### Task 11: Schema-constrained AI review and Vietnamese dossier

**Files:**
- Create: `src/openclaw_web/review/__init__.py`
- Create: `src/openclaw_web/review/ai.py`
- Create: `src/openclaw_web/review/openclaw_model.py`
- Create: `src/openclaw_web/review/dossier.py`
- Test: `tests/unit/test_ai_review.py`
- Test: `tests/unit/test_openclaw_model.py`
- Test: `tests/unit/test_dossier.py`

- [ ] **Step 1: Write failing review tests**

```python
from pathlib import Path

from openclaw_web.review.ai import ReviewService
from openclaw_web.review.dossier import render_dossier
from openclaw_web.review.openclaw_model import OpenClawAgentModel


async def test_invalid_model_json_retries_once_then_falls_back(fake_model) -> None:
    fake_model.responses = ["not json", "still not json"]
    result = await ReviewService(fake_model).review(normalized_bundle())
    assert result.status == "failed"
    assert fake_model.call_count == 2
    assert result.deterministic_fallback


def test_dossier_labels_estimates_and_lists_evidence() -> None:
    markdown = render_dossier(review_fixture())
    assert "Ước tính" in markdown
    assert "Confidence gaps" in markdown
    assert "https://example.com/" in markdown


def test_openclaw_model_uses_message_file_and_argument_array(tmp_path: Path) -> None:
    command = OpenClawAgentModel.build_command(
        agent_id="curie", prompt_path=tmp_path / "prompt.txt", timeout_seconds=300
    )
    assert command == [
        "openclaw", "agent", "--agent", "curie", "--message-file",
        str(tmp_path / "prompt.txt"), "--timeout", "300", "--json",
    ]
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_ai_review.py tests/unit/test_openclaw_model.py tests/unit/test_dossier.py -q
```

Expected: review modules are missing.

- [ ] **Step 3: Implement review protocol and guarded prompt assembly**

Define an injected async model protocol. Delimit crawled content as untrusted evidence. Ask only for schema fields allowed by the design. Validate output, attempt one JSON parse repair, and make one retry including compact validation errors. Never allow model output to alter measured metrics or deterministic rule matches.

Implement `OpenClawAgentModel` as the production adapter. It writes a non-secret evidence prompt to a permission-restricted temporary file, invokes `openclaw agent` with an argument array and `--message-file`, parses `--json` output, enforces a bounded timeout, removes the temporary file in `finally`, and selects `curie` or `website-brief` explicitly by stage. It never places evidence, credentials, or model output in a shell command string.

Render a Vietnamese dossier with report status, project ID, business identity, summary, business evidence, audit by page, conversion hypothesis, top issues, redesign angle, evidence matrix, image evidence, confidence gaps, and next action.

- [ ] **Step 4: Run review tests**

```powershell
python -m pytest tests/unit/test_ai_review.py tests/unit/test_openclaw_model.py tests/unit/test_dossier.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit review layer**

```powershell
git add src/openclaw_web/review tests/unit/test_ai_review.py tests/unit/test_openclaw_model.py tests/unit/test_dossier.py
git commit -m "feat: generate evidence-backed lead dossiers"
```

### Task 12: Audit pipeline orchestration and resumable stages

**Files:**
- Create: `src/openclaw_web/pipeline/__init__.py`
- Create: `src/openclaw_web/pipeline/stages.py`
- Create: `src/openclaw_web/pipeline/audit.py`
- Modify: `src/openclaw_web/cli.py`
- Test: `tests/integration/test_audit_pipeline.py`

- [ ] **Step 1: Write failing end-to-stage test**

```python
async def test_audit_pipeline_creates_review_ready_artifacts(
    fixture_candidate, pipeline, project_root
) -> None:
    result = await pipeline.audit(fixture_candidate)
    assert result.state == "review-ready"
    for name in (
        "candidate.json", "evidence.json", "pages.json",
        "scores.json", "issues.json", "dossier.md", "curie-to-website.json",
    ):
        assert (project_root / result.project_id / name).exists()


async def test_completed_stage_is_reused_when_input_hash_matches(pipeline, fixture_candidate) -> None:
    first = await pipeline.audit(fixture_candidate)
    second = await pipeline.audit(fixture_candidate)
    assert second.reused_stage_ids == first.completed_stage_ids
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/integration/test_audit_pipeline.py -q
```

Expected: pipeline does not exist.

- [ ] **Step 3: Implement stage runner and audit orchestration**

Each stage records input hash, output hash, status, timestamps, and error code. Reuse only successful output with matching input and generator version. Orchestrate crawl, screenshots, audits, scoring, AI review, dossier, and Curie handoff. Apply Gate 1 before `review-ready`; otherwise return `unqualified`, `partial`, or terminal failure with artifacts preserved.

Replace the `audit` CLI stub with a real async command supporting project root, database, page budget, browser toggle, and JSON result output.

- [ ] **Step 4: Verify audit pipeline and CLI**

```powershell
python -m pytest tests/integration/test_audit_pipeline.py tests/unit/test_cli.py -q
openclaw-web audit --help
```

Expected: tests pass and help shows bounded options.

- [ ] **Step 5: Commit orchestration**

```powershell
git add src/openclaw_web/pipeline src/openclaw_web/cli.py tests/integration/test_audit_pipeline.py
git commit -m "feat: orchestrate resumable website audits"
```

### Task 13: Website Brief artifact generator and cross-artifact validation

**Files:**
- Create: `src/openclaw_web/brief/__init__.py`
- Create: `src/openclaw_web/brief/stages.py`
- Create: `src/openclaw_web/brief/generator.py`
- Create: `src/openclaw_web/brief/validator.py`
- Modify: `src/openclaw_web/cli.py`
- Test: `tests/integration/test_brief_pipeline.py`
- Test: `tests/unit/test_brief_validator.py`

- [ ] **Step 1: Write failing brief and validator tests**

```python
import pytest

from openclaw_web.brief.validator import BriefValidationError, validate_brief_package


async def test_approved_lead_generates_required_brief_artifacts(brief_pipeline, approved_project) -> None:
    result = await brief_pipeline.generate(approved_project.project_id)
    assert result.valid
    assert set(result.artifacts) >= {
        "website-analysis.json", "content-inventory.json", "visual-inventory.json",
        "design-audit.json", "competitive-positioning.json", "redesign-brief.json",
        "visual-worlds.json", "design-genome.json", "design-tokens.json",
        "component-system.json", "page-blueprints.json", "DESIGN.md",
    }


def test_page_blueprint_rejects_missing_content_reference(brief_fixture) -> None:
    brief_fixture.page_blueprints[0].sections[0].content_ids = ["missing"]
    with pytest.raises(BriefValidationError):
        validate_brief_package(brief_fixture)
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/integration/test_brief_pipeline.py tests/unit/test_brief_validator.py -q
```

Expected: brief modules are missing.

- [ ] **Step 3: Implement hash-addressed Website Brief stages**

Implement evidence refresh, business truth, content inventory, visual inventory, section analysis, design audit, competitive positioning, redesign mode, five-to-seven visual worlds, weighted selection, design genome, tokens, components, page blueprints, and DESIGN.md rendering. Use injected model calls only for interpretation/generation stages and validate each artifact before continuing.

Cross-artifact validator rejects unresolved content/proof/component/token/genome references, missing conversion intent, unsupported final claims, duplicate section order, missing responsive rules, and interactive components without states.

Replace `brief` and `validate` CLI stubs.

- [ ] **Step 4: Verify generation and validation**

```powershell
python -m pytest tests/integration/test_brief_pipeline.py tests/unit/test_brief_validator.py -q
```

Expected: all pass and the fixture project produces the required package.

- [ ] **Step 5: Commit Website Brief pipeline**

```powershell
git add src/openclaw_web/brief src/openclaw_web/cli.py tests/integration/test_brief_pipeline.py tests/unit/test_brief_validator.py
git commit -m "feat: generate validated website briefs"
```

### Task 14: Homepage proof renderer and deterministic validation

**Files:**
- Create: `src/openclaw_web/render/__init__.py`
- Create: `src/openclaw_web/render/homepage.py`
- Create: `src/openclaw_web/render/templates/homepage.html.j2`
- Test: `tests/integration/test_homepage_render.py`

- [ ] **Step 1: Write failing renderer test**

```python
async def test_homepage_concept_renders_and_blocks_p0_p1_handoff(
    homepage_renderer, brief_fixture, tmp_path
) -> None:
    result = await homepage_renderer.render_and_validate(brief_fixture, tmp_path)
    assert result.html_path.exists()
    assert {shot.viewport for shot in result.screenshots} == {"desktop", "tablet", "mobile"}
    assert not result.unresolved_p0_p1
    assert result.validation_report.exists()
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/integration/test_homepage_render.py -q
```

Expected: render module is missing.

- [ ] **Step 3: Implement semantic renderer and checks**

Render semantic HTML from content IDs, blueprint sections, component definitions, and CSS variables generated from tokens into `<project_root>/homepage-concept.html`. Do not invent content. Implement deterministic checks for heading order, primary CTA visibility, missing proof references, contrast, focus styles, touch targets, reduced motion, horizontal overflow, broken images, and missing alt text. Capture three viewport screenshots.

Only create `website-to-pm.json` when cross-artifact validation passes and no P0/P1 remains.

- [ ] **Step 4: Verify renderer**

```powershell
python -m pytest tests/integration/test_homepage_render.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit proof renderer**

```powershell
git add src/openclaw_web/render tests/integration/test_homepage_render.py
git commit -m "feat: validate homepage proof concepts"
```

### Task 15: Feedback events and calibration reports

**Files:**
- Create: `src/openclaw_web/feedback/__init__.py`
- Create: `src/openclaw_web/feedback/calibration.py`
- Modify: `src/openclaw_web/cli.py`
- Test: `tests/unit/test_calibration.py`

- [ ] **Step 1: Write failing calibration test**

```python
def test_calibration_report_groups_reject_reasons_without_mutating_rubric(repository) -> None:
    repository.append_feedback(feedback("p1", "reject", "business-too-weak", 82))
    repository.append_feedback(feedback("p2", "approve", None, 78))
    before = scoring_file_hash()
    report = build_calibration_report(repository)
    assert report.approve_rate == 0.5
    assert report.reject_reasons == {"business-too-weak": 1}
    assert scoring_file_hash() == before
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_calibration.py -q
```

Expected: calibration module is missing.

- [ ] **Step 3: Implement immutable feedback and report generation**

Validate reason codes and actor/project identity. Store immutable events with score snapshot, rubric, and cohort. Generate JSON and Markdown reports for approval rate, reject reasons, score distributions, cohort precision, missing evidence, adapter failures, and threshold simulations. Proposed rule changes may be written only to a `.proposed.yaml` path and never activated automatically.

Replace `feedback` and `calibration report` CLI stubs.

- [ ] **Step 4: Verify calibration**

```powershell
python -m pytest tests/unit/test_calibration.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit feedback loop**

```powershell
git add src/openclaw_web/feedback src/openclaw_web/cli.py tests/unit/test_calibration.py
git commit -m "feat: record lead review feedback"
```

### Task 16: Delivery outbox and workflow coordinator adapter

**Files:**
- Create: `src/openclaw_web/delivery/__init__.py`
- Create: `src/openclaw_web/delivery/openclaw_transport.py`
- Create: `src/openclaw_web/delivery/outbox.py`
- Create: `src/openclaw_web/delivery/workflow_adapter.py`
- Modify: `src/openclaw_web/cli.py`
- Test: `tests/unit/test_outbox.py`
- Test: `tests/unit/test_openclaw_transport.py`
- Test: `tests/unit/test_workflow_adapter.py`

- [ ] **Step 1: Write failing outbox tests**

```python
import sys
from pathlib import Path

from openclaw_web.delivery.openclaw_transport import OpenClawAgentTransport

def test_delivery_retry_does_not_duplicate_sent_message(repository, fake_transport) -> None:
    record = repository.enqueue_delivery(review_delivery("project-1"))
    fake_transport.send_result = SentMessage("message-1", "https://discord.com/channels/g/c/message-1")
    worker = OutboxWorker(repository, fake_transport)
    worker.dispatch_once()
    worker.dispatch_once()
    assert fake_transport.call_count == 1
    assert repository.get_delivery(record.delivery_id).status == "sent"


def test_workflow_adapter_uses_argument_list_not_shell(tmp_path) -> None:
    command = WorkflowCoordinatorAdapter(tmp_path / "workflow-coordinator.py").approve(
        "project-1", "620891893659598850", dry_run=True
    )
    assert command.argv[:3] == [sys.executable, str(tmp_path / "workflow-coordinator.py"), "approve"]
    assert command.shell is False


def test_openclaw_transport_hands_off_payload_by_path(tmp_path: Path) -> None:
    payload = tmp_path / "delivery.json"
    payload.write_text('{"delivery_id":"d1"}', encoding="utf-8")
    command = OpenClawAgentTransport.build_command(payload, timeout_seconds=300)
    assert command == [
        "openclaw", "agent", "--agent", "main", "--message-file",
        str(payload.with_suffix(".handoff.txt")), "--timeout", "300", "--json",
    ]
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_outbox.py tests/unit/test_openclaw_transport.py tests/unit/test_workflow_adapter.py -q
```

Expected: delivery modules are missing.

- [ ] **Step 3: Implement outbox and coordinator subprocess adapter**

Claim pending rows transactionally, mark sending, call an injected OpenClaw delivery transport, record bot message ID/URL, and retry once only after idempotency inspection. Failed delivery preserves the project and error.

Implement `OpenClawAgentTransport` as the production transport. It stores the validated Components payload under the project delivery directory, creates a short permission-restricted handoff message containing only the payload path and delivery ID, invokes `openclaw agent --agent main --message-file ... --json` with an argument array, validates the returned bot message ID/channel/URL, then records the result through the outbox. The payload is never pasted into the command line. A missing/invalid message identity keeps the outbox row failed rather than guessing delivery success.

Implement coordinator calls as subprocess argument arrays with bounded timeouts and parsed JSON/stdout status. Buttons and delivery never modify project JSON directly.

- [ ] **Step 4: Verify outbox behavior**

```powershell
python -m pytest tests/unit/test_outbox.py tests/unit/test_openclaw_transport.py tests/unit/test_workflow_adapter.py tests/test_workflow_coordinator.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit delivery layer**

```powershell
git add src/openclaw_web/delivery src/openclaw_web/cli.py tests/unit/test_outbox.py tests/unit/test_openclaw_transport.py tests/unit/test_workflow_adapter.py
git commit -m "feat: add idempotent Discord delivery outbox"
```

### Task 17: Discord component cards and validated project actions

**Files:**
- Create: `src/openclaw_web/delivery/components.py`
- Modify: `src/openclaw_web/cli.py`
- Modify: `agents/shared/COORDINATOR.md`
- Modify: `agents/shared/contracts/workflow-commands.md`
- Test: `tests/unit/test_components.py`
- Modify: `tests/test_workflow_coordinator.py`

- [ ] **Step 1: Write failing card and action tests**

```python
def test_review_card_applies_per_button_allowlists() -> None:
    card = build_review_card(project_fixture())
    buttons = {button.label: button for button in card.buttons}
    assert buttons["Approve"].allowed_users == [MINH_ID, WIEN_ID]
    assert buttons["Reject"].allowed_users == [MINH_ID]
    assert buttons["Request changes"].allowed_users == [MINH_ID]


def test_stale_component_action_is_rejected(repository, workflow_adapter) -> None:
    component = repository.insert_component_set(component_fixture(state_version=2))
    repository.set_project_state_version(component.project_id, 3)
    result = ComponentActionService(repository, workflow_adapter).execute(
        message_id=component.message_id,
        actor_id=MINH_ID,
        action="approve",
    )
    assert result.status == "stale"
    assert workflow_adapter.call_count == 0


def test_unauthorized_actor_never_reaches_coordinator(repository, workflow_adapter) -> None:
    result = action_service(repository, workflow_adapter).execute(
        message_id="review-message", actor_id="unauthorized", action="approve"
    )
    assert result.status == "unauthorized"
    assert workflow_adapter.call_count == 0
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_components.py tests/test_workflow_coordinator.py -q
```

Expected: component builder/service is missing.

- [ ] **Step 3: Implement component payloads and callback service**

Build OpenClaw Components v2 payloads for review, page, and final cards. Record component set, bot-owned message, channel, project, allowed actions, expiry, and state version. Validate channel, message, actor, expiry, state, checklist, P0/P1, and idempotency before invoking the existing coordinator.

Support modal reason data for reject, request-change, and block when the installed runtime exposes forms. Feature-detect `agentComponents.enabled` and `agentComponents.ttlMs`; omit unsupported config keys. Return Vietnamese stale/expired/unauthorized guidance and typed fallback commands.

Replace `component-action` CLI stub.

- [ ] **Step 4: Update main-agent instructions and verify tests**

Document the exact inbound component envelope and command invocation. Add `/page-approve` to all command lists. Ensure typed actions and button actions use the same coordinator subcommands.

```powershell
python -m pytest tests/unit/test_components.py tests/test_workflow_coordinator.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit project buttons**

```powershell
git add src/openclaw_web/delivery/components.py src/openclaw_web/cli.py agents/shared/COORDINATOR.md agents/shared/contracts/workflow-commands.md tests/unit/test_components.py tests/test_workflow_coordinator.py
git commit -m "feat: add Discord project action cards"
```

### Task 18: Cron runner, structured logs, health, and retention

**Files:**
- Create: `src/openclaw_web/pipeline/cron.py`
- Create: `src/openclaw_web/observability.py`
- Create: `src/openclaw_web/health.py`
- Modify: `src/openclaw_web/cli.py`
- Create: `deploy/openclaw-web-discovery.service`
- Create: `deploy/openclaw-web-discovery.timer`
- Test: `tests/unit/test_cron.py`
- Test: `tests/unit/test_health.py`

- [ ] **Step 1: Write failing cron and health tests**

```python
from datetime import date

def test_overlapping_cron_run_exits_as_skipped(repository, cron_runner) -> None:
    repository.acquire_run_lock("hanoi-80km:2026-08-12", "existing", now(), 10800)
    result = cron_runner.run(schedule_date=date(2026, 8, 12))
    assert result.status == "skipped-overlap"
    assert result.exit_code == 0


def test_health_reports_missing_discovery_provider_without_breaking_manual_audit(settings) -> None:
    settings.discovery_providers = []
    report = HealthService(settings).check()
    assert report.manual_audit_ready
    assert not report.discovery_ready
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_cron.py tests/unit/test_health.py -q
```

Expected: cron and health services are missing.

- [ ] **Step 3: Implement cron budgets and health checks**

Apply the approved daily budgets and 180-minute cap. Acquire/renew/release a leased lock. Return `candidate-posted`, `no-candidate-defensible`, `partial`, `failed`, or `skipped-overlap`. Enqueue at most one review candidate. Implement JSON log events with redaction and health checks for DB, schemas, browser, Lighthouse, artifact root, market/rubrics, provider readiness, OpenClaw CLI, gateway, Discord, timer, last run, and outbox.

Implement retention that removes only expired rejected-candidate screenshots, run logs, and failed temporary files. Never delete approved artifacts or backups.

- [ ] **Step 4: Add user service and timer templates**

Service:

```ini
[Unit]
Description=OpenClaw website discovery run
After=network-online.target openclaw-gateway.service

[Service]
Type=oneshot
EnvironmentFile=%h/.config/openclaw-web/env
ExecStart=%h/.local/share/openclaw-web/venv/bin/openclaw-web cron-run
TimeoutStartSec=3h15m
```

Timer:

```ini
[Unit]
Description=Daily OpenClaw website discovery

[Timer]
OnCalendar=*-*-* 07:30:00 Asia/Bangkok
Persistent=true
RandomizedDelaySec=300
Unit=openclaw-web-discovery.service

[Install]
WantedBy=timers.target
```

Replace `health`, `discover`, and `cron-run` CLI stubs.

- [ ] **Step 5: Verify cron and health**

```powershell
python -m pytest tests/unit/test_cron.py tests/unit/test_health.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit operations layer**

```powershell
git add src/openclaw_web/pipeline/cron.py src/openclaw_web/observability.py src/openclaw_web/health.py src/openclaw_web/cli.py deploy/openclaw-web-discovery.service deploy/openclaw-web-discovery.timer tests/unit/test_cron.py tests/unit/test_health.py
git commit -m "feat: schedule daily lead discovery"
```

### Task 19: Reconcile contracts and operational documentation

**Files:**
- Modify: `agents/shared/contracts/curie-to-website.yaml`
- Modify: `agents/shared/contracts/website-to-pm.yaml`
- Modify: `agents/project-pm/README.md`
- Modify: `agents/website-brief/README.md`
- Modify: `agents/website-brief/TASK.md`
- Modify: `agents/curie/CURRENT-STATE.md`
- Modify: `agents/curie/TASK.md`
- Modify: `README.md`
- Create: `docs/runbooks/web-audit-discovery.md`
- Test: `tests/unit/test_contract_consistency.py`

- [ ] **Step 1: Write failing contract consistency tests**

```python
def test_contracts_allow_both_review_approvers(repo_root: Path) -> None:
    text = (repo_root / "agents/shared/contracts/curie-to-website.yaml").read_text("utf-8")
    assert MINH_ID in text
    assert WIEN_ID in text
    assert "without Minh's actor ID" not in text


def test_docs_use_final_confirm_and_page_approve(repo_root: Path) -> None:
    docs = "\n".join(path.read_text("utf-8") for path in documentation_paths(repo_root))
    assert "/finalize <project>" not in docs
    assert "/final-confirm <project_id>" in docs
    assert "/page-approve <project_id> <page_slug>" in docs
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/unit/test_contract_consistency.py -q
```

Expected: tests fail on current documented contradictions.

- [ ] **Step 3: Update contracts and runbook**

Document implemented package commands, artifact paths, market config, provider setup, dry-run, health, timer, button refresh/fallback, retention, backup, deployment, rollback, and troubleshooting. Correct approval actors and command names. Distinguish OpenClaw exec-approval buttons from project-action cards.

- [ ] **Step 4: Verify consistency tests**

```powershell
python -m pytest tests/unit/test_contract_consistency.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit documentation reconciliation**

```powershell
git add agents README.md docs/runbooks/web-audit-discovery.md tests/unit/test_contract_consistency.py
git commit -m "docs: align web audit workflow contracts"
```

### Task 20: End-to-end fixture workflow and quality gates

**Files:**
- Create: `tests/integration/test_end_to_end.py`
- Create: `tests/fixtures/sites/manufacturer/`
- Create: `tests/fixtures/sites/local-service/`
- Create: `tests/fixtures/sites/robots-blocked/`
- Create: `tests/fixtures/sites/modern/`
- Create: `tests/fixtures/sites/legacy/`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the failing end-to-end test**

```python
async def test_fixture_candidate_reaches_pm_handoff_only_after_gates(system_fixture) -> None:
    run = await system_fixture.discover_and_audit()
    assert run.status == "candidate-posted"
    project = system_fixture.repository.get_project(run.project_id)
    assert project.state == "review"

    await system_fixture.click_review_approve(actor_id=WIEN_ID)
    assert system_fixture.repository.get_project(run.project_id).state == "approved"

    brief = await system_fixture.generate_brief(run.project_id)
    assert brief.homepage_validation == "passed"
    assert brief.unresolved_p0_p1 == []
    assert (brief.project_root / "website-to-pm.json").exists()


async def test_unresolved_p1_blocks_pm_handoff(system_fixture) -> None:
    project = await system_fixture.project_with_unresolved_issue("P1")
    result = await system_fixture.generate_brief(project.project_id)
    assert result.homepage_validation == "failed"
    assert not (result.project_root / "website-to-pm.json").exists()
```

- [ ] **Step 2: Confirm RED**

```powershell
python -m pytest tests/integration/test_end_to_end.py -q
```

Expected: fixture harness and complete integration do not yet exist.

- [ ] **Step 3: Build local fixture harness and complete adapters**

Serve fixture sites from local ephemeral ports, inject fake discovery, geocoder, model, OpenClaw transport, and Lighthouse results, and use the real DB, pipeline, validators, renderer, outbox, and component service. Ensure fixture contents cover all design failure categories without Internet access.

- [ ] **Step 4: Run the full quality suite**

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy src/openclaw_web
python -m build
git diff --check
```

Expected: zero failed tests, zero lint/type errors, successful wheel/sdist build, and no whitespace errors.

- [ ] **Step 5: Commit end-to-end coverage**

```powershell
git add tests pyproject.toml
git commit -m "test: cover web audit workflow end to end"
```

### Task 21: Local installation, dry run, and artifact inspection

**Files:**
- Create: `deploy/install-local.ps1`
- Modify: `docs/runbooks/web-audit-discovery.md`
- Test: local commands only

- [ ] **Step 1: Create a clean local virtual environment**

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m playwright install chromium
```

- [ ] **Step 2: Verify CLI and health in local/manual mode**

```powershell
.\.venv\Scripts\openclaw-web --help
.\.venv\Scripts\openclaw-web health --json
.\.venv\Scripts\openclaw-web cron-run --dry-run --json
```

Expected: manual audit readiness passes; automatic discovery readiness reflects whether a provider is configured; dry run performs no external crawl or Discord delivery.

- [ ] **Step 3: Run bounded local fixture audit and brief**

```powershell
.\.venv\Scripts\python -m pytest tests/integration/test_end_to_end.py -q
```

Inspect the generated pytest temporary project and confirm every required artifact validates and no secret-like key appears.

- [ ] **Step 4: Run secret and artifact scans**

```powershell
rg -n --hidden -g '!\.git/**' -g '!\.venv/**' '(OPENCLAW_SSH_PASSWORD=|DISCORD_BOT_TOKEN=|api[_-]?key\s*[:=]\s*["'"'][^"'"']+)' .
python -m pytest tests/unit/test_artifacts.py tests/unit/test_contract_consistency.py -q
```

Expected: no credential values and all validation tests pass.

- [ ] **Step 5: Commit local install tooling**

```powershell
git add deploy/install-local.ps1 docs/runbooks/web-audit-discovery.md
git commit -m "chore: add local web audit installer"
```

### Task 22: Remote deployment preflight and approval gate

**Files:**
- Create: `deploy/install-remote.sh`
- Create: `deploy/rollback-remote.sh`
- Modify: `docs/runbooks/web-audit-discovery.md`

- [ ] **Step 1: Write deployment scripts without embedded secrets**

`install-remote.sh` must:

- require explicit artifact/source path arguments;
- verify paths remain under the approved OpenClaw workflow/application roots;
- create a timestamped backup of workflow files and OpenClaw config;
- preserve ownership and permissions;
- create a project-owned venv;
- install the built wheel and approved browser/Lighthouse dependencies;
- install user service/timer files without enabling them yet;
- run package health and OpenClaw config validation;
- print the rollback timestamp and no secret values.

`rollback-remote.sh` must stop/disable only the new timer/service, restore the selected timestamped workflow/config backups, validate OpenClaw, and preserve DB/artifacts.

- [ ] **Step 2: Run shell syntax checks locally**

```powershell
bash -n deploy/install-remote.sh
bash -n deploy/rollback-remote.sh
```

Expected: exit 0.

- [ ] **Step 3: Run read-only remote health and feature detection**

Use the repository-approved SSH access method. Run the documented health checks, inspect only non-secret component/config fields, verify disk/memory, Python/Node/browser prerequisites, and determine whether installed OpenClaw supports `agentComponents.ttlMs`.

- [ ] **Step 4: Present exact mutation and wait for explicit approval**

Report:

- package and dependency install paths;
- workflow/config paths changed;
- user service and timer names;
- expected resource use and Discord interruption;
- backup paths;
- rollback commands;
- verification commands.

Do not mutate the remote host until the user approves this exact deployment scope.

- [ ] **Step 5: Commit deployment tooling**

```powershell
git add deploy/install-remote.sh deploy/rollback-remote.sh docs/runbooks/web-audit-discovery.md
git commit -m "chore: add web audit deployment workflow"
```

### Task 23: Approved remote deployment and controlled live smoke

**Files:**
- Remote package installation and configuration only after Task 22 approval

- [ ] **Step 1: Back up approved remote paths**

Create timestamped backups of OpenClaw config, workflow coordinator/contracts/instructions, and any existing service/timer files. Record mode and owner without printing file contents.

- [ ] **Step 2: Install and validate without enabling automation**

Install the wheel/venv, browser and Lighthouse dependencies, market/rubric config, workflow files, and service/timer units. Run:

```text
openclaw-web health --json
openclaw-web cron-run --dry-run --json
openclaw config validate
openclaw health
openclaw channels status --channel discord --probe
```

Expected: required health passes; no live discovery or Discord post occurs.

- [ ] **Step 3: Run one bounded manual live audit**

Use an explicitly approved public URL, `max_pages <= 3`, one full audit, and no automatic Discord delivery. Validate artifacts, screenshots, scoring trace, and absence of unsupported claims.

- [ ] **Step 4: Test project cards in trusted channels**

Enable only feature-supported agent component fields after config backup. Restart the gateway only if CLI requires it. Send controlled review/page/final cards. Verify Minh, Wien, unauthorized actor, stale state, expiry/refresh, modal reason, typed fallback, message tracking, and terminalization.

- [ ] **Step 5: Enable timer and verify schedule**

Enable/start `openclaw-web-discovery.timer`, verify next trigger is `07:30 Asia/Bangkok`, invoke the service once with bounded test config, confirm overlap behavior, outbox idempotency, and no duplicate review card.

- [ ] **Step 6: Run final live verification**

Verify OpenClaw config, gateway, Discord, package health, timer, last run, pending outbox, project states, file permissions, backups, and unchanged unrelated exec/model/security policy.

- [ ] **Step 7: Record deployment evidence**

Update the runbook/worklog with non-secret versions, backup timestamp, service/timer status, live smoke result, remaining optional provider configuration, and rollback location.

## Final verification checklist

Before declaring completion, run fresh:

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy src/openclaw_web
python -m build
git diff --check
git status --short
```

Then verify requirements line by line against the approved design spec:

- URL safety and robots;
- Hanoi 80 km geofence;
- multi-industry cohorts;
- discovery budgets and dedupe;
- screenshots and audit adapters;
- deterministic scores and provisional threshold metadata;
- AI review fallback;
- Gate 1 and dossiers;
- complete Website Brief package;
- upgrade priority;
- homepage validation and PM block on P0/P1;
- feedback/calibration without self-modifying weights;
- outbox idempotency;
- project-action actor/state/expiry checks;
- typed fallback parity;
- daily 07:30 timer and overlap lock;
- observability, retention, docs, deployment, rollback, and secret hygiene.
