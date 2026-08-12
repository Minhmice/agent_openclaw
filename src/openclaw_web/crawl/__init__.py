"""Safe crawl-boundary primitives.

Automatic redirects are forbidden. Network adapters must validate every navigation and
redirect before manually issuing it, use the returned canonical URL exactly, and either
connect to a validated address or revalidate the connected peer. DNS validation alone
does not provide socket pinning.
"""

from openclaw_web.crawl.extract import (
    CallToAction,
    EvidenceExcerpt,
    ExtractedPage,
    FormSummary,
    Heading,
    extract_page,
)
from openclaw_web.crawl.robots import RobotsFetchOutcome, RobotsPolicy
from openclaw_web.crawl.safety import (
    Resolver,
    UnsafeTarget,
    normalize_url,
    resolve_and_validate,
    sanitize_redirect_headers,
    validate_redirect,
    validate_resolved_ips,
)
from openclaw_web.crawl.service import CrawlFailure, CrawlLimits, CrawlResult, WebsiteCrawler

__all__ = [
    "CallToAction",
    "CrawlFailure",
    "CrawlLimits",
    "CrawlResult",
    "EvidenceExcerpt",
    "ExtractedPage",
    "FormSummary",
    "Heading",
    "Resolver",
    "RobotsFetchOutcome",
    "RobotsPolicy",
    "UnsafeTarget",
    "WebsiteCrawler",
    "extract_page",
    "normalize_url",
    "resolve_and_validate",
    "sanitize_redirect_headers",
    "validate_redirect",
    "validate_resolved_ips",
]
