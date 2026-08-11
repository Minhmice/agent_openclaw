"""URL and redirect validation at the crawler's SSRF boundary.

All HTTPX, Playwright, and asset-fetch adapters must call :func:`validate_redirect`
for every navigation or redirect hop and must sanitize forwarded headers. DNS checks
are mandatory but cannot eliminate time-of-check/time-of-use races by themselves;
future transports must connect to a validated address or revalidate the peer address.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from typing import Protocol, TypeAlias
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

IPAddress: TypeAlias = ipaddress.IPv4Address | ipaddress.IPv6Address
AddressInput: TypeAlias = str | IPAddress

_HEX_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_HOST = re.compile(r"[0-9a-fx.]+\Z", re.IGNORECASE)
_SPECIAL_HOST_SUFFIXES = (
    "localhost",
    "local",
    "localdomain",
    "internal",
    "home",
    "lan",
    "test",
    "invalid",
    "example",
    "onion",
)
_SENSITIVE_REDIRECT_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "host",
        "origin",
        "referer",
        "x-api-key",
        "proxy-connection",
    }
)


class UnsafeTarget(ValueError):
    """A URL, address, redirect, or resolver result violates crawl policy."""


class Resolver(Protocol):
    """Synchronous DNS resolver injected by a network adapter or test."""

    def __call__(self, host: str, /) -> Iterable[AddressInput]:
        """Return every address currently associated with *host*."""


def _reject_ambiguous_text(value: str) -> None:
    if not value or value != value.strip():
        raise UnsafeTarget("URL is empty or contains surrounding whitespace")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise UnsafeTarget("URL contains control or whitespace characters")
    if "\\" in value:
        raise UnsafeTarget("URL contains an ambiguous path separator")


def _normalize_escapes(value: str) -> str:
    if _BAD_ESCAPE.search(value):
        raise UnsafeTarget("URL contains an invalid percent escape")

    def normalize(match: re.Match[str]) -> str:
        byte = int(match.group(1), 16)
        if byte < 32 or byte == 127:
            raise UnsafeTarget("URL contains a percent-encoded control character")
        if byte == 0x5C:
            raise UnsafeTarget("URL contains an encoded ambiguous path separator")
        return f"%{match.group(1).upper()}"

    return _HEX_ESCAPE.sub(normalize, value)


def _remove_dot_segments(path: str) -> str:
    keep_trailing_slash = path.endswith(("/", "/.", "/.."))
    segments: list[str] = []
    for segment in path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if len(segments) > 1:
                segments.pop()
            continue
        segments.append(segment)
    normalized = "/".join(segments)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    normalized = normalized or "/"
    if keep_trailing_slash and normalized != "/" and not normalized.endswith("/"):
        normalized = f"{normalized}/"
    return normalized


def _is_public_address(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_public_address(address.ipv4_mapped)
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _normalize_host(host: str) -> tuple[str, IPAddress | None]:
    if not host or "%" in host:
        raise UnsafeTarget("URL host is missing or contains an encoded authority component")
    host = host.rstrip(".").lower()
    if not host:
        raise UnsafeTarget("URL host is missing")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if not _is_public_address(literal):
            raise UnsafeTarget("URL contains a non-public literal address")
        return literal.compressed, literal

    if host[0].isdigit() and _NUMERIC_HOST.fullmatch(host):
        raise UnsafeTarget("URL contains an ambiguous numeric hostname")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise UnsafeTarget("URL contains an invalid internationalized hostname") from error
    if len(ascii_host) > 253:
        raise UnsafeTarget("URL hostname is too long")
    labels = ascii_host.split(".")
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise UnsafeTarget("URL contains an invalid hostname")
    if len(labels) < 2:
        raise UnsafeTarget("URL contains a single-label internal hostname")
    if any(
        ascii_host == suffix or ascii_host.endswith(f".{suffix}")
        for suffix in _SPECIAL_HOST_SUFFIXES
    ):
        raise UnsafeTarget("URL contains a local or special-use hostname")
    return ascii_host, None


def _split_url(url: str) -> SplitResult:
    _reject_ambiguous_text(url)
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise UnsafeTarget("URL authority is malformed") from error
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeTarget("URL scheme must be HTTP or HTTPS")
    if not parsed.netloc or parsed.hostname is None:
        raise UnsafeTarget("URL must contain an absolute host")
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise UnsafeTarget("URL userinfo is not allowed")
    if "%" in parsed.netloc:
        raise UnsafeTarget("URL authority may not contain percent escapes")
    if parsed.netloc.endswith(":"):
        raise UnsafeTarget("URL port is malformed")
    return parsed


def normalize_url(url: str) -> str:
    """Return the canonical crawl identity for a safe absolute HTTP(S) URL.

    Fragments are intentionally omitted. Query parameter order is retained because
    reordering can change application semantics.
    """

    parsed = _split_url(url)
    host, literal = _normalize_host(parsed.hostname or "")
    try:
        port = parsed.port
    except ValueError as error:
        raise UnsafeTarget("URL port is malformed") from error
    if port == 0:
        raise UnsafeTarget("URL port zero is not allowed")

    scheme = parsed.scheme.lower()
    default_port = 80 if scheme == "http" else 443
    rendered_host = f"[{host}]" if isinstance(literal, ipaddress.IPv6Address) else host
    authority = rendered_host if port in {None, default_port} else f"{rendered_host}:{port}"

    path = parsed.path or "/"
    if not path.startswith("/"):
        raise UnsafeTarget("URL path is ambiguous")
    path = _remove_dot_segments(_normalize_escapes(path))
    query = _normalize_escapes(parsed.query)
    return urlunsplit((scheme, authority, path, query, ""))


def validate_resolved_ips(addresses: Iterable[AddressInput]) -> tuple[IPAddress, ...]:
    """Validate all DNS answers and return an immutable canonical address tuple.

    An empty set or a set containing even one non-public address is rejected. Rejecting
    mixed answers prevents a resolver from smuggling an internal rebinding target beside
    an otherwise acceptable public answer.
    """

    validated: list[IPAddress] = []
    for candidate in addresses:
        try:
            address = ipaddress.ip_address(candidate)
        except (TypeError, ValueError) as error:
            raise UnsafeTarget("DNS resolution returned an invalid address") from error
        if not _is_public_address(address):
            raise UnsafeTarget("DNS resolution returned a non-public address")
        validated.append(address)
    if not validated:
        raise UnsafeTarget("DNS resolution returned no addresses")
    return tuple(validated)


def resolve_and_validate(host: str, resolver: Resolver) -> tuple[IPAddress, ...]:
    """Resolve *host* with an injected resolver and validate every answer."""

    normalized_host, _literal = _normalize_host(host)
    try:
        addresses = resolver(normalized_host)
        return validate_resolved_ips(addresses)
    except UnsafeTarget:
        raise
    except Exception:  # noqa: BLE001 - resolver implementations have no shared exception base.
        raise UnsafeTarget("DNS resolution failed") from None


def validate_redirect(source_url: str, location: str, resolver: Resolver) -> str:
    """Resolve, normalize, and DNS-check one redirect hop.

    HTTPS-to-HTTP redirects are rejected to avoid transport downgrades. This function
    deliberately performs a new resolver call for the destination on every invocation;
    callers must never reuse the source hop's DNS result.
    """

    source = normalize_url(source_url)
    if not isinstance(location, str) or not location:
        raise UnsafeTarget("redirect Location is missing")
    _reject_ambiguous_text(location)
    destination = normalize_url(urljoin(source, location))
    source_scheme = urlsplit(source).scheme
    destination_parts = urlsplit(destination)
    if source_scheme == "https" and destination_parts.scheme == "http":
        raise UnsafeTarget("HTTPS redirect downgrade is not allowed")
    resolve_and_validate(destination_parts.hostname or "", resolver)
    return destination


def sanitize_redirect_headers(
    headers: Mapping[str, str], source_url: str, target_url: str
) -> dict[str, str]:
    """Copy safe request headers for a redirect without mutating the caller mapping.

    Credentials, cookies, API keys, and host-derived headers are stripped on every hop,
    including same-origin redirects. Network adapters should regenerate any required
    host-derived values from the validated destination.
    """

    normalize_url(source_url)
    normalize_url(target_url)
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _SENSITIVE_REDIRECT_HEADERS
    }
