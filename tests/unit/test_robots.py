from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from openclaw_web.crawl import robots as robots_module
from openclaw_web.crawl.robots import MAX_CRAWL_DELAY, RobotsPolicy
from openclaw_web.crawl.safety import UnsafeTarget


def test_robots_disallow_and_delay_are_honored() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private\nCrawl-delay: 3\n")

    assert not policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/private/a")
    assert policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/public")
    assert policy.crawl_delay("AgentOpenClawAudit/1.0") == 3


def test_specific_groups_allow_precedence_and_query() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: AgentOpenClawAudit
        Allow: /private/public
        Disallow: /private

        User-agent: *
        Disallow: /fallback
        """
    )

    assert policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/private/public?a=1")
    assert not policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/private/secret?a=1")
    assert policy.allowed("OtherBot", "https://example.com/private/secret")
    assert not policy.allowed("OtherBot", "https://example.com/fallback?q=1")


def test_rule_order_does_not_override_longest_match() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nAllow: /\nDisallow: /private\n")

    assert policy.allowed("Bot", "https://example.com/public")
    assert not policy.allowed("Bot", "https://example.com/private/report")


def test_repeated_matching_groups_are_merged() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: AuditBot
        Disallow: /private

        User-agent: OtherBot
        Disallow: /

        User-agent: auditbot
        Allow: /private/public
        """
    )

    assert policy.allowed("AUDITBOT/1.0", "https://example.com/private/public/report")
    assert not policy.allowed("AUDITBOT/1.0", "https://example.com/private/secret")


def test_repeated_wildcard_groups_are_merged_without_a_specific_match() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: *
        Disallow: /private

        User-agent: AuditBot
        Disallow: /

        User-agent: *
        Allow: /private/public
        """
    )

    assert policy.allowed("OtherBot", "https://example.com/private/public/report")
    assert not policy.allowed("OtherBot", "https://example.com/private/secret")


def test_most_specific_matching_product_token_wins() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: Audit
        Disallow: /

        User-agent: AuditBot
        Allow: /

        User-agent: *
        Disallow: /
        """
    )

    assert policy.allowed("Company-AUDITBOT/2.0", "https://example.com/report")
    assert not policy.allowed("UnrelatedBot", "https://example.com/report")


def test_longest_rule_wins_and_allow_wins_equal_specificity() -> None:
    longest = RobotsPolicy.parse("User-agent: *\nDisallow: /private\nAllow: /private/public\n")
    equal = RobotsPolicy.parse("User-agent: *\nDisallow: /same\nAllow: /same\n")

    assert longest.allowed("Bot", "https://example.com/private/public/report")
    assert not longest.allowed("Bot", "https://example.com/private/secret")
    assert equal.allowed("Bot", "https://example.com/same")


def test_wildcard_and_trailing_end_anchor_are_supported() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /*.php$\nAllow: /public/*.php$\n")

    assert not policy.allowed("Bot", "https://example.com/index.php")
    assert policy.allowed("Bot", "https://example.com/index.php?id=1")
    assert policy.allowed("Bot", "https://example.com/public/index.php")
    assert policy.allowed("Bot", "https://example.com/public/index.php?id=1")


def test_end_anchor_without_a_path_does_not_match_nonempty_url_paths() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: $\n")

    assert policy.allowed("Bot", "https://example.com/")


def test_empty_allow_and_disallow_rules_have_no_effect() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow:\nAllow:\nDisallow: /private\n")

    assert policy.allowed("Bot", "https://example.com/")
    assert not policy.allowed("Bot", "https://example.com/private")


def test_rules_match_normalized_path_and_query_case_sensitively() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: *
        Disallow: /search?private=1
        Disallow: /CaseSensitive
        """
    )

    assert not policy.allowed("Bot", "https://example.com/search?private=1&view=full")
    assert policy.allowed("Bot", "https://example.com/search?private=2")
    assert not policy.allowed("Bot", "https://example.com/CaseSensitive")
    assert policy.allowed("Bot", "https://example.com/casesensitive")


@pytest.mark.parametrize("rule_path", ["/Az09-._~", "/%41%7A%30%39%2D%2E%5F%7E"])
@pytest.mark.parametrize("url_path", ["/Az09-._~", "/%41%7A%30%39%2D%2E%5F%7E"])
def test_raw_and_percent_encoded_ascii_unreserved_octets_compare_consistently(
    rule_path: str, url_path: str
) -> None:
    policy = RobotsPolicy.parse(f"User-agent: *\nDisallow: {rule_path}\n")

    assert not policy.allowed("Bot", f"https://example.com{url_path}")


def test_encoded_unreserved_allow_rule_wins_as_the_longest_match() -> None:
    policy = RobotsPolicy.parse(
        "User-agent: *\nDisallow: /private\nAllow: /private/%70%75%62%6C%69%63\n"
    )

    assert policy.allowed("Bot", "https://example.com/private/public/report")
    assert not policy.allowed("Bot", "https://example.com/private/secret")


@pytest.mark.parametrize(
    ("encoded_literal", "raw_literal"),
    [("%2A", "*"), ("%24", "$")],
)
def test_percent_encoded_rule_metacharacters_match_literal_uri_characters(
    encoded_literal: str, raw_literal: str
) -> None:
    policy = RobotsPolicy.parse(f"User-agent: *\nDisallow: /literal{encoded_literal}value\n")

    assert not policy.allowed("Bot", f"https://example.com/literal{raw_literal}value")
    assert not policy.allowed("Bot", f"https://example.com/literal{encoded_literal}value")


def test_percent_encoded_rule_metacharacters_do_not_become_matcher_syntax() -> None:
    literal_wildcard = RobotsPolicy.parse("User-agent: *\nDisallow: /literal%2Avalue\n")
    literal_dollar = RobotsPolicy.parse("User-agent: *\nDisallow: /literal%24\n")

    assert literal_wildcard.allowed("Bot", "https://example.com/literal-any-value")
    assert not literal_dollar.allowed("Bot", "https://example.com/literal$continued")


@pytest.mark.parametrize("rule_path", ["/caf%C3%A9", "/café"])
@pytest.mark.parametrize("url_path", ["/caf%C3%A9", "/café"])
def test_percent_encoded_and_unicode_paths_compare_consistently(
    rule_path: str, url_path: str
) -> None:
    policy = RobotsPolicy.parse(f"User-agent: *\nDisallow: {rule_path}\n")

    assert not policy.allowed("Bot", f"https://example.com{url_path}")


def test_percent_encoded_reserved_octet_remains_distinct_from_a_separator() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private%2fsecret\n")

    assert not policy.allowed("Bot", "https://example.com/private%2Fsecret")
    assert policy.allowed("Bot", "https://example.com/private/secret")


@pytest.mark.parametrize("invalid_escape", ["%", "%G0", "%0G"])
def test_invalid_uri_percent_escapes_are_rejected(invalid_escape: str) -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private\n")

    with pytest.raises(UnsafeTarget, match="invalid percent escape"):
        policy.allowed("Bot", f"https://example.com/private{invalid_escape}")


def test_group_boundaries_and_global_metadata_apply_to_access_rules() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: FirstBot
        Sitemap: https://example.com/sitemap.xml
        User-agent: SecondBot
        Disallow: /shared
        User-agent: ThirdBot
        Disallow: /third

        User-agent: FourthBot

        User-agent: FifthBot
        Disallow: /fifth
        """
    )

    assert not policy.allowed("FirstBot", "https://example.com/shared")
    assert not policy.allowed("SecondBot", "https://example.com/shared")
    assert policy.allowed("ThirdBot", "https://example.com/shared")
    assert not policy.allowed("ThirdBot", "https://example.com/third")
    assert not policy.allowed("FourthBot", "https://example.com/fifth")
    assert not policy.allowed("FifthBot", "https://example.com/fifth")


def test_crawl_delay_prefers_specific_agent_and_accepts_fraction() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: *
        Crawl-delay: 7
        Disallow: /fallback

        User-agent: AgentOpenClawAudit
        Crawl-delay: 0.25
        """
    )

    assert policy.crawl_delay("AgentOpenClawAudit/1.0") == 0.25
    assert policy.crawl_delay("OtherBot") == 7


def test_crawl_delay_uses_the_same_most_specific_merged_groups() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: Audit
        Crawl-delay: 9
        Disallow: /audit-only

        User-agent: AuditBot
        Disallow: /private

        User-agent: auditbot
        Crawl-delay: 0.75
        Allow: /

        User-agent: *
        Crawl-delay: 4
        """
    )

    assert policy.crawl_delay("AUDITBOT/1.0") == 0.75
    assert policy.crawl_delay("OtherBot") == 4


def test_crawl_delay_group_ignores_comment_only_and_inline_comments() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: FirstBot # first member
        # This comment is not a physical blank line.
        User-agent: SecondBot # second member
        # Nor is this one.
        Crawl-delay: 1.5 # shared delay
        """
    )

    assert policy.crawl_delay("FirstBot/1.0") == 1.5
    assert policy.crawl_delay("SecondBot/1.0") == 1.5


def test_physical_blank_line_does_not_end_group_before_an_access_rule() -> None:
    """Blank lines are insignificant until access-rule grammar starts a new group."""

    policy = RobotsPolicy.parse("User-agent: FirstBot\n\nUser-agent: SecondBot\nCrawl-delay: 2\n")

    assert policy.crawl_delay("FirstBot/1.0") == 2
    assert policy.crawl_delay("SecondBot/1.0") == 2


def test_extensions_do_not_split_or_start_access_rule_groups() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: FirstBot
        Crawl-delay: 1.5
        Request-rate: 1/10
        Clean-param: ref /catalog

        User-agent: SecondBot
        Disallow: /shared
        """
    )

    assert not policy.allowed("FirstBot/1.0", "https://example.com/shared")
    assert not policy.allowed("SecondBot/1.0", "https://example.com/shared")
    assert policy.crawl_delay("FirstBot/1.0") == 1.5
    assert policy.crawl_delay("SecondBot/1.0") == 1.5


def test_global_directives_do_not_split_consecutive_user_agents() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: FirstBot
        Sitemap: https://example.com/sitemap.xml
        # Global metadata may appear between group members.
        Host: example.com
        User-agent: SecondBot
        Crawl-delay: 3
        """
    )

    assert policy.crawl_delay("FirstBot/1.0") == 3
    assert policy.crawl_delay("SecondBot/1.0") == 3


def test_user_agent_after_rule_record_starts_new_crawl_delay_group() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: FirstBot
        Disallow: /private
        User-agent: SecondBot
        Crawl-delay: 4
        """
    )

    assert policy.crawl_delay("FirstBot/1.0") is None
    assert policy.crawl_delay("SecondBot/1.0") == 4


@pytest.mark.parametrize("invalid", ["-1", "NaN", "inf", "0", "999999999999"])
def test_invalid_crawl_delay_is_ignored_with_wildcard_fallback(invalid: str) -> None:
    policy = RobotsPolicy.parse(
        f"User-agent: AgentOpenClawAudit\nCrawl-delay: {invalid}\n\nUser-agent: *\nCrawl-delay: 4\n"
    )

    assert policy.crawl_delay("AgentOpenClawAudit/1.0") == 4


def test_unavailable_policy_allows_with_conservative_delay_and_sanitized_error() -> None:
    policy = RobotsPolicy.unavailable(
        RuntimeError("token=secret https://example.com/robots.txt"),
        origin="https://example.com/robots.txt",
        default_delay=5,
    )

    assert policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/private")
    assert policy.crawl_delay("AgentOpenClawAudit/1.0") == 5
    assert policy.fetch_error == "robots policy unavailable"
    assert policy.fetch_outcome.value == "http_unavailable"
    assert not policy.homepage_blocked("AgentOpenClawAudit/1.0", "https://example.com/")
    with pytest.raises(UnsafeTarget, match="origin"):
        policy.allowed("AgentOpenClawAudit/1.0", "https://other.example.com/")


@pytest.mark.parametrize(
    "invalid_delay",
    ["5", True, None, math.nan, math.inf, 0, -1, MAX_CRAWL_DELAY + 1],
)
def test_unavailable_policy_validates_fallback_delay_type_and_range(
    invalid_delay: object,
) -> None:
    policy = RobotsPolicy.unavailable(
        RuntimeError("secret"),
        origin="https://example.com/robots.txt",
        default_delay=invalid_delay,
    )

    assert policy.crawl_delay("Bot") == 5


def test_unreachable_policy_fails_closed_and_sanitizes_error() -> None:
    policy = RobotsPolicy.unreachable(
        RuntimeError("token=secret https://example.com/robots.txt"),
        origin="https://example.com/robots.txt",
    )

    assert not policy.allowed("Bot", "https://example.com/public")
    assert policy.fetch_error == "robots policy unreachable"
    assert policy.fetch_outcome.value == "unreachable"
    assert policy.homepage_blocked("Bot", "https://example.com/")
    with pytest.raises(UnsafeTarget, match="origin"):
        policy.homepage_blocked("Bot", "https://other.example.com/")


def test_unreachable_policy_can_use_a_same_origin_loaded_cache() -> None:
    cached = RobotsPolicy.parse(
        "User-agent: *\nDisallow: /private\n", origin="https://example.com/"
    )

    policy = RobotsPolicy.unreachable(
        RuntimeError("secret"),
        origin="https://example.com/robots.txt",
        cached_policy=cached,
    )

    assert not policy.allowed("Bot", "https://example.com/private")
    assert policy.allowed("Bot", "https://example.com/public")


def test_explicit_homepage_disallow_is_terminal() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /\n")

    assert not policy.homepage_allowed("AgentOpenClawAudit/1.0", "https://example.com/")
    assert policy.homepage_blocked("AgentOpenClawAudit/1.0", "https://example.com/")


def test_origin_and_absolute_url_are_validated() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow:\n", origin="https://example.com/")

    assert policy.allowed("Bot", "https://example.com/path")
    with pytest.raises(UnsafeTarget, match="origin"):
        policy.allowed("Bot", "https://other.example.com/path")
    with pytest.raises(UnsafeTarget):
        policy.allowed("Bot", "/relative")


def test_unscoped_policy_binds_to_first_origin_and_rejects_reuse() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private\n")

    assert not policy.allowed("Bot", "https://example.com/private")
    with pytest.raises(UnsafeTarget, match="origin"):
        policy.allowed("Bot", "https://other.example.com/private")
    with pytest.raises(UnsafeTarget, match="origin"):
        policy.homepage_allowed("Bot", "https://other.example.com/")


def test_unscoped_policy_origin_binding_is_thread_safe() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow:\n")
    barrier = threading.Barrier(2)

    def use_origin(url: str) -> object:
        barrier.wait()
        try:
            return policy.allowed("Bot", url)
        except UnsafeTarget as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                use_origin,
                ["https://first.example.com/", "https://second.example.com/"],
            )
        )

    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, UnsafeTarget) for result in results) == 1


def test_empty_malformed_and_oversized_policies_are_deterministic() -> None:
    assert RobotsPolicy.parse("").allowed("Bot", "https://example.com/")
    assert RobotsPolicy.parse("not a directive\n:\n").allowed("Bot", "https://example.com/")
    with pytest.raises(ValueError, match="maximum size"):
        RobotsPolicy.parse("x" * 33, max_bytes=32)


def test_match_input_is_bounded_by_the_policy_size_limit() -> None:
    policy = RobotsPolicy.parse("", max_bytes=0)

    assert policy.allowed("Bot", f"https://example.com/{'x' * 100}")

    with pytest.raises(UnsafeTarget, match="match URL"):
        policy.allowed("Bot", f"https://example.com/{'x' * (16 * 1024)}")


def test_bare_empty_query_delimiter_participates_in_robots_matching() -> None:
    policy = RobotsPolicy.parse(
        "User-agent: *\nDisallow: /search?\n", origin="https://example.com/"
    )

    assert policy.allowed("Bot", "https://example.com/search")
    assert not policy.allowed("Bot", "https://example.com/search?")


@pytest.mark.parametrize(
    ("malformed_product", "request_agent"),
    [
        ("Bad Bot", "Bad Bot"),
        ("Bad/Bot", "Bad/Bot"),
        ("Bot2", "Bot2"),
        ("B\N{LATIN SMALL LETTER O WITH DIAERESIS}t", "B\N{LATIN SMALL LETTER O WITH DIAERESIS}t"),
        ("Bad*Bot", "Bad*Bot"),
    ],
)
def test_malformed_user_agent_product_tokens_cannot_outscore_wildcard(
    malformed_product: str, request_agent: str
) -> None:
    policy = RobotsPolicy.parse(
        f"User-agent: {malformed_product}\nDisallow: /\nUser-agent: *\nAllow: /\n"
    )

    assert policy.allowed(request_agent, "https://example.com/public")


def test_large_policy_rule_matching_stops_at_deterministic_work_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "User-agent: *\n" + "".join(f"Disallow: /{index:05x}\n" for index in range(30_000))
    assert 480 * 1024 < len(text.encode("utf-8")) < 512 * 1024
    policy = RobotsPolicy.parse(text)
    calls = 0
    original_matches = robots_module._Rule.matches

    def counting_matches(rule: object, value: str) -> bool:
        nonlocal calls
        calls += 1
        return original_matches(rule, value)  # type: ignore[arg-type]

    monkeypatch.setattr(robots_module._Rule, "matches", counting_matches)

    assert not policy.allowed("Bot", "https://example.com/not-listed")
    assert calls <= 10_000
