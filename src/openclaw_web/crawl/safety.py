"""URL and redirect validation at the crawler's SSRF boundary.

Automatic redirects are forbidden. HTTPX, Playwright, and asset-fetch adapters must
issue every hop manually, call :func:`validate_redirect`, and use its returned canonical
URL exactly. Each hop must also be DNS-revalidated and connected to a validated address
or have its connected peer address revalidated; DNS checks alone cannot eliminate the
time-of-check/time-of-use race. Forwarded headers must be sanitized on every hop.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from typing import Protocol, TypeAlias
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import idna

IPAddress: TypeAlias = ipaddress.IPv4Address | ipaddress.IPv6Address
AddressInput: TypeAlias = str | IPAddress

_HEX_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_DOT = re.compile(r"%2E", re.IGNORECASE)
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_HOST = re.compile(r"[0-9a-fx.]+\Z", re.IGNORECASE)
_AMBIGUOUS_REDIRECT_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:/{3,}")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z", re.ASCII)
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
_SAFE_REDIRECT_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "pragma",
        "range",
        "user-agent",
    }
)
_ALLOW_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "192.0.0.9/32",
        "192.0.0.10/32",
        "2001:1::1/128",
        "2001:1::2/128",
        "2001:1::3/128",
        "2001:3::/32",
        "2001:4:112::/48",
        "2001:20::/28",
        "2001:30::/28",
    )
)
_DENY_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "64:ff9b:1::/48",
        "100::/64",
        "100:0:0:1::/64",
        "2001::/23",
        "2001:db8::/32",
        "3ffe::/16",
        "3fff::/20",
        "5f00::/16",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)
_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")


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


def _decode_dot_segment_escapes(path: str) -> str:
    segments: list[str] = []
    for segment in path.split("/"):
        decoded = _ENCODED_DOT.sub(".", segment)
        segments.append(decoded if decoded in {".", ".."} else segment)
    return "/".join(segments)


def _is_public_address(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_public_address(address.ipv4_mapped)
    if isinstance(address, ipaddress.IPv6Address) and address in _NAT64_WELL_KNOWN_PREFIX:
        embedded_ipv4 = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        return _is_public_address(embedded_ipv4)
    if any(
        address.version == network.version and address in network for network in _ALLOW_NETWORKS
    ):
        return True
    if any(address.version == network.version and address in network for network in _DENY_NETWORKS):
        return False
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
        or address.is_unspecified
    )


def _normalize_literal_host(host: str) -> tuple[str, IPAddress] | None:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not _is_public_address(literal):
        raise UnsafeTarget("URL contains a non-public literal address")
    return literal.compressed, literal


def _normalize_host(host: str) -> tuple[str, IPAddress | None]:
    if not host or "%" in host:
        raise UnsafeTarget("URL host is missing or contains an encoded authority component")
    host = host.rstrip(".").lower()
    if not host:
        raise UnsafeTarget("URL host is missing")

    literal = _normalize_literal_host(host)
    if literal is not None:
        return literal

    if host[0].isdigit() and _NUMERIC_HOST.fullmatch(host):
        raise UnsafeTarget("URL contains an ambiguous numeric hostname")
    try:
        ascii_host = idna.encode(host, uts46=True, std3_rules=True, transitional=False).decode(
            "ascii"
        )
    except idna.IDNAError as error:
        raise UnsafeTarget("URL contains an invalid internationalized hostname") from error
    ascii_host = ascii_host.lower().rstrip(".")
    if not ascii_host:
        raise UnsafeTarget("URL host is missing")
    literal = _normalize_literal_host(ascii_host)
    if literal is not None:
        return literal
    if ascii_host[0].isdigit() and _NUMERIC_HOST.fullmatch(ascii_host):
        raise UnsafeTarget("URL contains an ambiguous numeric hostname")
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
    if parsed.netloc.startswith("["):
        try:
            ipaddress.IPv6Address(parsed.hostname)
        except ValueError as error:
            raise UnsafeTarget("URL bracketed host must be a valid IPv6 literal") from error
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
    path = _remove_dot_segments(_decode_dot_segment_escapes(_normalize_escapes(path)))
    query = _normalize_escapes(parsed.query)
    normalized = urlunsplit((scheme, authority, path, query, ""))
    query_delimiter_present = "?" in url.partition("#")[0]
    if query_delimiter_present and not query:
        normalized = f"{normalized}?"
    return normalized


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
    resolution_failed = False
    try:
        addresses = tuple(resolver(normalized_host))
    except Exception:  # noqa: BLE001 - resolver implementations have no shared exception base.
        resolution_failed = True
        addresses = ()
    if resolution_failed:
        raise UnsafeTarget("DNS resolution failed") from None
    return validate_resolved_ips(addresses)


def _validate_redirect_location(location: str) -> None:
    """Reject raw references whose authority is interpreted inconsistently by clients."""

    _reject_ambiguous_text(location)
    _normalize_escapes(location)
    if location.startswith("///") or _AMBIGUOUS_REDIRECT_SCHEME.match(location):
        raise UnsafeTarget("redirect Location has ambiguous slash or authority syntax")
    try:
        parsed = urlsplit(location)
    except ValueError as error:
        raise UnsafeTarget("redirect Location authority is malformed") from error
    if location.startswith("//"):
        if (
            not parsed.netloc
            or parsed.hostname is None
            or "@" in parsed.netloc
            or "%" in parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise UnsafeTarget("redirect Location network authority is malformed")
        try:
            _port = parsed.port
        except ValueError as error:
            raise UnsafeTarget("redirect Location port is malformed") from error


def _has_explicit_empty_query(reference: str) -> bool:
    """Return whether a raw URL reference explicitly ends an empty query."""

    before_fragment = reference.partition("#")[0]
    return before_fragment.endswith("?") and not urlsplit(reference).query


def _is_fragment_only_reference(reference: str) -> bool:
    """Return whether a raw URL reference contains only a fragment component."""

    parsed = urlsplit(reference)
    return (
        reference.startswith("#")
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.path
        and not parsed.query
    )


def validate_redirect(source_url: str, location: str, resolver: Resolver) -> str:
    """Resolve, normalize, and DNS-check one redirect hop.

    Automatic redirects are forbidden. The adapter must issue the hop manually using
    the returned canonical URL exactly, then connect to a validated address or revalidate
    the connected peer. HTTPS-to-HTTP redirects are rejected. A new resolver call is
    deliberately performed for every invocation; callers must never reuse a prior hop's
    DNS result.
    """

    source = normalize_url(source_url)
    if not isinstance(location, str) or not location:
        raise UnsafeTarget("redirect Location is missing")
    _validate_redirect_location(location)
    explicit_empty_query = _has_explicit_empty_query(location)
    inherit_empty_query = _has_explicit_empty_query(source) and _is_fragment_only_reference(
        location
    )
    joined = urljoin(source, location)
    if explicit_empty_query:
        joined_parts = urlsplit(joined)
        joined = urlunsplit(
            (joined_parts.scheme, joined_parts.netloc, joined_parts.path, "", joined_parts.fragment)
        )
    destination = normalize_url(joined)
    if (explicit_empty_query or inherit_empty_query) and not destination.endswith("?"):
        destination = f"{destination}?"
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

    A conservative allowlist is applied on every hop, including same-origin redirects;
    credentials, cookies, API keys, forwarding metadata, host-derived values, referrers,
    origins, and unknown headers are never forwarded. Network adapters should regenerate
    required host-derived values from the validated destination. The mapping API cannot
    represent duplicate field names; adapters with duplicate fields must combine them
    safely before calling this function.
    """

    normalize_url(source_url)
    normalize_url(target_url)
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or _HEADER_NAME.fullmatch(key) is None:
            raise UnsafeTarget("redirect header name is invalid")
        if not isinstance(value, str) or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise UnsafeTarget("redirect header value contains a control character")
        if key.casefold() in _SAFE_REDIRECT_HEADERS:
            sanitized[key] = value
    return sanitized
