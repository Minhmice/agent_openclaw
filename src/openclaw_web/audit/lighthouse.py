"""Strict Lighthouse report parser and bounded subprocess adapter."""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias
from urllib.parse import urlsplit

JsonInput: TypeAlias = bytes | bytearray | str | Mapping[str, object]
SubprocessRunner: TypeAlias = Callable[..., subprocess.CompletedProcess[bytes]]


@dataclass(frozen=True, slots=True)
class LighthouseLimits:
    timeout_seconds: float = 90.0
    max_json_bytes: int = 5_000_000
    max_json_depth: int = 24
    max_json_nodes: int = 100_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")
        if self.timeout_seconds > 600:
            raise ValueError("timeout_seconds exceeds the supported maximum")
        for name in ("max_json_bytes", "max_json_depth", "max_json_nodes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_json_bytes > 50_000_000:
            raise ValueError("max_json_bytes exceeds the supported maximum")
        if self.max_json_depth > 64:
            raise ValueError("max_json_depth exceeds the supported maximum")
        if self.max_json_nodes > 1_000_000:
            raise ValueError("max_json_nodes exceeds the supported maximum")


@dataclass(frozen=True, slots=True)
class LighthouseMetrics:
    status: Literal["complete", "partial"]
    performance_score: int | None
    accessibility_score: int | None
    seo_score: int | None
    best_practices_score: int | None
    lcp_ms: float | None
    cls: float | None
    fcp_ms: float | None
    speed_index_ms: float | None
    tbt_ms: float | None
    unavailable_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LighthouseRunResult:
    status: Literal["complete", "partial", "unavailable"]
    metrics: LighthouseMetrics | None
    reason: str | None = None


def _reject_constant(_value: str) -> object:
    raise ValueError("nonstandard numeric constant")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _shape(value: object, limits: LighthouseLimits) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_json_nodes or depth > limits.max_json_depth:
            raise ValueError("report shape exceeds limits")
        if isinstance(item, Mapping):
            if not all(isinstance(key, str) for key in item):
                raise TypeError("report keys must be strings")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list | tuple):
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and not isinstance(item, str | int | float | bool):
            raise TypeError("unsupported report value")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite report value")


def _load(value: JsonInput, limits: LighthouseLimits) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        document: object = value
        try:
            encoded_size = len(
                json.dumps(
                    value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode("utf-8")
            )
        except (TypeError, ValueError, RecursionError) as error:
            raise ValueError("invalid in-memory report") from error
        if encoded_size > limits.max_json_bytes:
            raise ValueError("report exceeds byte limit")
    else:
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(raw) > limits.max_json_bytes:
            raise ValueError("report exceeds byte limit")
        try:
            document = json.loads(
                raw,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("invalid JSON") from error
    _shape(document, limits)
    if not isinstance(document, Mapping):
        raise TypeError("report root must be an object")
    return document


def _mapping(parent: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be an object")
    return value


def _score(categories: Mapping[str, object], name: str) -> int | None:
    category = categories.get(name)
    if category is None:
        return None
    if not isinstance(category, Mapping):
        raise TypeError("category must be an object")
    value = category.get("score")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError("category score must be finite numeric")
    if not 0 <= float(value) <= 1:
        raise ValueError("category score must be between zero and one")
    return round(float(value) * 100)


def _metric(audits: Mapping[str, object], name: str) -> float | None:
    audit = audits.get(name)
    if audit is None:
        return None
    if not isinstance(audit, Mapping):
        raise TypeError("audit must be an object")
    value = audit.get("numericValue")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError("numericValue must be finite numeric")
    if float(value) < 0:
        raise ValueError("numericValue must be non-negative")
    return float(value)


def parse_lighthouse(
    value: JsonInput,
    *,
    limits: LighthouseLimits | None = None,
) -> LighthouseMetrics:
    """Parse a Lighthouse report while rejecting ambiguous or hostile JSON."""

    bounds = limits or LighthouseLimits()
    try:
        document = _load(value, bounds)
        categories = _mapping(document, "categories")
        audits = _mapping(document, "audits")
        values: dict[str, int | float | None] = {
            "performance_score": _score(categories, "performance"),
            "accessibility_score": _score(categories, "accessibility"),
            "seo_score": _score(categories, "seo"),
            "best_practices_score": _score(categories, "best-practices"),
            "lcp_ms": _metric(audits, "largest-contentful-paint"),
            "cls": _metric(audits, "cumulative-layout-shift"),
            "fcp_ms": _metric(audits, "first-contentful-paint"),
            "speed_index_ms": _metric(audits, "speed-index"),
            "tbt_ms": _metric(audits, "total-blocking-time"),
        }
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise ValueError("invalid Lighthouse report") from error
    unavailable = tuple(sorted(name for name, item in values.items() if item is None))
    return LighthouseMetrics(
        status="partial" if unavailable else "complete",
        performance_score=values["performance_score"],  # type: ignore[arg-type]
        accessibility_score=values["accessibility_score"],  # type: ignore[arg-type]
        seo_score=values["seo_score"],  # type: ignore[arg-type]
        best_practices_score=values["best_practices_score"],  # type: ignore[arg-type]
        lcp_ms=values["lcp_ms"],
        cls=values["cls"],
        fcp_ms=values["fcp_ms"],
        speed_index_ms=values["speed_index_ms"],
        tbt_ms=values["tbt_ms"],
        unavailable_inputs=unavailable,
    )


def _default_run(
    argv: list[str], *, timeout: float, cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        cwd=cwd,
        check=False,
    )


def _read_report(
    path: Path,
    expected: os.stat_result,
    maximum: int,
) -> tuple[bytes | None, Literal["unsafe-report", "report-too-large", "invalid-report"] | None]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, "unsafe-report"
    try:
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_dev != expected.st_dev
            or actual.st_ino != expected.st_ino
            or actual.st_size != expected.st_size
        ):
            return None, "unsafe-report"
        chunks: list[bytes] = []
        total = 0
        while total <= maximum:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - total))
            if not chunk:
                final = os.fstat(descriptor)
                if (
                    final.st_dev != actual.st_dev
                    or final.st_ino != actual.st_ino
                    or final.st_size != actual.st_size
                    or final.st_mtime_ns != actual.st_mtime_ns
                    or final.st_ctime_ns != actual.st_ctime_ns
                ):
                    return None, "unsafe-report"
                return b"".join(chunks), None
            chunks.append(chunk)
            total += len(chunk)
        return None, "report-too-large"
    except OSError:
        return None, "invalid-report"
    finally:
        os.close(descriptor)


class LighthouseRunner:
    """Run a resolved Lighthouse executable into an isolated bounded report file."""

    def __init__(
        self,
        output_root: Path,
        *,
        executable: Path | str | None = None,
        limits: LighthouseLimits | None = None,
        subprocess_runner: SubprocessRunner = _default_run,
    ) -> None:
        self._output_root = Path(os.path.abspath(output_root))
        self._requested_executable = executable
        self._limits = limits or LighthouseLimits()
        self._run = subprocess_runner

    def _prepare_output_root(self) -> bool:
        try:
            self._output_root.mkdir(parents=True, exist_ok=True)
            metadata = self._output_root.lstat()
        except OSError:
            return False
        return stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)

    def _executable(self) -> Path | None:
        requested = self._requested_executable
        located = shutil.which("lighthouse") if requested is None else str(requested)
        if located is None:
            return None
        candidate = Path(located)
        if not candidate.is_absolute():
            resolved = shutil.which(str(candidate))
            if resolved is None:
                return None
            candidate = Path(resolved)
        try:
            metadata = candidate.lstat()
            if os.name == "nt" and stat.S_ISLNK(metadata.st_mode):
                return None
            final_path = candidate.resolve(strict=True)
            final_metadata = final_path.lstat()
        except (OSError, RuntimeError):
            return None
        if stat.S_ISLNK(final_metadata.st_mode) or not stat.S_ISREG(final_metadata.st_mode):
            return None
        if os.name != "nt" and not os.access(final_path, os.X_OK):
            return None
        return final_path

    def run(self, url: str) -> LighthouseRunResult:
        executable = self._executable()
        if executable is None:
            if not self._prepare_output_root():
                return LighthouseRunResult("unavailable", None, "unsafe-output-root")
            return LighthouseRunResult("unavailable", None, "executable-unavailable")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.hostname is None or parts.username:
            return LighthouseRunResult("unavailable", None, "invalid-url")
        try:
            port = parts.port
        except ValueError:
            return LighthouseRunResult("unavailable", None, "invalid-url")
        rendered_host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        authority = rendered_host if port is None else f"{rendered_host}:{port}"
        safe_url = parts._replace(netloc=authority, query="", fragment="").geturl()
        if not self._prepare_output_root():
            return LighthouseRunResult("unavailable", None, "unsafe-output-root")

        work = Path(tempfile.mkdtemp(prefix="lighthouse-", dir=self._output_root))
        report = work / "report.json"
        argv = [
            str(executable),
            safe_url,
            "--quiet",
            "--output=json",
            f"--output-path={report}",
            "--chrome-flags=--headless --disable-gpu",
        ]
        try:
            try:
                completed = self._run(argv, timeout=self._limits.timeout_seconds, cwd=work)
            except subprocess.TimeoutExpired:
                return LighthouseRunResult("unavailable", None, "process-timeout")
            except Exception as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                return LighthouseRunResult("unavailable", None, "process-failed")
            returncode = getattr(completed, "returncode", None)
            if isinstance(returncode, bool) or not isinstance(returncode, int):
                return LighthouseRunResult("unavailable", None, "process-failed")
            if returncode != 0:
                return LighthouseRunResult("unavailable", None, "process-failed")
            try:
                metadata = report.lstat()
            except OSError:
                return LighthouseRunResult("unavailable", None, "report-missing")
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                return LighthouseRunResult("unavailable", None, "unsafe-report")
            if metadata.st_size > self._limits.max_json_bytes:
                return LighthouseRunResult("unavailable", None, "report-too-large")
            raw, read_failure = _read_report(report, metadata, self._limits.max_json_bytes)
            if read_failure is not None or raw is None:
                return LighthouseRunResult("unavailable", None, read_failure or "invalid-report")
            try:
                metrics = parse_lighthouse(raw, limits=self._limits)
            except ValueError:
                return LighthouseRunResult("unavailable", None, "invalid-report")
            return LighthouseRunResult(metrics.status, metrics)
        finally:
            shutil.rmtree(work, ignore_errors=True)
