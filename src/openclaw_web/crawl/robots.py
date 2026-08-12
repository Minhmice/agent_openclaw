"""Bounded, deterministic robots.txt policy handling.

Fetch adapters must enforce the robots response byte limit while streaming; the parser
cap is a second line of defense. The RFC-compatible default accepts at least 500 KiB.
Deployments may choose a smaller custom cap only as an explicitly conservative policy.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url

DEFAULT_MAX_BYTES = 512 * 1024
DEFAULT_MAX_MATCH_URL_BYTES = 16 * 1024
DEFAULT_MAX_RULE_MATCH_OPERATIONS = 10_000
DEFAULT_UNAVAILABLE_DELAY = 5.0
MAX_CRAWL_DELAY = 86_400.0
_GROUP_RULE_DIRECTIVES = frozenset({"allow", "crawl-delay", "disallow", "request-rate"})
_GLOBAL_DIRECTIVES = frozenset({"host", "sitemap"})
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_ASCII_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_USER_AGENT_PRODUCT = re.compile(r"(?:\*|[A-Za-z_-]+)\Z", re.ASCII)


class RobotsFetchOutcome(Enum):
    """How fetching robots.txt concluded."""

    LOADED = "loaded"
    HTTP_UNAVAILABLE = "http_unavailable"
    UNREACHABLE = "unreachable"


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
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0 or value > MAX_CRAWL_DELAY:
        return None
    return value


def _fallback_delay(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_UNAVAILABLE_DELAY
    delay = float(value)
    if not math.isfinite(delay) or delay <= 0 or delay > MAX_CRAWL_DELAY:
        return DEFAULT_UNAVAILABLE_DELAY
    return delay


def _parse_groups(text: str) -> tuple[_Group, ...]:
    groups: list[_Group] = []
    agents: list[str] = []
    rules: list[_Rule] = []
    delay: float | None = None
    access_rules_started = False

    def finish_group() -> None:
        nonlocal agents, rules, delay, access_rules_started
        if agents:
            groups.append(_Group(tuple(agents), tuple(rules), delay))
        agents = []
        rules = []
        delay = None
        access_rules_started = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        directive, raw_value = line.split(":", 1)
        directive = directive.strip().casefold()
        value = raw_value.strip()
        if directive == "user-agent":
            if _USER_AGENT_PRODUCT.fullmatch(value) is not None:
                if agents and access_rules_started:
                    finish_group()
                agents.append(value.casefold())
            continue
        if directive in _GLOBAL_DIRECTIVES:
            continue
        if directive not in _GROUP_RULE_DIRECTIVES or not agents:
            continue
        if directive == "crawl-delay" and delay is None:
            delay = _valid_delay(value)
        elif directive in {"allow", "disallow"}:
            rule = _Rule.parse(allow=directive == "allow", raw_pattern=value)
            if rule is not None:
                access_rules_started = True
                rules.append(rule)
    if agents:
        finish_group()
    return tuple(groups)


class RobotsPolicy:
    """A parsed robots policy with safe fallback behavior and explicit terminal checks."""

    __slots__ = (
        "_explicit",
        "_fetch_outcome",
        "_groups",
        "_max_bytes",
        "_max_match_url_bytes",
        "_max_rule_operations",
        "_origin",
        "_origin_lock",
        "_unavailable_delay",
        "fetch_error",
    )

    def __init__(
        self,
        groups: tuple[_Group, ...],
        *,
        origin: str | None,
        explicit: bool,
        fetch_outcome: RobotsFetchOutcome,
        max_bytes: int | None,
        max_match_url_bytes: int,
        max_rule_operations: int,
        unavailable_delay: float | None,
        fetch_error: str | None,
    ) -> None:
        self._groups = groups
        self._origin = origin
        self._origin_lock = Lock()
        self._explicit = explicit
        self._fetch_outcome = fetch_outcome
        self._max_bytes = max_bytes
        self._max_match_url_bytes = max_match_url_bytes
        self._max_rule_operations = max_rule_operations
        self._unavailable_delay = unavailable_delay
        self.fetch_error = fetch_error

    @property
    def fetch_outcome(self) -> RobotsFetchOutcome:
        """Return the sanitized robots fetch classification."""

        return self._fetch_outcome

    @classmethod
    def parse(
        cls,
        text: str,
        *,
        origin: str | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_match_url_bytes: int = DEFAULT_MAX_MATCH_URL_BYTES,
        max_rule_operations: int = DEFAULT_MAX_RULE_MATCH_OPERATIONS,
    ) -> RobotsPolicy:
        """Parse a bounded robots response.

        Production fetchers should pass ``origin``. Omitting it is a seed-compatibility
        path: the first URL decision binds the policy to that origin under a lock, and
        every later cross-origin use is rejected.
        """

        if max_bytes < 0 or len(text.encode("utf-8")) > max_bytes:
            raise ValueError("robots policy exceeds maximum size")
        if max_match_url_bytes <= 0:
            raise ValueError("robots match URL maximum must be positive")
        if max_rule_operations <= 0:
            raise ValueError("robots rule operation maximum must be positive")
        normalized_origin = cls._normalize_origin(origin) if origin is not None else None
        return cls(
            _parse_groups(text),
            origin=normalized_origin,
            explicit=True,
            fetch_outcome=RobotsFetchOutcome.LOADED,
            max_bytes=max_bytes,
            max_match_url_bytes=max_match_url_bytes,
            max_rule_operations=max_rule_operations,
            unavailable_delay=None,
            fetch_error=None,
        )

    @classmethod
    def unavailable(
        cls,
        fetch_error: object,
        *,
        origin: str | None = None,
        default_delay: object = DEFAULT_UNAVAILABLE_DELAY,
    ) -> RobotsPolicy:
        """Create allow-with-delay policy for an explicit HTTP-unavailable response.

        This constructor represents responses such as HTTP 4xx, not opaque network or
        server failures. Use :meth:`unreachable` for those failures.
        """

        del fetch_error
        normalized_origin = cls._normalize_origin(origin) if origin is not None else None
        return cls(
            (),
            origin=normalized_origin,
            explicit=False,
            fetch_outcome=RobotsFetchOutcome.HTTP_UNAVAILABLE,
            max_bytes=None,
            max_match_url_bytes=DEFAULT_MAX_MATCH_URL_BYTES,
            max_rule_operations=DEFAULT_MAX_RULE_MATCH_OPERATIONS,
            unavailable_delay=_fallback_delay(default_delay),
            fetch_error="robots policy unavailable",
        )

    @classmethod
    def unreachable(
        cls,
        fetch_error: object,
        *,
        origin: str | None = None,
        cached_policy: RobotsPolicy | None = None,
    ) -> RobotsPolicy:
        """Fail closed for a network/server failure, or return a valid cached policy."""

        del fetch_error
        normalized_origin = cls._normalize_origin(origin) if origin is not None else None
        if cached_policy is not None:
            if cached_policy.fetch_outcome is not RobotsFetchOutcome.LOADED:
                raise ValueError("cached robots policy is not a loaded policy")
            if normalized_origin is not None:
                cached_policy._bind_origin(normalized_origin)
            return cached_policy
        return cls(
            (),
            origin=normalized_origin,
            explicit=False,
            fetch_outcome=RobotsFetchOutcome.UNREACHABLE,
            max_bytes=None,
            max_match_url_bytes=DEFAULT_MAX_MATCH_URL_BYTES,
            max_rule_operations=DEFAULT_MAX_RULE_MATCH_OPERATIONS,
            unavailable_delay=None,
            fetch_error="robots policy unreachable",
        )

    @staticmethod
    def _normalize_origin(origin: str) -> str:
        normalized = normalize_url(origin)
        parsed = urlsplit(normalized)
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    def _bind_origin(self, candidate_origin: str) -> None:
        with self._origin_lock:
            if self._origin is None:
                self._origin = candidate_origin
            elif candidate_origin != self._origin:
                raise UnsafeTarget("robots policy URL does not match its origin")

    def _validated_url(self, absolute_http_url: str) -> str:
        normalized = normalize_url(absolute_http_url)
        if len(normalized.encode("utf-8")) > self._max_match_url_bytes:
            raise UnsafeTarget("robots policy match URL exceeds maximum size")
        parsed = urlsplit(normalized)
        candidate_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        self._bind_origin(candidate_origin)
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
        if self._fetch_outcome is RobotsFetchOutcome.UNREACHABLE:
            return False
        if not self._explicit:
            return True
        parsed = urlsplit(normalized)
        has_query = bool(parsed.query) or normalized.endswith("?")
        path_query = parsed.path + (f"?{parsed.query}" if has_query else "")
        match_value = _canonical_match_text(path_query, rule_pattern=False)
        specific_groups, wildcard_groups = self._matching_groups(user_agent)
        selected_groups = specific_groups or wildcard_groups
        best_specificity = -1
        allowed = True
        operations = 0
        for group in selected_groups:
            for rule in group.rules:
                rule_operations = max(1, len(rule.literals))
                if operations + rule_operations > self._max_rule_operations:
                    return False
                operations += rule_operations
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

        allowed = self.homepage_allowed(user_agent, homepage_url)
        return (
            self._fetch_outcome is RobotsFetchOutcome.UNREACHABLE or self._explicit
        ) and not allowed
