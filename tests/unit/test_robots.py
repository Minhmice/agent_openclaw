from __future__ import annotations

import pytest

from openclaw_web.crawl.robots import RobotsPolicy
from openclaw_web.crawl.safety import UnsafeTarget


def test_robots_disallow_and_delay_are_honored() -> None:
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private\nCrawl-delay: 3\n")

    assert not policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/private/a")
    assert policy.allowed("AgentOpenClawAudit/1.0", "https://example.com/public")
    assert policy.crawl_delay("AgentOpenClawAudit/1.0") == 3


def test_robotparser_handles_specific_groups_allow_precedence_and_query() -> None:
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
