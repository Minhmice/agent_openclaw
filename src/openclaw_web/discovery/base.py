"""Shared contracts, sanitised failures, and deterministic provider throttling."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from openclaw_web.models import CandidateSeed
from openclaw_web.settings import MarketConfig

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
AsyncSleeper = Callable[[float], Awaitable[None]]
StatusHandler = Callable[[httpx.Response], None]

_MAX_JSON_DEPTH = 64
_MAX_RESPONSE_BYTES = 5_000_000


class DiscoveryError(RuntimeError):
    """Base discovery failure whose messages are safe for operator surfaces."""


class DiscoveryConfigurationError(DiscoveryError):
    """A provider or source is not safely configured."""


class DiscoveryProviderError(DiscoveryError):
    """An automatic discovery provider failed."""


class DiscoveryRateLimitError(DiscoveryProviderError):
    """A provider rejected a request due to its request quota."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DiscoveryPayloadError(DiscoveryError):
    """A bounded source/provider payload did not satisfy the strict contract."""


class BatchDiscoverySource(Protocol):
    """Synchronous operator-provided source (manual, CSV, or JSON)."""

    def discover(self) -> Sequence[CandidateSeed]: ...


class AutomaticDiscoveryProvider(Protocol):
    """Asynchronous external provider with an explicit readiness state."""

    name: str

    async def discover(
        self, market: MarketConfig, cohort: str, limit: int
    ) -> Sequence[CandidateSeed]: ...

    def readiness(self) -> str: ...


class DiscoverySource(AutomaticDiscoveryProvider, Protocol):
    """Public asynchronous discovery source contract."""

    async def aclose(self) -> None: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalized_clock(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DiscoveryConfigurationError("discovery clock must return an aware datetime")
    return value.astimezone(UTC)


def validate_limit(limit: int, *, cap: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit <= 0:
        raise ValueError("limit must be positive")
    if limit > cap:
        raise ValueError(f"limit must not exceed {cap}")
    return limit


def positive_int(value: int, *, field: str, cap: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value <= 0 or value > cap:
        raise ValueError(f"{field} must be between 1 and {cap}")
    return value


def nonnegative_float(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} must be a real number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return result


def response_byte_limit(value: int) -> int:
    return positive_int(value, field="max_response_bytes", cap=_MAX_RESPONSE_BYTES)


def _strict_json_bytes(data: bytes, *, max_depth: int = _MAX_JSON_DEPTH) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("nonstandard JSON constant")

    try:
        parsed = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
        stack: list[tuple[object, int]] = [(parsed, 1)]
        while stack:
            value, depth = stack.pop()
            if depth > max_depth:
                raise ValueError("JSON nesting is too deep")
            if isinstance(value, dict):
                stack.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                stack.extend((item, depth + 1) for item in value)
        return parsed
    except (RecursionError, UnicodeError, ValueError):
        raise DiscoveryPayloadError("invalid provider payload") from None


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: object,
    timeout: httpx.Timeout,
    max_response_bytes: int,
    status_handler: StatusHandler,
) -> Any:
    """Send one bounded streaming JSON request and return strict decoded JSON."""

    limit = response_byte_limit(max_response_bytes)
    try:
        async with client.stream(
            method, url, headers=headers, json=payload, timeout=timeout
        ) as response:
            status_handler(response)
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json" and not media_type.endswith("+json"):
                raise DiscoveryPayloadError("invalid provider payload")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared, 10)
                except ValueError:
                    raise DiscoveryPayloadError("invalid provider payload") from None
                if declared_size < 0 or declared_size > limit:
                    raise DiscoveryPayloadError("invalid provider payload")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                remaining = limit + 1 - len(body)
                if remaining <= 0:
                    raise DiscoveryPayloadError("invalid provider payload")
                body.extend(chunk[:remaining])
                if len(body) > limit or len(chunk) > remaining:
                    raise DiscoveryPayloadError("invalid provider payload")
            return _strict_json_bytes(bytes(body))
    except DiscoveryError:
        raise
    except httpx.HTTPError:
        raise DiscoveryProviderError("provider request failed") from None


class AsyncRateLimiter:
    """Lock-protected minimum request interval with injected time and sleep."""

    def __init__(
        self,
        interval: float,
        *,
        monotonic: MonotonicClock,
        sleeper: AsyncSleeper,
    ) -> None:
        self._interval = nonnegative_float(interval, field="min_interval_seconds")
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = asyncio.Lock()
        self._last_request: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            now = self._monotonic()
            if self._last_request is not None:
                delay = self._interval - (now - self._last_request)
                if delay > 0:
                    await self._sleeper(delay)
                    now = self._monotonic()
            self._last_request = now
