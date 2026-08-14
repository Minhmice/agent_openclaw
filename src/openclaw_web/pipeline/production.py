"""Production discovery-to-review orchestration with injected persistence boundaries."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, TypeAlias, TypeVar, cast
from urllib.parse import urlsplit

from openclaw_web.audit.builtin import (
    BuiltinAuditResult,
    PageAuditObservation,
    audit_page,
)
from openclaw_web.audit.lighthouse import LighthouseMetrics, LighthouseRunResult
from openclaw_web.crawl.extract import ExtractedPage
from openclaw_web.crawl.safety import UnsafeTarget, normalize_url
from openclaw_web.crawl.service import CrawlResult
from openclaw_web.delivery.components import build_review_card
from openclaw_web.discovery.base import (
    AutomaticDiscoveryProvider,
    DiscoveryConfigurationError,
)
from openclaw_web.discovery.scheduler import APPROVED_COHORTS
from openclaw_web.discovery.service import CandidateRepository, DiscoveryService
from openclaw_web.geofence import GeofenceService
from openclaw_web.models import (
    Candidate,
    CandidateSeed,
    DeliveryRecord,
    DeliveryState,
    ProjectState,
)
from openclaw_web.scoring.engine import RuleEngine, RuleResult
from openclaw_web.scoring.rules import Rubric
from openclaw_web.screenshots import ScreenshotResult
from openclaw_web.settings import MarketConfig

Crawler: TypeAlias = Callable[[str], Awaitable[CrawlResult]]
Audit: TypeAlias = Callable[[PageAuditObservation], BuiltinAuditResult]
Screenshot: TypeAlias = Callable[[str], Awaitable[ScreenshotResult] | ScreenshotResult]
Lighthouse: TypeAlias = Callable[
    [str],
    Awaitable[LighthouseRunResult | Mapping[str, object]]
    | LighthouseRunResult
    | Mapping[str, object],
]
Clock: TypeAlias = Callable[[], datetime]
_T = TypeVar("_T")
_LIGHTHOUSE_METRIC_NAMES = (
    "performance_score",
    "accessibility_score",
    "seo_score",
    "best_practices_score",
    "lcp_ms",
    "cls",
    "fcp_ms",
    "speed_index_ms",
    "tbt_ms",
)


class ProjectSink(Protocol):
    """Idempotently persist one production project; return true only when inserted."""

    def __call__(self, project: ProductionProject) -> bool: ...


class DeliverySink(Protocol):
    """Idempotently enqueue one delivery; this boundary must never dispatch it."""

    def __call__(self, delivery: DeliveryRecord) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProductionProject:
    project_id: str
    candidate_id: str
    market_id: str
    artifact_dir: str
    created_at: datetime


def _workflow_project_payload(project: ProductionProject) -> dict[str, object]:
    return {
        "project_id": project.project_id,
        "candidate_id": project.candidate_id,
        "market_id": project.market_id,
        "artifact_dir": project.artifact_dir,
        "status": ProjectState.REVIEW.value,
        "state_version": 0,
        "pages": [],
        "final_confirmations": {},
        "created_at": project.created_at.isoformat(),
        "last_update": project.created_at.isoformat(),
    }


@dataclass(frozen=True, slots=True)
class ProductionRunResult:
    status: str
    project_id: str | None = None
    provider_failures: tuple[str, ...] = ()
    evaluated_candidates: int = 0


@dataclass(frozen=True, slots=True)
class _AuditedCandidate:
    candidate: Candidate
    cohort: str
    crawl: CrawlResult
    audits: tuple[BuiltinAuditResult, ...]
    scoring_inputs: Mapping[str, object]
    score: RuleResult
    project_id: str
    screenshot: ScreenshotResult | None
    screenshot_reason: str | None
    lighthouse: LighthouseRunResult | Mapping[str, object] | None
    lighthouse_reason: str | None


def _aware_utc(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DiscoveryConfigurationError("production clock must return an aware datetime")
    return value.astimezone(UTC)


def _nonblank(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _bounded_positive(value: int, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def _cohorts(market: MarketConfig) -> tuple[str, ...]:
    if "*" in market.industries:
        return APPROVED_COHORTS
    result: list[str] = []
    for industry in market.industries:
        cohort = industry if industry in APPROVED_COHORTS else "other"
        if cohort not in result:
            result.append(cohort)
    return tuple(result or ("other",))


def _round_robin(groups: Sequence[Sequence[CandidateSeed]], limit: int) -> tuple[CandidateSeed, ...]:
    selected: list[CandidateSeed] = []
    index = 0
    while len(selected) < limit:
        added = False
        for group in groups:
            if index < len(group):
                selected.append(group[index])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        index += 1
    return tuple(selected)


def _json_write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _lighthouse_metrics(
    lighthouse: LighthouseRunResult | Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if lighthouse is None:
        return None
    metrics: LighthouseMetrics | Mapping[str, object] | None
    metrics = lighthouse.metrics if isinstance(lighthouse, LighthouseRunResult) else lighthouse
    if metrics is None:
        return None
    if isinstance(metrics, LighthouseMetrics):
        return {
            name: value
            for name in _LIGHTHOUSE_METRIC_NAMES
            if (value := getattr(metrics, name)) is not None
        }
    return {
        name: value
        for name, value in metrics.items()
        if isinstance(name, str)
        and name in _LIGHTHOUSE_METRIC_NAMES
        and not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    }


def _lighthouse_payload(
    lighthouse: LighthouseRunResult | Mapping[str, object] | None,
    failure_reason: str | None,
) -> dict[str, object]:
    if failure_reason is not None:
        return {"metrics": None, "reason": failure_reason, "status": "unavailable"}
    if lighthouse is None:
        return {"metrics": None, "reason": "not-configured", "status": "unavailable"}
    metrics = _lighthouse_metrics(lighthouse)
    if isinstance(lighthouse, LighthouseRunResult):
        reason = lighthouse.reason
        if lighthouse.status == "unavailable":
            return {
                "metrics": None,
                "reason": reason or "collection-unavailable",
                "status": "unavailable",
            }
        normalized = dict(metrics or {})
        unavailable = sorted(set(_LIGHTHOUSE_METRIC_NAMES) - normalized.keys())
        status = "partial" if unavailable else "complete"
        if unavailable:
            reason = reason or f"missing-metrics:{','.join(unavailable)}"
        return {"metrics": normalized, "reason": reason, "status": status}
    normalized = dict(metrics or {})
    unavailable = sorted(set(_LIGHTHOUSE_METRIC_NAMES) - normalized.keys())
    status = "partial" if unavailable else "complete"
    reason = f"missing-metrics:{','.join(unavailable)}" if unavailable else None
    return {"metrics": normalized, "reason": reason, "status": status}


def _persist_screenshot_evidence(
    evidence_dir: Path,
    screenshot: ScreenshotResult | None,
    failure_reason: str | None,
) -> dict[str, object]:
    if failure_reason is not None:
        return {"reason": failure_reason, "screenshots": [], "status": "unavailable"}
    if screenshot is None:
        return {"reason": "not-configured", "screenshots": [], "status": "unavailable"}
    output_dir = evidence_dir / "screenshots"
    persisted: list[dict[str, object]] = []
    for index, record in enumerate(screenshot.screenshots):
        try:
            source = record.path.resolve(strict=True)
            source_size = source.stat().st_size
        except OSError:
            continue
        if not source.is_file() or source_size != record.byte_size:
            continue
        name = f"{index:02d}-{record.viewport}-{record.kind}.png"
        output_dir.mkdir(exist_ok=True)
        target = output_dir / name
        shutil.copyfile(source, target)
        relative = f"evidence/screenshots/{name}"
        persisted.append(
            {
                "byte_size": target.stat().st_size,
                "height": record.height,
                "kind": record.kind,
                "path": relative,
                "viewport": record.viewport,
                "width": record.width,
            }
        )
    complete = bool(persisted) and len(persisted) == len(screenshot.screenshots)
    status = screenshot.status if complete else "partial"
    reason = "capture-incomplete" if status == "partial" else None
    return {"reason": reason, "screenshots": persisted, "status": status}


def _page_payload(page: ExtractedPage) -> dict[str, object]:
    return {
        "url": page.url,
        "title": page.title,
        "description": page.description,
        "headings": [asdict(item) for item in page.headings],
        "ctas": [asdict(item) for item in page.ctas],
        "forms": [asdict(item) for item in page.forms],
        "links": list(page.links),
        "evidence": [asdict(item) for item in page.evidence],
    }


def _contact_observed(pages: Sequence[ExtractedPage]) -> bool:
    paths = (
        urlsplit(url).path.casefold()
        for page in pages
        for url in (page.url, *page.links)
    )
    return any("contact" in path or "lien-he" in path for path in paths)


def _scoring_inputs(
    rubric: Rubric,
    crawl: CrawlResult,
    audits: Sequence[BuiltinAuditResult],
    lighthouse: Mapping[str, object] | None,
) -> dict[str, object]:
    pages = crawl.pages
    inputs: dict[str, dict[str, object]] = {}

    def supply(path: str, value: object) -> None:
        if path in rubric.allowed_inputs:
            section, key = path.split(".", 1)
            inputs.setdefault(section, {})[key] = value

    supply("audit.has_conversion_cta", any(page.ctas for page in pages))
    if not crawl.failures and not crawl.budget_exhausted:
        supply("crawl.has_contact_page", _contact_observed(pages))
    if all("resources" not in result.unavailable_inputs for result in audits):
        broken = sum(
            len(finding.evidence)
            for result in audits
            for finding in result.findings
            if finding.rule_id == "LINK-BROKEN"
        )
        supply("audit.broken_links", broken)
    if lighthouse is not None:
        for key, value in lighthouse.items():
            supply(f"lighthouse.{key}", value)
    return cast(dict[str, object], inputs)


def _project_identity(candidate: Candidate, market: MarketConfig, rubric: Rubric) -> str:
    material = f"{candidate.candidate_id}:{market.market_id}:{rubric.rubric_version}"
    return f"project-{uuid.uuid5(uuid.NAMESPACE_URL, material).hex}"


def _observed_evidence_ids(
    crawl: CrawlResult,
    audits: Sequence[BuiltinAuditResult],
    lighthouse: Mapping[str, object] | None,
) -> set[str]:
    evidence_ids = {
        finding.rule_id for result in audits for finding in result.findings
    }
    if lighthouse is not None and "lcp_ms" in lighthouse:
        evidence_ids.add("LIGHTHOUSE-LCP-SLOW")
    if lighthouse is not None and "performance_score" in lighthouse:
        evidence_ids.add("LIGHTHOUSE-PERFORMANCE-POOR")
    if not crawl.failures and not crawl.budget_exhausted and not _contact_observed(crawl.pages):
        evidence_ids.add("CRAWL-CONTACT-MISSING")
    return evidence_ids


def _evidence_urls(
    candidate: Candidate,
    crawl: CrawlResult,
    audits: Sequence[BuiltinAuditResult],
) -> tuple[str, ...]:
    values = [*(str(url) for url in candidate.source_urls), *(page.url for page in crawl.pages)]
    values.extend(
        evidence
        for result in audits
        for finding in result.findings
        for evidence in finding.evidence
        if urlsplit(evidence).scheme in {"http", "https"}
    )
    public_urls: list[str] = []
    for value in values:
        try:
            public_urls.append(normalize_url(value))
        except (TypeError, ValueError, UnsafeTarget):
            continue
    return tuple(dict.fromkeys(public_urls))


def _defensible(
    candidate: Candidate,
    crawl: CrawlResult,
    audits: Sequence[BuiltinAuditResult],
    lighthouse: Mapping[str, object] | None,
    score: RuleResult,
) -> bool:
    observed_ids = _observed_evidence_ids(crawl, audits, lighthouse)
    return bool(
        score.qualified
        and score.evidence_ids
        and set(score.evidence_ids).issubset(observed_ids)
        and len(_evidence_urls(candidate, crawl, audits)) >= 3
    )


def _dossier(candidate: Candidate, audited: _AuditedCandidate) -> str:
    unavailable = sorted(
        {
            *audited.score.unavailable_inputs,
            *(name for result in audited.audits for name in result.unavailable_inputs),
        }
    )
    findings = [
        finding
        for result in audited.audits
        for finding in result.findings
    ]
    unavailable_text = ", ".join(f"`{item}`" for item in unavailable) or "không có"
    finding_text = "\n".join(
        f"- `{item.rule_id}` ({item.severity}): "
        + ("; ".join(item.evidence) if item.evidence else "đã quan sát")
        for item in findings
    ) or "- Không có finding deterministic."
    source_urls = "\n".join(f"- {url}" for url in candidate.source_urls)
    return (
        f"# Hồ sơ review: {candidate.name}\n\n"
        "## Sự thật đã quan sát\n\n"
        f"- Website: {candidate.website_url}\n"
        f"- Cohort: `{audited.cohort}`\n"
        f"- Điểm rubric: {audited.score.score:g}/{audited.score.qualification_threshold:g}\n\n"
        "## Finding deterministic\n\n"
        f"{finding_text}\n\n"
        "## Suy luận và ước tính\n\n"
        "- Không bổ sung suy luận hoặc ước tính ngoài evidence đã thu thập.\n\n"
        "## Khoảng trống confidence\n\n"
        f"- Dữ liệu chưa xác minh: {unavailable_text}.\n\n"
        "## Evidence URLs\n\n"
        f"{source_urls}\n"
    )


def _compact_review_message(audited: _AuditedCandidate) -> str:
    findings = [finding for result in audited.audits for finding in result.findings]
    priority = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    ordered_findings = sorted(findings, key=lambda item: (priority.get(item.severity, 9), item.rule_id))
    top_findings = []
    seen_rule_ids: set[str] = set()
    for finding in ordered_findings:
        if finding.rule_id in seen_rule_ids:
            continue
        seen_rule_ids.add(finding.rule_id)
        top_findings.append(finding)
        if len(top_findings) == 3:
            break
    opportunities = [
        f"{finding.severity}  {finding.rule_id}: cần review evidence trước khi thay đổi"
        for finding in top_findings
    ] or ["P2  Chưa có finding deterministic nổi bật."]
    unavailable = sorted(
        {
            *audited.score.unavailable_inputs,
            *(name for result in audited.audits for name in result.unavailable_inputs),
        }
    )
    confidence = ", ".join(unavailable) if unavailable else "không có khoảng trống được ghi nhận"
    evidence_count = len(_evidence_urls(audited.candidate, audited.crawl, audited.audits))
    return (
        f"**{audited.candidate.name} · Website review**\n"
        f"`{audited.project_id}` · cohort `{audited.cohort}`\n\n"
        "**WHY NOW**\n"
        f"Audit đạt review gate với {evidence_count} public evidence URL.\n\n"
        "**TOP OPPORTUNITIES**\n"
        + "\n".join(opportunities)
        + "\n\n**EVIDENCE**\n"
        + f"{evidence_count} public URLs · audit deterministic\n\n"
        + "**CONFIDENCE**\n"
        + f"Dữ liệu chưa xác minh: {confidence}."
    )


class ProductionPipeline:
    """Run one bounded production slice and enqueue at most one review delivery."""

    def __init__(
        self,
        *,
        repository: CandidateRepository,
        artifact_root: Path,
        market: MarketConfig,
        providers: Sequence[AutomaticDiscoveryProvider],
        crawler: Crawler,
        rubric: Rubric,
        review_channel: str,
        clock: Clock,
        project_sink: ProjectSink,
        delivery_sink: DeliverySink,
        audit: Audit | None = None,
        screenshot: Screenshot | None = None,
        lighthouse: Lighthouse | None = None,
        max_discovered: int = 50,
        per_provider_limit: int = 10,
        max_full_audits: int = 10,
    ) -> None:
        if not isinstance(artifact_root, Path) or not artifact_root.is_absolute():
            raise ValueError("artifact_root must be an absolute pathlib.Path")
        if not isinstance(market, MarketConfig):
            raise TypeError("market must be a validated MarketConfig")
        if not isinstance(rubric, Rubric):
            raise TypeError("rubric must be a validated Rubric")
        if not providers:
            raise DiscoveryConfigurationError("at least one provider is required")
        self._repository = repository
        self._artifact_root = artifact_root
        self._market = market
        self._providers = tuple(providers)
        self._crawler = crawler
        self._rubric = rubric
        self._review_channel = _nonblank(review_channel, "review_channel")
        self._clock = clock
        self._project_sink = project_sink
        self._delivery_sink = delivery_sink
        self._audit = audit or audit_page
        self._screenshot = screenshot
        self._lighthouse = lighthouse
        self._max_discovered = _bounded_positive(max_discovered, "max_discovered", 10_000)
        self._per_provider_limit = _bounded_positive(
            per_provider_limit, "per_provider_limit", 1_000
        )
        self._max_full_audits = _bounded_positive(
            max_full_audits, "max_full_audits", 100
        )

    async def _discover(self) -> tuple[tuple[CandidateSeed, ...], tuple[str, ...], int]:
        calls: list[tuple[AutomaticDiscoveryProvider, str]] = [
            (provider, cohort)
            for cohort in _cohorts(self._market)
            for provider in self._providers
        ]

        async def invoke(
            provider: AutomaticDiscoveryProvider, cohort: str
        ) -> tuple[CandidateSeed, ...]:
            if provider.readiness() != "ready":
                raise DiscoveryConfigurationError("provider is not ready")
            result = await provider.discover(
                self._market, cohort, self._per_provider_limit
            )
            if isinstance(result, str | bytes | bytearray):
                raise DiscoveryConfigurationError("provider returned invalid candidates")
            values = tuple(result)
            if any(not isinstance(item, CandidateSeed) for item in values):
                raise DiscoveryConfigurationError("provider returned invalid candidates")
            return values[: self._per_provider_limit]

        results = await asyncio.gather(
            *(invoke(provider, cohort) for provider, cohort in calls),
            return_exceptions=True,
        )
        groups: list[tuple[CandidateSeed, ...]] = []
        failures: list[str] = []
        successes = 0
        for (provider, cohort), result in zip(calls, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                failures.append(f"{provider.name}:{cohort}")
            else:
                successes += 1
                groups.append(result)
        return _round_robin(groups, self._max_discovered), tuple(failures), successes

    async def _optional_value(
        self,
        operation: Callable[[str], Awaitable[_T] | _T] | None,
        url: str,
        *,
        failure_reason: str,
    ) -> tuple[_T | None, str | None]:
        if operation is None:
            return None, None
        try:
            value = operation(url)
            if inspect.isawaitable(value):
                return await value, None
            return value, None
        except Exception:  # noqa: BLE001 - optional evidence remains explicitly unavailable
            return None, failure_reason

    async def _audit_candidate(self, candidate: Candidate, cohort: str) -> _AuditedCandidate | None:
        if candidate.website_url is None:
            return None
        website_url = str(candidate.website_url)
        crawl = await self._crawler(website_url)
        if not crawl.pages:
            return None
        screenshot, screenshot_reason = await self._optional_value(
            self._screenshot,
            website_url,
            failure_reason="capture-failed",
        )
        lighthouse_value, lighthouse_reason = await self._optional_value(
            self._lighthouse,
            website_url,
            failure_reason="collection-failed",
        )
        lighthouse = cast(
            LighthouseRunResult | Mapping[str, object] | None,
            lighthouse_value,
        )
        audits = tuple(
            self._audit(
                PageAuditObservation(
                    page=page,
                    requested_url=website_url if index == 0 else page.url,
                    browser=screenshot if index == 0 else None,
                )
            )
            for index, page in enumerate(crawl.pages)
        )
        lighthouse_metrics = _lighthouse_metrics(lighthouse)
        scoring_inputs = _scoring_inputs(self._rubric, crawl, audits, lighthouse_metrics)
        score = RuleEngine(self._rubric).evaluate(scoring_inputs, cohort_id=cohort)
        if not _defensible(candidate, crawl, audits, lighthouse_metrics, score):
            return None
        return _AuditedCandidate(
            candidate,
            cohort,
            crawl,
            audits,
            scoring_inputs,
            score,
            _project_identity(candidate, self._market, self._rubric),
            screenshot,
            screenshot_reason,
            lighthouse,
            lighthouse_reason,
        )

    def _write_artifacts(self, audited: _AuditedCandidate, created_at: datetime) -> Path:
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        destination = self._artifact_root / audited.project_id
        if destination.is_dir():
            return destination
        staging = Path(
            tempfile.mkdtemp(prefix=f".{audited.project_id}-", dir=self._artifact_root)
        )
        try:
            evidence = staging / "evidence"
            evidence.mkdir()
            _json_write(
                evidence / "screenshots.json",
                _persist_screenshot_evidence(
                    evidence,
                    audited.screenshot,
                    audited.screenshot_reason,
                ),
            )
            _json_write(
                evidence / "lighthouse.json",
                _lighthouse_payload(audited.lighthouse, audited.lighthouse_reason),
            )
            _json_write(
                staging / "candidate.json",
                audited.candidate.model_dump(mode="json"),
            )
            _json_write(
                evidence / "pages.json",
                {
                    "start_url": audited.crawl.start_url,
                    "pages": [_page_payload(page) for page in audited.crawl.pages],
                    "failures": [asdict(item) for item in audited.crawl.failures],
                    "budget_exhausted": audited.crawl.budget_exhausted,
                },
            )
            _json_write(
                staging / "audit.json",
                {
                    "status": (
                        "partial"
                        if any(item.status == "partial" for item in audited.audits)
                        else "complete"
                    ),
                    "findings": [
                        asdict(finding)
                        for result in audited.audits
                        for finding in result.findings
                    ],
                    "unavailable_inputs": sorted(
                        {
                            name
                            for result in audited.audits
                            for name in result.unavailable_inputs
                        }
                    ),
                },
            )
            _json_write(
                staging / "score.json",
                {
                    **audited.score.model_dump(mode="json"),
                    "inputs": audited.scoring_inputs,
                },
            )
            _json_write(
                staging / "issues.json",
                [
                    {
                        "issue_id": f"{audited.project_id}:{finding.rule_id}",
                        "rule_id": finding.rule_id,
                        "severity": finding.severity,
                        "evidence": list(finding.evidence),
                        "recommendation_vi": "Cần review evidence trước khi quyết định thay đổi.",
                    }
                    for result in audited.audits
                    for finding in result.findings
                ],
            )
            (staging / "dossier.vi.md").write_text(
                _dossier(audited.candidate, audited), encoding="utf-8", newline="\n"
            )
            review_card = build_review_card(audited.project_id)
            component_material = f"component:{audited.project_id}:review:v0"
            _json_write(
                staging / "review-card.json",
                {
                    "component_set": {
                        "component_set_id": (
                            f"component-{uuid.uuid5(uuid.NAMESPACE_URL, component_material).hex}"
                        ),
                        "project_id": audited.project_id,
                        "card_type": review_card.card_type,
                        "allowed_actions": [button.action for button in review_card.buttons],
                        "expires_at": (created_at + timedelta(hours=24)).isoformat(),
                        "state_version": 0,
                        "project_state": ProjectState.REVIEW.value,
                    },
                    "message": _compact_review_message(audited),
                    "components": review_card.payload,
                },
            )
            _json_write(
                staging / "workflow-project.json",
                _workflow_project_payload(
                    ProductionProject(
                        project_id=audited.project_id,
                        candidate_id=audited.candidate.candidate_id,
                        market_id=self._market.market_id,
                        artifact_dir=str(destination),
                        created_at=created_at,
                    )
                ),
            )
            try:
                os.replace(staging, destination)
            except OSError:
                if destination.is_dir():
                    shutil.rmtree(staging)
                else:
                    raise
            return destination
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def _persist_review(self, audited: _AuditedCandidate, created_at: datetime) -> bool:
        artifact_dir = self._write_artifacts(audited, created_at)
        project = ProductionProject(
            project_id=audited.project_id,
            candidate_id=audited.candidate.candidate_id,
            market_id=self._market.market_id,
            artifact_dir=str(artifact_dir),
            created_at=created_at,
        )
        self._project_sink(project)
        key = f"review:{audited.project_id}"
        delivery = DeliveryRecord(
            delivery_id=f"delivery-{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}",
            event_type="review-card",
            project_id=audited.project_id,
            channel_id=self._review_channel,
            payload_path=str(artifact_dir / "review-card.json"),
            idempotency_key=key,
            status=DeliveryState.PENDING,
        )
        return self._delivery_sink(delivery)

    async def run(self) -> ProductionRunResult:
        now = _aware_utc(self._clock)
        try:
            seeds, failures, successes = await self._discover()
        except Exception:  # noqa: BLE001 - scheduled boundary returns a stable safe status
            return ProductionRunResult("failed")
        if successes == 0:
            return ProductionRunResult("failed", provider_failures=failures)
        geofence = GeofenceService(
            self._market.center.latitude,
            self._market.center.longitude,
            self._market.radius_km,
        )
        try:
            discovered = DiscoveryService(
                geofence=geofence,
                repository=self._repository,
                max_seed_candidates=self._max_discovered,
            ).process(seeds)
        except Exception:  # noqa: BLE001 - persistence/config boundary returns stable status
            return ProductionRunResult("failed", provider_failures=failures)

        candidates: list[tuple[Candidate, str]] = []
        seen: set[str] = set()
        for outcome in discovered.outcomes:
            candidate = outcome.candidate
            if (
                isinstance(candidate, Candidate)
                and outcome.cohort is not None
                and outcome.status in {"accepted", "duplicate"}
                and candidate.candidate_id not in seen
            ):
                candidates.append((candidate, outcome.cohort))
                seen.add(candidate.candidate_id)

        evaluated = 0
        for candidate, cohort in candidates:
            if evaluated >= self._max_full_audits:
                break
            try:
                audited = await self._audit_candidate(candidate, cohort)
            except Exception:  # noqa: BLE001 - candidate failures do not expose provider details
                evaluated += 1
                continue
            evaluated += 1
            if audited is not None:
                try:
                    persisted = self._persist_review(audited, now)
                except Exception:  # noqa: BLE001 - sink/artifact failure is a failed run
                    return ProductionRunResult(
                        "failed", provider_failures=failures, evaluated_candidates=evaluated
                    )
                if persisted:
                    return ProductionRunResult(
                        "candidate-posted", audited.project_id, failures, evaluated
                    )
        status = "partial" if failures else "no-candidate-defensible"
        return ProductionRunResult(status, provider_failures=failures, evaluated_candidates=evaluated)


__all__ = [
    "DeliverySink",
    "ProductionPipeline",
    "ProductionProject",
    "ProductionRunResult",
    "ProjectSink",
]
