# Web Audit, Discovery, Website Brief, and Discord Workflow Design

**Date:** 2026-08-12  
**Status:** Approved design; implementation not started  
**Repository:** `agent_openclaw`  
**Primary runtime:** Python modular monolith  
**Deployment target:** Remote OpenClaw host, user-scoped services  

## 1. Goal

Build an evidence-first website lead-mining and redesign-planning system that:

1. discovers businesses across multiple industries within 80 km of central Hanoi;
2. identifies strong businesses with weak websites and defensible online conversion opportunity;
3. crawls public pages safely and captures technical, content, visual, and responsive evidence;
4. produces reproducible deterministic scores plus schema-constrained AI interpretation;
5. ranks at most one defensible review candidate per daily run;
6. turns an approved lead into a complete Website Brief package and homepage proof concept;
7. routes project actions through Discord Components v2 buttons with typed-command fallback;
8. records feedback for later scoring calibration;
9. runs discovery every day at `07:30 Asia/Bangkok` without depending on an LLM to schedule or execute the crawl.

## 2. Non-goals

The following P2 work is outside this implementation:

- a deployed lead dashboard;
- a general-purpose CRM;
- automated outreach to a prospect;
- production website generation or deployment;
- CMS, authentication, billing, or account management;
- continuous autonomous scoring-weight changes;
- unrestricted crawling or bypassing `robots.txt`;
- storing login-gated, cookie-backed, private, or sensitive website content;
- changing firewall, credentials, unrelated OpenClaw security policy, or public network exposure.

The implementation may render a static `homepage-concept.html` for validation. That renderer is a proof surface, not a production website builder or dashboard.

## 3. Architecture decision

Use a Python modular monolith rather than an OpenClaw plugin or two-runtime service split.

```text
openclaw_web/
├── audit
├── brief
├── cli
├── config
├── crawl
├── db
├── delivery
├── discovery
├── evidence
├── feedback
├── geofence
├── models
├── observability
├── render
├── scoring
├── screenshots
└── validation
```

Reasons:

- the existing coordinator and tests are Python;
- SQLite and filesystem artifacts can be transactionally coordinated in one process;
- browser, HTTP, scoring, validation, and report behavior can be tested independently of OpenClaw;
- the daily discovery timer does not require a model turn;
- Discord delivery remains an adapter rather than the owner of business rules;
- a future native OpenClaw plugin can replace only the Discord adapter if needed.

## 4. Technology choices

Production package baseline:

```text
Python >= 3.11
SQLite via Python stdlib
Pydantic 2
PyYAML
HTTPX
Beautiful Soup 4
tldextract with network PSL updates disabled
Playwright Chromium
Jinja2
Typer
```

Developer/test baseline:

```text
pytest
pytest-asyncio
respx
coverage
ruff
mypy
```

External optional adapters:

```text
Lighthouse CLI
PageSpeed Insights API
CrUX API
Serper or another approved search provider
Google Places or another approved geocoding/listing provider
Wappalyzer API
Lychee
```

The built-in HTTP/HTML audit, manual/CSV seed input, scoring, artifact generation, and fixture tests must work without paid APIs. Fully automatic live discovery requires at least one configured discovery source. Bulk geocoding must not depend on the public Nominatim service; low-volume/manual geocoding can be implemented behind an explicit rate-limited adapter.

## 5. Market configuration

The initial market is multi-industry within 80 km of central Hanoi.

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

Geofence order:

1. use public latitude/longitude from the discovery source when available;
2. use an approved configured geocoder for normalized public addresses;
3. fall back to a versioned province/district allowlist when geocoding is unavailable;
4. mark unresolved location as low confidence rather than assuming it is in scope.

Initial industry cohorts:

```text
manufacturer
professional-services
local-service
showroom-retail
ecommerce
education
healthcare
hospitality
real-estate
other
```

Each cohort has a versioned scoring override. Daily seed budget is divided across cohorts so a high-volume category cannot consume the entire run. The initial scheduler assigns an equal minimum budget per cohort and distributes any remainder deterministically. Historical yield may be reported, but it does not automatically rewrite cohort weights.

## 6. End-to-end state flow

Candidate states:

```text
discovered
→ geofenced
→ prefiltered
→ audited
→ review-ready
```

Terminal or side states:

```text
geofence-rejected
duplicate
robots-blocked
unqualified
partial
failed-retryable
failed-terminal
```

Existing project states remain canonical:

```text
discovered
→ review
→ approved
→ website-brief
→ task
→ stakeholder-review
→ offer-ready
```

Reject path:

```text
review → rejected
```

Page states remain:

```text
planned
→ content-draft
→ content-ready
→ design-ready
→ qa-needed
→ stakeholder-review
→ approved
```

Only a `review-ready` candidate that passes Gate 1 may create or update a project in `review`.

## 7. Persistence model

SQLite is the operational source of truth. JSON and Markdown artifacts are versioned outputs.

Core tables:

```text
schema_migrations
runs
run_locks
candidates
candidate_sources
pages
evidence
audits
scores
issues
projects
feedback
deliveries
component_sets
worklog_events
```

### 7.1 Stable identity

Candidate identity is derived from:

```text
canonical registrable domain
+ normalized business name
+ normalized public address or phone when available
```

`project_id` is a stable slug and never contains a run date.

Deduplication checks:

- exact canonical domain;
- redirected canonical domain;
- business name and phone;
- business name and normalized address;
- active/review project state;
- rejected candidate recheck window.

### 7.2 Run idempotency

Daily run key:

```text
market_id + schedule_window + config_version
```

The same key resumes or returns the existing run. It never creates duplicate candidates or Discord cards.

### 7.3 Artifact write safety

Every artifact contains:

```text
schema_version
generator_version
created_at
source_run_id
content_hash
```

Writers use:

```text
serialize temporary file
→ validate schema
→ fsync temporary file
→ atomic replace
```

Failure preserves the last valid artifact.

## 8. Project artifact layout

```text
projects/<project_id>/
├── manifest.json
├── candidate.json
├── evidence.json
├── pages.json
├── audits/
├── screenshots/
├── scores.json
├── issues.json
├── dossier.md
├── curie-to-website.json
├── website-analysis.json
├── content-inventory.json
├── visual-inventory.json
├── design-audit.json
├── competitive-positioning.json
├── redesign-brief.json
├── visual-worlds.json
├── design-genome.json
├── design-tokens.json
├── component-system.json
├── page-blueprints.json
├── DESIGN.md
├── homepage-concept.html
├── validation-report.json
└── website-to-pm.json
```

## 9. Discovery design

Each discovery provider implements a common interface:

```python
class DiscoverySource(Protocol):
    def discover(self, market: MarketConfig) -> Iterable[CandidateSeed]: ...
```

Supported initial sources:

- manual URL;
- JSON and CSV seed files;
- search API adapter;
- public listing/places adapter;
- sitemap/domain seed importer.

Candidate seed schema:

```yaml
url:
business_name:
address:
latitude:
longitude:
industry_hint:
source_url:
source_type:
discovered_at:
```

Discovery providers do not audit, score, create projects, or send messages.

## 10. Crawl security and budgets

### 10.1 URL safety

Before every request and redirect:

- allow only HTTP and HTTPS;
- reject embedded credentials;
- normalize IDNA hostnames and default ports;
- remove fragments;
- resolve DNS;
- reject loopback, private, link-local, multicast, reserved, and cloud metadata addresses;
- revalidate the destination after every redirect;
- never forward cookies or authorization headers across redirects;
- reject local file, data, JavaScript, browser-extension, and non-web schemes.

Blocked ranges include IPv4 private/link-local/loopback ranges and IPv6 loopback, unique-local, and link-local ranges.

### 10.2 Robots and rate limits

Default domain budget:

```yaml
user_agent: AgentOpenClawAudit/1.0
max_pages: 25
max_depth: 3
max_redirects: 5
request_timeout_seconds: 15
page_timeout_seconds: 30
max_html_bytes: 5242880
max_asset_probe_bytes: 20971520
requests_per_second: 1
concurrency_per_domain: 2
```

The crawler obeys `robots.txt`, supported crawl delays, and same-site boundaries. It prioritizes homepage, flagship offer pages, conversion pages, proof pages, capability/about pages, pricing/FAQ, supporting content, then legal/careers/blog pages.

Robots-blocked homepage is terminal for the candidate. Inner-page blocks become explicit evidence gaps.

### 10.3 Extraction

For each page collect:

- status and redirects;
- canonical URL;
- title and metadata;
- H1–H6;
- normalized text excerpts;
- navigation and links;
- images and alt text;
- forms and field count;
- CTA candidates;
- contact and booking links;
- structured data and Open Graph;
- language and stale dates;
- CSS references and selected computed-style observations;
- DOM size and mobile viewport configuration;
- broken asset candidates.

Raw HTML is not retained by default. The system retains normalized evidence excerpts, hashes, and source URLs.

## 11. Screenshot design

Playwright Chromium uses a fresh isolated context per candidate.

Viewports:

```text
desktop 1440 × 1000
tablet  1024 × 900
mobile   390 × 844
```

Capture:

- initial viewport;
- bounded full-page image;
- major semantic sections when available;
- console errors;
- failed network requests;
- horizontal overflow;
- visible CTA and form state;
- cookie/banner obstruction.

The browser never logs in, submits a form, completes a purchase, books an appointment, downloads a file, or grants notification/geolocation permission.

One clean-context retry is allowed. Persistent screenshot failure produces a partial report with a responsive-audit confidence gap.

## 12. Technical audit adapters

Required built-in audit:

- HTTP and HTTPS;
- redirects;
- broken bounded internal links;
- mixed content;
- metadata and heading structure;
- image failures and alt coverage;
- CTA and form observations;
- mobile viewport;
- stale content;
- page/request/DOM size observations;
- console and network errors.

Required when browser dependencies are available:

- Lighthouse performance;
- accessibility;
- SEO;
- best practices;
- LCP, CLS, FCP, Speed Index, and TBT.

Optional enrichment:

- PageSpeed Insights;
- CrUX;
- Wappalyzer;
- Lychee.

Optional adapter failure does not fail the run. Missing inputs reduce confidence and remain visible in `unavailable_inputs`.

## 13. Evidence model

Every fact uses:

```yaml
evidence_id:
candidate_id:
page_url:
evidence_type:
observed_value:
claim_status: observed|inferred|estimated|unverified
confidence: high|medium|low
captured_at:
content_hash:
source_owner:
rights_status:
```

Every score records:

```yaml
score_name:
score_value:
rubric_version:
inputs:
evidence_ids:
deterministic: true|false
explanation_vi:
```

Deterministic values cannot be overwritten by AI output.

## 14. Scoring design

Top-level formula remains:

```text
LeadScore =
  BusinessScore × 30%
+ MoneyScore × 35%
+ WebUglyScore × 25%
+ TechnicalPainScore × 10%
```

Default qualification threshold is `75`. It is marked provisional until sufficient labeled feedback exists.

Money dimensions:

```text
search_demand          20
aov_ltv                20
trust_dependency       15
online_conversion_fit  15
business_strength      15
web_gap                15
```

Rules live in versioned YAML:

```text
config/scoring/base-v1.yaml
config/scoring/cohorts/*.yaml
```

Rule shape:

```yaml
id: TECH-LCP-POOR
dimension: technical_pain
input: lighthouse.lcp_ms
operator: gt
threshold: 4000
points: 12
severity: P1
explanation_vi: "LCP vượt 4 giây trên trang có ý định chuyển đổi."
```

Each score reports matched rules, unavailable inputs, confidence, and rubric version. Same evidence plus same rubric must produce the same deterministic score.

Final ranking:

```text
rank_score = LeadScore
           - confidence_penalty
           - stale_evidence_penalty
           - generic_cohort_penalty
```

Candidate qualification also requires business evidence, page-level weakness evidence, at least three evidence URLs, geofence pass, duplicate pass, and no terminal crawl failure.

## 15. AI review boundary

AI receives only a normalized evidence bundle, technical metrics, screenshot paths, section records, deterministic scores, and claim map.

It may produce:

- visual-quality assessment;
- trust assessment;
- conversion-friction hypotheses;
- business-strength narrative;
- redesign opportunity;
- KEEP/EVOLVE/RETIRE suggestions;
- confidence gaps.

It may not change measured values or deterministic points.

AI output is schema validated. One parse repair and one retry with validation errors are allowed. Persistent failure falls back to a deterministic dossier with `ai_review_status: failed`.

## 16. Curie output gate

A review candidate requires:

- one stable project ID;
- public crawlable website;
- business-strength evidence;
- page-level weakness evidence;
- explicit claim status for unmeasured money/conversion claims;
- at least three evidence URLs;
- explicit confidence gaps;
- duplicate check;
- Vietnamese dossier;
- valid machine-readable handoff.

No defensible candidate produces `no_candidate_defensible`, not a forced lead.

## 17. Website Brief pipeline

Website Brief starts only from a valid approved Curie handoff. It independently refreshes critical evidence before generation.

Stages:

```text
approved intake
→ evidence verification
→ business truth
→ content inventory
→ visual inventory
→ section analysis
→ design audit
→ competitive positioning
→ redesign brief
→ visual worlds
→ direction selection
→ design genome
→ tokens
→ component system
→ page blueprints
→ homepage concept
→ critique
→ final validation
→ PM handoff
```

Each stage is hash-addressed, schema validated, resumable, and invalidated by changed upstream evidence.

### 17.1 Business truth

`website-analysis.json` contains business, audience, offer, positioning, proof, conversion, information architecture, page inventory, and confidence gaps. Inferred fields include confidence and evidence IDs.

### 17.2 Content inventory

Each content entity includes type, raw and normalized copy, source, purpose, audience, importance, proof strength, claim status, evidence IDs, reuse status, and reason. Reuse states are `KEEP`, `REWRITE`, `MERGE`, `MOVE`, `REMOVE`, and `UNKNOWN`.

### 17.3 Visual inventory

Capture measured or evidenced color, typography, spacing, shape, depth, grid, imagery, iconography, motion, responsive observations, accessibility observations, and semantic section records.

### 17.4 Design audit

Score brand specificity, hierarchy, clarity, consistency, readability, trust, emotional fit, content fit, responsive quality, accessibility, and distinctiveness from 1 to 10. Classify patterns as `KEEP`, `EVOLVE`, or `RETIRE`. Every classification requires evidence and reason.

### 17.5 Competitive positioning

Observed competitor data remains separate from inferred category conventions. The artifact records expected conventions, sameness, safe differentiation, risky differentiation, content gaps, trust conventions, and CTA conventions.

### 17.6 Redesign mode

Choose `PRESERVE`, `REFRESH`, `EVOLVE`, or `OVERHAUL` with reason and confidence. The brief records preserve/evolve/retire lists, business goal, conversion goal, design thesis, and constraints.

### 17.7 Visual worlds

Generate five to seven structurally distinct directions. Score:

```text
brand_fit          20%
audience_fit       20%
business_fit       20%
distinctiveness    15%
scalability        10%
content_fit        10%
feasibility         5%
```

No direction may be selected when brand, audience, or business fit is below 6. Keep one primary and at most one alternate.

### 17.8 Genome, tokens, components, and pages

All downstream design choices reference `design-genome.json`. Components reference existing tokens and define purpose, anatomy, variants, states, responsive behavior, motion, usage rules, and anti-patterns. Interactive components require focus, disabled, and error states plus reduced-motion behavior when applicable.

Page blueprints require role, user, goal, conversion, CTA, message sequence, dependencies, target day, sections, responsive notes, and open questions. Content/proof/component references must resolve.

## 18. Upgrade prioritization

Issue fields:

```yaml
issue_id:
page_id:
funnel_step:
severity: P0|P1|P2|P3
business_impact: 0-10
funnel_proximity: 0-10
evidence_confidence: 0-10
reach_frequency: 0-10
dependency_unlock: 0-10
effort_efficiency: 0-10
```

Formula:

```text
UpgradePriority =
  business_impact      × 30%
+ funnel_proximity     × 20%
+ evidence_confidence  × 15%
+ reach_frequency      × 15%
+ dependency_unlock    × 10%
+ effort_efficiency    × 10%
```

Ordering:

```text
severity rank
→ UpgradePriority
→ funnel proximity
→ evidence confidence
→ stable issue ID
```

P0 always precedes P1; P1 precedes P2.

## 19. Homepage proof concept

The P1 renderer combines design tokens, component definitions, homepage blueprint, and content inventory into static semantic HTML and CSS variables.

It exists to validate:

- five-second business comprehension;
- primary CTA visibility;
- hierarchy and proof proximity;
- component consistency;
- mobile overflow;
- contrast and focus;
- touch targets;
- reduced motion;
- image/source completeness.

Playwright captures before/after desktop, tablet, and mobile screenshots. Unresolved P0/P1 prevents `website-to-pm.json` generation.

## 20. Discord Components v2 actions

Project cards use OpenClaw agent components when the installed schema/runtime supports them. Runtime preflight feature-detects `agentComponents.enabled` and `agentComponents.ttlMs`. If the installed OpenClaw release does not support configurable TTL, use its default TTL and refresh cards instead of writing an invalid config field.

Desired TTL, when supported, is 24 hours. Typed commands remain permanent fallback.

Review actions:

```text
Approve            Minh or Wien
Request changes    Minh
Reject             Minh
View evidence      Minh or Wien
Refresh            Minh or Wien
```

Page actions:

```text
Page status        Minh or Wien
Mark done          assigned actor
Block              assigned actor
Approve page       assigned reviewer after gates
Refresh            Minh or Wien
```

Final actions:

```text
Final confirm      each actor once
View unresolved    Minh or Wien
Refresh            Minh or Wien
```

Reject, request-change, and block use modal reason input when supported. No one-click final override exists.

### 20.1 Component validation

The database records component set, bot-owned message, channel, project, card type, allowed actions, expiry, and project state version.

Callback processing validates:

1. channel;
2. bot-owned message ID;
3. component set;
4. actor ID;
5. expiry;
6. current project/page state;
7. state version;
8. checklist and P0/P1 gates;
9. idempotency.

Buttons and typed commands invoke the same coordinator service. Stale cards ask the user to refresh and never replay an action.

### 20.2 Rollout

After backup and config validation:

1. enable feature-supported agent components;
2. send a controlled card in a trusted channel;
3. test Minh and Wien authorization;
4. test unauthorized actor denial;
5. test stale and expired callbacks;
6. test modal input;
7. test card terminalization;
8. enable new project cards.

Old messages are not retrofitted with buttons.

## 21. Daily discovery timer

Use a `systemd --user` service and timer rather than a model-driven scheduler.

Schedule:

```text
07:30 Asia/Bangkok every day
```

Daily budget:

```yaml
max_seed_candidates: 200
max_geofenced_candidates: 120
max_http_preflights: 100
max_business_prefilter: 40
max_full_audits: 10
max_ai_reviews: 5
max_review_candidates_posted: 1
max_run_minutes: 180
```

The service acquires a leased SQLite run lock. Overlapping invocation exits successfully with `skipped-overlap`. Runs are resumable from completed stages.

Possible outcomes:

```text
candidate-posted
no-candidate-defensible
partial
failed
```

The timer produces no automated outreach and never approves a project.

## 22. Delivery outbox

Audit transactions never call Discord directly. They enqueue:

```yaml
delivery_id:
event_type:
project_id:
channel_id:
payload_path:
idempotency_key:
status: pending|sending|sent|failed
attempt_count:
last_error:
message_id:
message_url:
```

The delivery worker claims a row, sends through OpenClaw, records bot message identity, and retries once after checking for prior delivery. A failed delivery never deletes the project or audit result.

## 23. Feedback and calibration

Immutable feedback events include actor, decision, reason, score snapshot, rubric version, cohort, and timestamp.

Reason codes:

```text
business-too-weak
website-not-weak
money-opportunity-weak
evidence-insufficient
wrong-location
duplicate
wrong-industry-fit
contact-unusable
false-positive-technical
other
```

Feedback reports aggregate score distributions, approval rate, reject reasons, cohort precision, evidence gaps, adapter failures, and threshold simulations.

The initial threshold of 75 is provisional. Calibration may generate `*.proposed.yaml`, but production rubric changes require explicit human approval and a new rubric version. The system never self-modifies production weights.

## 24. Observability and retention

Structured logs include run, candidate, project, stage, duration, result, attempt, and error code. They exclude secrets, cookies, authorization headers, raw session data, sensitive config, and unnecessary raw HTML.

Health checks cover:

- database and schema;
- browser and Lighthouse;
- artifact root;
- market and scoring config;
- discovery provider readiness;
- OpenClaw CLI and gateway;
- Discord probe;
- timer state;
- last successful run;
- pending/failed outbox.

Retention defaults:

```text
normalized evidence                  retain
scores, issues, feedback             retain
qualified-project screenshots        retain
rejected-candidate screenshots       30 days
raw HTML                             do not retain by default
run logs                             30 days
failed temporary files               7 days
```

Approved project artifacts and config backups are never automatically deleted.

## 25. Failure policy

| Failure | Behavior |
|---|---|
| Homepage unreachable | Terminal candidate failure |
| Robots blocks homepage | `robots-blocked`; no bypass |
| Inner page fails | Record page error and continue |
| Browser crashes | One clean retry, then partial |
| Lighthouse fails | Partial; preserve HTTP evidence |
| Optional API fails | Continue with unavailable input |
| AI timeout/invalid output | One retry, deterministic fallback |
| SQLite busy | Bounded retry, no duplicate mutation |
| Process interrupted | Resume completed stages |
| Discord delivery fails | One checked retry, preserve project |
| Cron overlap | `skipped-overlap` |
| Stale button callback | Reject and render refresh guidance |

## 26. Security and credential handling

- Secrets come from environment or approved secret provider.
- No secret is written to artifact, log, test fixture, command argument, worklog, or Git.
- Discovery API credentials are optional adapters and separately scoped.
- Browser contexts do not inherit operator cookies.
- Crawled text is untrusted data and cannot redefine system instructions, tool policy, or workflow state.
- AI prompts clearly delimit website content as evidence, not instructions.
- Remote config mutations require backup, validation, scoped approval, and rollback instructions.

## 27. Testing strategy

### 27.1 Unit tests

- URL normalization and SSRF denial;
- redirect revalidation;
- geofence and address fallback;
- deduplication;
- robots parsing;
- page prioritization;
- score rules and cohort overrides;
- upgrade priority;
- artifact schemas;
- state transitions and actor authorization;
- component staleness and idempotency;
- feedback aggregation;
- delivery outbox behavior;
- run locking.

### 27.2 Integration fixtures

Local fixture sites cover:

- healthy modern site;
- weak legacy site;
- broken links and images;
- robots block;
- redirect to private IP;
- mobile overflow;
- missing CTA;
- long form;
- mixed content;
- stale metadata;
- manufacturer;
- local service.

CI uses local fake services for external APIs.

### 27.3 Browser tests

- three viewport screenshots;
- cleanup and timeout;
- console/network capture;
- horizontal overflow;
- CTA visibility;
- no form submission;
- accessibility basics;
- homepage proof rendering.

### 27.4 End-to-end dry run

```text
fixture discovery
→ geofence
→ audit
→ scoring
→ dossier
→ fake delivery
→ button callback
→ approval
→ Website Brief
→ homepage concept
→ PM handoff
```

Live tests are opt-in and require explicit environment flags. They use bounded crawl limits and do not post to Discord unless separately enabled.

## 28. CLI contract

```text
openclaw-web health
openclaw-web audit <url>
openclaw-web discover --market hanoi-80km
openclaw-web run resume <run_id>
openclaw-web brief <project_id>
openclaw-web validate <project_id>
openclaw-web feedback <project_id> <decision> <reason>
openclaw-web calibration report
openclaw-web delivery dispatch
openclaw-web cron-run [--dry-run] [--force]
openclaw-web component-action <action-envelope>
```

`--force` bypasses only schedule-window reuse. It does not bypass geofence, deduplication, approval, evidence, or project-state gates.

## 29. Documentation reconciliation

Implementation updates the current contract drift:

- `curie-to-website` accepts Minh or Wien as the recorded approving actor;
- the coordinator command list includes `/page-approve`;
- PM documentation uses `/final-confirm`, not `/finalize`;
- Discord documentation distinguishes project-action buttons from exec-approval buttons;
- typed commands remain canonical fallback even after project cards gain components;
- remote/runtime-specific component TTL claims are feature-detected rather than assumed.

## 30. Deployment and rollback

Deployment is a separately approved remote mutation after local verification.

Deployment scope:

- install the package into a project-owned virtual environment;
- install Playwright Chromium and Lighthouse only after dependency approval;
- create a project-owned data/artifact root;
- create one user service and one user timer;
- upload/update workflow instructions and contracts;
- apply only the required Discord agent-components config fields supported by the installed schema;
- restart the gateway only when the OpenClaw CLI reports that a restart is required.

Before mutation:

- run documented read-only health checks;
- back up OpenClaw config and workflow files;
- record permissions and owner;
- show exact mutation, impact, rollback, and verification.

Rollback:

- stop/disable only the new timer and service;
- restore the workflow/config backups;
- validate OpenClaw config;
- restart the gateway only if required;
- preserve database and project artifacts for diagnosis;
- do not delete backups automatically.

## 31. Definition of done

The P0/P1 implementation plus requested buttons and discovery timer is complete only when:

- one URL can produce a schema-valid evidence-backed dossier;
- private/internal targets and robots violations are blocked;
- desktop/tablet/mobile screenshots exist or have explicit partial reason;
- deterministic scores are reproducible and traceable;
- cohort-specific scoring works for the configured Hanoi market;
- duplicate and run idempotency tests pass;
- one approved lead produces the required Website Brief artifacts;
- homepage concept renders and passes deterministic validation;
- unresolved P0/P1 blocks PM handoff;
- feedback and calibration reports work without auto-changing production weights;
- daily timer is idempotent and overlap-safe;
- delivery outbox prevents duplicate review posts;
- project-action buttons enforce actor, message, state, expiry, and checklist gates;
- typed commands invoke the same mutation service;
- contracts and docs no longer contradict authorization or command names;
- local unit, integration, browser, and end-to-end dry-run tests pass;
- controlled remote health, cron, Discord, and button smoke tests pass;
- secrets are absent from repository, artifacts, logs, and process arguments.

