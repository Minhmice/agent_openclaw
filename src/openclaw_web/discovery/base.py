"""Shared contracts, sanitised failures, and deterministic provider throttling."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from openclaw_web.models import CandidateSeed
from openclaw_web.settings import MarketConfig

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
AsyncSleeper = Callable[[float], Awaitable[None]]


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
        self, market: MarketConfig, cohort: str, *, limit: int
    ) -> Sequence[CandidateSeed]: ...

    def readiness(self) -> str: ...


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
