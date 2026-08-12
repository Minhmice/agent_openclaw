"""Bounded, deterministic robots.txt policy handling."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url

DEFAULT_MAX_BYTES = 512 * 1024
DEFAULT_UNAVAILABLE_DELAY = 5.0
MAX_CRAWL_DELAY = 86_400.0
_GROUP_RULE_DIRECTIVES = frozenset({"allow", "crawl-delay", "disallow", "request-rate"})
_GLOBAL_DIRECTIVES = frozenset({"host", "sitemap"})
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_ASCII_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _canonical_match_text(value: str, *, rule_pattern: bool) -> str:
    """Return stable ASCII text while preserving rule matcher syntax."""

    rendered: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if (
            character == "%"
            and index + 2 < len(value)
            and value[index + 1] in _HEX_DIGITS
            and value[index + 2] in _HEX_DIGITS
        ):
            byte = int(value[index + 1 : index + 3], 16)
            decoded = chr(byte)
            rendered.append(decoded if decoded in _ASCII_UNRESERVED else f"%{byte:02X}")
            index += 3
            continue
        if ord(character) > 127:
            rendered.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))
        elif character in {"*", "$"} and not (
            rule_pattern and (character == "*" or index == len(value) - 1)
        ):
            rendered.append(f"%{ord(character):02X}")
        else:
            rendered.append(character)
        index += 1
    return "".join(rendered)


def _rule_specificity(pattern: str) -> int:
    """Count fixed octets, excluding wildcard syntax."""

    specificity = 0
    index = 0
    while index < len(pattern):
        if pattern[index] == "*":
            index += 1
        elif (
            pattern[index] == "%"
            and index + 2 < len(pattern)
            and pattern[index + 1] in _HEX_DIGITS
            and pattern[index + 2] in _HEX_DIGITS
        ):
            specificity += 1
            index += 3
        else:
            specificity += 1
            index += 1
    return specificity


@dataclass(frozen=True, slots=True)
class _Rule:
    allow: bool
    specificity: int
    literals: tuple[str, ...]
    literal_patterns: tuple[re.Pattern[str], ...]
    has_wildcard: bool
    leading_wildcard: bool
    trailing_wildcard: bool
    end_anchored: bool

    @classmethod
    def parse(cls, *, allow: bool, raw_pattern: str) -> _Rule | None:
        if not raw_pattern:
            return None
        pattern = _canonical_match_text(raw_pattern, rule_pattern=True)
        end_anchored = pattern.endswith("$")
        if end_anchored:
            pattern = pattern[:-1]
        literals = tuple(part for part in pattern.split("*") if part)
        return cls(
            allow=allow,
            specificity=_rule_specificity(pattern),
            literals=literals,
            literal_patterns=tuple(re.compile(re.escape(part)) for part in literals),
            has_wildcard="*" in pattern,
            leading_wildcard=pattern.startswith("*"),
            trailing_wildcard=pattern.endswith("*"),
            end_anchored=end_anchored,
        )

    def matches(self, value: str) -> bool:
        if not self.literals:
            return self.has_wildcard

        if not self.has_wildcard:
            literal = self.literals[0]
            return value == literal if self.end_anchored else value.startswith(literal)

        first_index = 0
        last_index = len(self.literals)
        position = 0
        end_limit = len(value)

        if not self.leading_wildcard:
            prefix_match = self.literal_patterns[0].match(value)
            if prefix_match is None:
                return False
            position = prefix_match.end()
            first_index = 1

        if self.end_anchored and not self.trailing_wildcard:
            suffix = self.literals[-1]
            if not value.endswith(suffix):
                return False
            end_limit = len(value) - len(suffix)
            last_index -= 1
            if position > end_limit:
                return False

        for literal_pattern in self.literal_patterns[first_index:last_index]:
            match = literal_pattern.search(value, position, end_limit)
            if match is None:
                return False
            position = match.end()
        return True


@dataclass(frozen=True, slots=True)
class _Group:
    agents: tuple[str, ...]
    rules: tuple[_Rule, ...]
    delay: float | None


def _valid_delay(raw_value: str) -> float | None:
    try:
        value = float(raw_value)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0 or value > MAX_CRAWL_DELAY:
        return None
    return value


def _parse_groups(text: str) -> tuple[_Group, ...]:
    groups: list[_Group] = []
    agents: list[str] = []
    rules: list[_Rule] = []
    delay: float | None = None
    rules_started = False

    def finish_group() -> None:
        nonlocal agents, rules, delay, rules_started
        if agents:
            groups.append(_Group(tuple(agents), tuple(rules), delay))
        agents = []
        rules = []
        delay = None
        rules_started = False

    for raw_line in text.splitlines():
        # A physical blank line ends a group. A comment-only line does not.
        if not raw_line.strip():
            if agents:
                finish_group()
            continue
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        directive, raw_value = line.split(":", 1)
        directive = directive.strip().casefold()
        value = raw_value.strip()
        if directive == "user-agent":
            if value:
                if agents and rules_started:
                    finish_group()
                agents.append(value.casefold())
            continue
        if directive in _GLOBAL_DIRECTIVES:
            continue
        if directive not in _GROUP_RULE_DIRECTIVES or not agents:
            continue
        rules_started = True
        if directive == "crawl-delay" and delay is None:
            delay = _valid_delay(value)
        elif directive in {"allow", "disallow"}:
            rule = _Rule.parse(allow=directive == "allow", raw_pattern=value)
            if rule is not None:
                rules.append(rule)
    if agents:
        finish_group()
    return tuple(groups)


class RobotsPolicy:
    """A parsed robots policy with safe fallback behavior and explicit terminal checks."""

    __slots__ = (
        "_explicit",
        "_groups",
        "_max_bytes",
        "_origin",
        "_unavailable_delay",
        "fetch_error",
    )

    def __init__(
        self,
        groups: tuple[_Group, ...],
        *,
        origin: str | None,
        explicit: bool,
        max_bytes: int | None,
        unavailable_delay: float | None,
        fetch_error: str | None,
    ) -> None:
        self._groups = groups
        self._origin = origin
        self._explicit = explicit
        self._max_bytes = max_bytes
        self._unavailable_delay = unavailable_delay
        self.fetch_error = fetch_error

    @classmethod
    def parse(
        cls,
        text: str,
        *,
        origin: str | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> RobotsPolicy:
        """Parse a bounded robots response."""

        if max_bytes < 0 or len(text.encode("utf-8")) > max_bytes:
            raise ValueError("robots policy exceeds maximum size")
        normalized_origin = cls._normalize_origin(origin) if origin is not None else None
        return cls(
            _parse_groups(text),
            origin=normalized_origin,
            explicit=True,
            max_bytes=max_bytes,
            unavailable_delay=None,
            fetch_error=None,
        )

    @classmethod
    def unavailable(
        cls, fetch_error: object, *, default_delay: float = DEFAULT_UNAVAILABLE_DELAY
    ) -> RobotsPolicy:
        """Create an allow-with-conservative-delay policy for a failed robots fetch."""

        del fetch_error
        delay = (
            default_delay
            if math.isfinite(default_delay) and default_delay > 0
            else DEFAULT_UNAVAILABLE_DELAY
        )
        return cls(
            (),
            origin=None,
            explicit=False,
            max_bytes=None,
            unavailable_delay=delay,
            fetch_error="robots policy unavailable",
        )

    @staticmethod
    def _normalize_origin(origin: str) -> str:
        normalized = normalize_url(origin)
        parsed = urlsplit(normalized)
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    def _validated_url(self, absolute_http_url: str) -> str:
        normalized = normalize_url(absolute_http_url)
        if self._max_bytes is not None and len(normalized.encode("utf-8")) > self._max_bytes:
            raise UnsafeTarget("robots policy URL exceeds maximum size")
        parsed = urlsplit(normalized)
        candidate_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if self._origin is not None and candidate_origin != self._origin:
            raise UnsafeTarget("robots policy URL does not match its origin")
        return normalized

    def _matching_groups(self, user_agent: str) -> tuple[tuple[_Group, ...], tuple[_Group, ...]]:
        folded_agent = user_agent.casefold()
        wildcard_groups: list[_Group] = []
        specific_groups: list[_Group] = []
        best_score = -1
        for group in self._groups:
            if "*" in group.agents:
                wildcard_groups.append(group)
            score = max(
                (len(agent) for agent in group.agents if agent != "*" and agent in folded_agent),
                default=-1,
            )
            if score > best_score:
                best_score = score
                specific_groups = [group]
            elif score == best_score and score >= 0:
                specific_groups.append(group)
        return tuple(specific_groups), tuple(wildcard_groups)

    def allowed(self, user_agent: str, absolute_http_url: str) -> bool:
        """Return the merged longest-match decision for a same-origin URL."""

        normalized = self._validated_url(absolute_http_url)
        if not self._explicit:
            return True
        parsed = urlsplit(normalized)
        path_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        match_value = _canonical_match_text(path_query, rule_pattern=False)
        specific_groups, wildcard_groups = self._matching_groups(user_agent)
        selected_groups = specific_groups or wildcard_groups
        best_specificity = -1
        allowed = True
        for group in selected_groups:
            for rule in group.rules:
                if not rule.matches(match_value):
                    continue
                if rule.specificity > best_specificity:
                    best_specificity = rule.specificity
                    allowed = rule.allow
                elif rule.specificity == best_specificity and rule.allow:
                    allowed = True
        return allowed

    def crawl_delay(self, user_agent: str) -> float | None:
        """Return a valid delay from the selected groups, then wildcard fallback."""

        if self._unavailable_delay is not None:
            return self._unavailable_delay
        specific_groups, wildcard_groups = self._matching_groups(user_agent)
        for group in specific_groups or wildcard_groups:
            if group.delay is not None:
                return group.delay
        if specific_groups:
            for group in wildcard_groups:
                if group.delay is not None:
                    return group.delay
        return None

    def homepage_allowed(self, user_agent: str, homepage_url: str) -> bool:
        """Return whether an explicit response permits the origin homepage."""

        normalized = self._validated_url(homepage_url)
        parsed = urlsplit(normalized)
        homepage = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        return self.allowed(user_agent, homepage)

    def homepage_blocked(self, user_agent: str, homepage_url: str) -> bool:
        """Return a terminal signal only for an explicit homepage disallow."""

        return self._explicit and not self.homepage_allowed(user_agent, homepage_url)
