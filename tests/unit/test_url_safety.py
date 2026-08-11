from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping

import pytest

from openclaw_web.crawl.robots import RobotsPolicy
from openclaw_web.crawl.safety import (
    UnsafeTarget,
    normalize_url,
    resolve_and_validate,
    sanitize_redirect_headers,
    validate_redirect,
    validate_resolved_ips,
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
        "http://intranet/",
        "http://service.internal/",
        "http://site.example/",
        "http://hidden.onion/",
        "http://example.com\\@127.0.0.1/",
        "http://example.com/%zz",
        "http://example.com/%0d%0aX-Test:bad",
        "http://example.com:0/",
        "http://example.com:/",
        "http://example.com:99999/",
        "http://[fe80::1%25eth0]/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://0x7f000001/",
        "http://127.1/",
        "http://\N{CIRCLED DIGIT ONE}\N{CIRCLED DIGIT TWO}\N{CIRCLED DIGIT SEVEN}.\N{CIRCLED DIGIT ZERO}.\N{CIRCLED DIGIT ZERO}.\N{CIRCLED DIGIT ONE}/",
        "http://\N{CIRCLED DIGIT ONE}\N{CIRCLED DIGIT ZERO}.\N{CIRCLED DIGIT ZERO}.\N{CIRCLED DIGIT ZERO}.\N{CIRCLED DIGIT ONE}/",
        "http://\N{CIRCLED DIGIT ZERO}\N{CIRCLED DIGIT ONE}\N{CIRCLED DIGIT SEVEN}\N{CIRCLED DIGIT SEVEN}.\N{CIRCLED DIGIT ZERO}.\N{CIRCLED DIGIT ZERO}.\N{CIRCLED DIGIT ONE}/",
        "http://[v1.fe]/",
        "http://[example.com]/",
        "http://[2606:4700:4700::1111/",
        "http://example.com\r\nHost:evil.test/",
    ],
)
def test_unsafe_targets_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeTarget):
        normalize_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTP://Example.COM:80", "http://example.com/"),
        ("https://Example.COM.:443/a/./b/../c#part", "https://example.com/a/c"),
        ("https://example.com/a/.", "https://example.com/a/"),
        (
            "https://Example.COM:8443/a%2fb?q=b%2fa&a=2",
            "https://example.com:8443/a%2Fb?q=b%2Fa&a=2",
        ),
        ("https://b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.de/", "https://xn--bcher-kva.de/"),
        ("https://[2606:4700:4700::1111]:443", "https://[2606:4700:4700::1111]/"),
        ("http://93.184.216.34/path", "http://93.184.216.34/path"),
        (
            "http://\N{CIRCLED DIGIT NINE}\N{CIRCLED DIGIT THREE}.\N{CIRCLED DIGIT ONE}\N{CIRCLED DIGIT EIGHT}\N{CIRCLED DIGIT FOUR}.\N{CIRCLED DIGIT TWO}\N{CIRCLED DIGIT ONE}\N{CIRCLED DIGIT SIX}.\N{CIRCLED DIGIT THREE}\N{CIRCLED DIGIT FOUR}/",
            "http://93.184.216.34/",
        ),
        ("https://example.com/a/%2e/b", "https://example.com/a/b"),
        ("https://example.com/a/%2E/b", "https://example.com/a/b"),
        ("https://example.com/a/.%2e/b", "https://example.com/b"),
        ("https://example.com/a/%2e./b", "https://example.com/b"),
        ("https://example.com/a/%2e%2e/b", "https://example.com/b"),
        ("https://example.com/a/%252e/b", "https://example.com/a/%252e/b"),
        ("https://example.com/a/%252e%252e/b", "https://example.com/a/%252e%252e/b"),
        (
            "https://example.com/a?next=.%2e/%2f",
            "https://example.com/a?next=.%2E/%2F",
        ),
    ],
)
def test_normalize_url_canonicalizes_safe_urls(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


def test_robots_policy_checks_the_browser_equivalent_normalized_path() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private\n")

    assert not policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/public/%2e%2e/private")


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",
        "100.64.0.1",
        "198.18.0.1",
        "192.0.2.1",
        "224.0.0.1",
        "0.0.0.0",
        "fc00::1",
        "ff02::1",
        "::",
        "::ffff:127.0.0.1",
    ],
)
def test_non_global_addresses_are_rejected(address: str) -> None:
    with pytest.raises(UnsafeTarget):
        validate_resolved_ips([address])


def test_resolved_private_ip_is_rejected() -> None:
    with pytest.raises(UnsafeTarget):
        validate_resolved_ips([ipaddress.ip_address("10.1.2.3")])


def test_resolved_public_ips_return_canonical_immutable_tuple() -> None:
    addresses = validate_resolved_ips(["8.8.8.8", ipaddress.ip_address("2606:4700:4700::1111")])

    assert addresses == (
        ipaddress.ip_address("8.8.8.8"),
        ipaddress.ip_address("2606:4700:4700::1111"),
    )


def test_empty_invalid_and_mixed_dns_sets_are_rejected() -> None:
    with pytest.raises(UnsafeTarget, match="no addresses"):
        validate_resolved_ips([])
    with pytest.raises(UnsafeTarget, match="invalid address"):
        validate_resolved_ips(["not-an-ip"])
    with pytest.raises(UnsafeTarget, match="non-public"):
        validate_resolved_ips(["8.8.8.8", "10.0.0.1"])


def test_resolver_is_injected_and_failures_are_sanitized() -> None:
    def failing_resolver(_host: str) -> Iterable[str]:
        raise RuntimeError("secret-token=https://private.example/")

    with pytest.raises(UnsafeTarget, match="DNS resolution failed") as exc_info:
        resolve_and_validate("example.com", failing_resolver)

    assert "secret-token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_redirect_resolves_relative_target_and_revalidates_each_hop() -> None:
    calls: list[str] = []

    def resolver(host: str) -> Iterable[str]:
        calls.append(host)
        return ["93.184.216.34"]

    first = validate_redirect("https://example.com/start", "/next", resolver)
    second = validate_redirect(first, "https://cdn.example.com/final", resolver)

    assert first == "https://example.com/next"
    assert second == "https://cdn.example.com/final"
    assert calls == ["example.com", "cdn.example.com"]


def test_redirect_rejects_private_destination_and_https_downgrade() -> None:
    def private_resolver(_host: str) -> Iterable[str]:
        return ["10.0.0.1"]

    with pytest.raises(UnsafeTarget, match="non-public"):
        validate_redirect("https://example.com/", "https://other.example.com/", private_resolver)
    with pytest.raises(UnsafeTarget, match="downgrade"):
        validate_redirect("https://example.com/", "http://example.com/", lambda _host: ["8.8.8.8"])
    with pytest.raises(UnsafeTarget, match="missing"):
        validate_redirect("https://example.com/", "", lambda _host: ["8.8.8.8"])


def test_redirect_headers_are_copied_and_sensitive_values_removed_case_insensitively() -> None:
    headers: Mapping[str, str] = {
        "Authorization": "Bearer secret",
        "proxy-AUTHORIZATION": "Basic secret",
        "Cookie": "session=secret",
        "Host": "example.com",
        "Origin": "https://example.com",
        "Referer": "https://example.com/private",
        "X-Api-Key": "secret",
        "Accept": "text/html",
        "User-Agent": "AgentOpenClawAudit/1.0",
    }

    sanitized = sanitize_redirect_headers(
        headers, "https://example.com/a", "https://cdn.example.com/b"
    )

    assert sanitized == {"Accept": "text/html", "User-Agent": "AgentOpenClawAudit/1.0"}
    assert headers["Authorization"] == "Bearer secret"
