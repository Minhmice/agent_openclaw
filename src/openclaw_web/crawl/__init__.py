"""Safe crawl-boundary primitives.

Automatic redirects are forbidden. Network adapters must validate every navigation and
redirect before manually issuing it, use the returned canonical URL exactly, and either
connect to a validated address or revalidate the connected peer. DNS validation alone
does not provide socket pinning.
"""

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

__all__ = [
    "Resolver",
    "RobotsFetchOutcome",
    "RobotsPolicy",
    "UnsafeTarget",
    "normalize_url",
    "resolve_and_validate",
    "sanitize_redirect_headers",
    "validate_redirect",
    "validate_resolved_ips",
]
