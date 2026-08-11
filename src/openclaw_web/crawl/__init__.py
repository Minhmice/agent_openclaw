"""Safe crawl-boundary primitives.

Network adapters must validate every navigation and redirect with this package before
sending a request. DNS validation reduces SSRF risk, but is not socket pinning: clients
must eventually connect to a validated address or revalidate the connected peer.
"""

from openclaw_web.crawl.robots import RobotsPolicy
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
    "RobotsPolicy",
    "UnsafeTarget",
    "normalize_url",
    "resolve_and_validate",
    "sanitize_redirect_headers",
    "validate_redirect",
    "validate_resolved_ips",
]
