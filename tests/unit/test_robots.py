from __future__ import annotations

import pytest

from openclaw_web.crawl.robots import RobotsPolicy
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
    assert policy.allowed("FourthBot", "https://example.com/fifth")
    assert not policy.allowed("FifthBot", "https://example.com/fifth")


def test_crawl_delay_prefers_specific_agent_and_accepts_fraction() -> None:
    policy = RobotsPolicy.parse(
        """
        User-agent: *
        Crawl-delay: 7

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

        User-agent: AuditBot
        Disallow: /private

        User-agent: auditbot
        Crawl-delay: 0.75

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


def test_physical_blank_line_ends_crawl_delay_group() -> None:
    """A physical blank line separates groups, even before any rule record."""

    policy = RobotsPolicy.parse("User-agent: FirstBot\n\nUser-agent: SecondBot\nCrawl-delay: 2\n")

    assert policy.crawl_delay("FirstBot/1.0") is None
    assert policy.crawl_delay("SecondBot/1.0") == 2


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
        RuntimeError("token=secret https://example.com/robots.txt"), default_delay=5
    )

    assert policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/private")
    assert policy.crawl_delay("AgentOpenClawAudit/1.0") == 5
    assert policy.fetch_error == "robots policy unavailable"
    assert not policy.homepage_blocked("AgentOpenClawAudit/1.0", "https://example.com/")


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


def test_empty_malformed_and_oversized_policies_are_deterministic() -> None:
    assert RobotsPolicy.parse("").allowed("Bot", "https://example.com/")
    assert RobotsPolicy.parse("not a directive\n:\n").allowed("Bot", "https://example.com/")
    with pytest.raises(ValueError, match="maximum size"):
        RobotsPolicy.parse("x" * 33, max_bytes=32)


def test_match_input_is_bounded_by_the_policy_size_limit() -> None:
    policy = RobotsPolicy.parse("", max_bytes=32)

    with pytest.raises(UnsafeTarget, match="maximum size"):
        policy.allowed("Bot", f"https://example.com/{'x' * 33}")
