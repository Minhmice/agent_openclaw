"""Bounded, deterministic robots.txt policy handling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url

DEFAULT_MAX_BYTES = 512 * 1024
DEFAULT_UNAVAILABLE_DELAY = 5.0
MAX_CRAWL_DELAY = 86_400.0


@dataclass(frozen=True, slots=True)
class _DelayRule:
    agents: tuple[str, ...]
    delay: float


def _valid_delay(raw_value: str) -> float | None:
    try:
        value = float(raw_value)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0 or value > MAX_CRAWL_DELAY:
        return None
    return value


def _parse_delay_rules(text: str) -> tuple[_DelayRule, ...]:
    rules: list[_DelayRule] = []
    agents: list[str] = []
    delay: float | None = None
    records_started = False

    def finish_group() -> None:
        nonlocal agents, delay, records_started
        if agents and delay is not None:
            rules.append(_DelayRule(tuple(agents), delay))
        agents = []
        delay = None
        records_started = False

    for raw_line in [*text.splitlines(), ""]:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            if agents:
                finish_group()
            continue
        if ":" not in line:
            continue
        directive, raw_value = line.split(":", 1)
        directive = directive.strip().casefold()
        value = raw_value.strip()
        if directive == "user-agent":
            if agents and records_started:
                finish_group()
            if value:
                agents.append(value.casefold())
            continue
        if not agents:
            continue
        records_started = True
        if directive == "crawl-delay" and delay is None:
            delay = _valid_delay(value)
    return tuple(rules)


class RobotsPolicy:
    """A parsed robots policy with safe fallback behavior and explicit terminal checks."""

    __slots__ = (
        "_delay_rules",
        "_explicit",
        "_origin",
        "_parser",
        "_unavailable_delay",
        "fetch_error",
    )

    def __init__(
        self,
        parser: RobotFileParser,
        delay_rules: tuple[_DelayRule, ...],
        *,
        origin: str | None,
        explicit: bool,
        unavailable_delay: float | None,
        fetch_error: str | None,
    ) -> None:
        self._parser = parser
        self._delay_rules = delay_rules
        self._origin = origin
        self._explicit = explicit
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
        """Parse a bounded robots response.

        Invalid directives are ignored by ``urllib.robotparser``. Oversized input is
        rejected before parsing to bound memory use.
        """

        if max_bytes < 0 or len(text.encode("utf-8")) > max_bytes:
            raise ValueError("robots policy exceeds maximum size")
        normalized_origin = cls._normalize_origin(origin) if origin is not None else None
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return cls(
            parser,
            _parse_delay_rules(text),
            origin=normalized_origin,
            explicit=True,
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
        parser = RobotFileParser()
        parser.parse([])
        return cls(
            parser,
            (),
            origin=None,
            explicit=False,
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
        parsed = urlsplit(normalized)
        candidate_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if self._origin is not None and candidate_origin != self._origin:
            raise UnsafeTarget("robots policy URL does not match its origin")
        return normalized

    def allowed(self, user_agent: str, absolute_http_url: str) -> bool:
        """Return the parser decision for a validated absolute, same-origin URL."""

        normalized = self._validated_url(absolute_http_url)
        if not self._explicit:
            return True
        return self._parser.can_fetch(user_agent, normalized)

    def crawl_delay(self, user_agent: str) -> float | None:
        """Return the most-specific valid delay, falling back to the wildcard group."""

        if self._unavailable_delay is not None:
            return self._unavailable_delay
        folded_agent = user_agent.casefold()
        best_score = -1
        best_delay: float | None = None
        for rule in self._delay_rules:
            for agent in rule.agents:
                if agent == "*":
                    score = 0
                elif agent in folded_agent:
                    score = len(agent)
                else:
                    continue
                if score > best_score:
                    best_score = score
                    best_delay = rule.delay
        return best_delay

    def homepage_allowed(self, user_agent: str, homepage_url: str) -> bool:
        """Return whether an explicit response permits the origin homepage."""

        normalized = self._validated_url(homepage_url)
        parsed = urlsplit(normalized)
        homepage = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        return self.allowed(user_agent, homepage)

    def homepage_blocked(self, user_agent: str, homepage_url: str) -> bool:
        """Return a terminal signal only for an explicit homepage disallow."""

        return self._explicit and not self.homepage_allowed(user_agent, homepage_url)
