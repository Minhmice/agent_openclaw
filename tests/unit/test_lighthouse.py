from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from openclaw_web.audit.builtin import PageAuditObservation
from openclaw_web.audit.lighthouse import (
    LighthouseLimits,
    LighthouseRunner,
    parse_lighthouse,
)
from openclaw_web.audit.service import AuditService
from openclaw_web.crawl.extract import CallToAction, ExtractedPage, Heading


def _document() -> dict[str, object]:
    return {
        "categories": {
            "performance": {"score": 0.42},
            "accessibility": {"score": 0.91},
            "seo": {"score": 0.88},
            "best-practices": {"score": 0.73},
        },
        "audits": {
            "largest-contentful-paint": {"numericValue": 5100},
            "cumulative-layout-shift": {"numericValue": 0.12},
            "first-contentful-paint": {"numericValue": 1800},
            "speed-index": {"numericValue": 2300},
            "total-blocking-time": {"numericValue": 350},
        },
    }


def _page() -> ExtractedPage:
    return ExtractedPage(
        url="https://example.com/",
        title="Example",
        description="Description",
        headings=(Heading(1, "Example"),),
        ctas=(CallToAction("Contact", "contact", "https://example.com/contact"),),
        forms=(),
        links=(),
        evidence=(),
    )


def _executable(tmp_path: Path) -> Path:
    path = tmp_path / ("lighthouse.cmd" if os.name == "nt" else "lighthouse")
    path.write_text("stub", encoding="utf-8")
    path.chmod(0o700)
    return path.resolve()


def test_runner_rejects_non_executable_file(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows executable resolution does not use POSIX mode bits")
    executable = _executable(tmp_path)
    executable.chmod(0o600)

    result = LighthouseRunner(tmp_path / "reports", executable=executable).run(
        "https://example.com/"
    )

    assert result.status == "unavailable"
    assert result.reason == "executable-unavailable"


def test_lighthouse_parser_normalizes_scores_and_core_metrics() -> None:
    result = parse_lighthouse(_document())

    assert result.performance_score == 42
    assert result.accessibility_score == 91
    assert result.seo_score == 88
    assert result.best_practices_score == 73
    assert result.lcp_ms == 5100
    assert result.cls == 0.12
    assert result.fcp_ms == 1800
    assert result.speed_index_ms == 2300
    assert result.tbt_ms == 350
    assert result.status == "complete"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"categories":{"performance":{"score":0.4},"performance":{"score":0.9}}}',
        b'{"categories":{"performance":{"score":NaN}}}',
        b'{"categories":{"performance":{"score":2}}}',
        b'{"categories":[]}',
        b"not-json",
    ],
)
def test_lighthouse_parser_rejects_ambiguous_or_malformed_documents(payload: bytes) -> None:
    with pytest.raises(ValueError, match="invalid Lighthouse report"):
        parse_lighthouse(payload)


def test_lighthouse_parser_rejects_deep_and_oversized_documents() -> None:
    deep: object = 1
    for _ in range(40):
        deep = {"x": deep}

    with pytest.raises(ValueError, match="invalid Lighthouse report"):
        parse_lighthouse(cast(dict[str, object], deep))
    with pytest.raises(ValueError, match="invalid Lighthouse report"):
        parse_lighthouse(b"{}" * 1_000, limits=LighthouseLimits(max_json_bytes=20))
    with pytest.raises(ValueError, match="invalid Lighthouse report"):
        parse_lighthouse(
            {"categories": {}, "audits": {}, "padding": "x" * 100},
            limits=LighthouseLimits(max_json_bytes=20),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": True},
        {"max_json_bytes": 1.5},
        {"max_json_depth": True},
        {"max_json_nodes": 2.5},
    ],
)
def test_lighthouse_limits_reject_non_strict_numeric_values(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        LighthouseLimits(**changes)  # type: ignore[arg-type]


def test_runner_uses_argv_isolated_file_and_cleans_up(tmp_path: Path) -> None:
    executable = _executable(tmp_path)
    root = tmp_path / "reports"
    observed: dict[str, object] = {}

    def fake_run(
        argv: list[str], *, timeout: float, cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        observed.update(argv=argv, timeout=timeout, cwd=cwd)
        output_arg = next(item for item in argv if item.startswith("--output-path="))
        output = Path(output_arg.split("=", 1)[1])
        output.write_text(json.dumps(_document()), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=b"secret", stderr=b"password=secret")

    result = LighthouseRunner(root, executable=executable, subprocess_runner=fake_run).run(
        "https://example.com/path?q=secret"
    )

    assert result.status == "complete"
    assert result.metrics is not None and result.metrics.lcp_ms == 5100
    assert isinstance(observed["argv"], list)
    assert observed["argv"][0] == str(executable)
    assert all("no-sandbox" not in item for item in observed["argv"])
    observed_cwd = observed["cwd"]
    assert isinstance(observed_cwd, Path)
    assert observed_cwd.parent == root.resolve()
    assert list(root.iterdir()) == []
    assert "secret" not in (result.reason or "")


@pytest.mark.parametrize("mode", ["missing", "failure", "timeout", "invalid", "oversized"])
def test_runner_returns_sanitized_structured_status_and_cleans_up(
    tmp_path: Path, mode: str
) -> None:
    root = tmp_path / "reports"
    executable = tmp_path / "missing" if mode == "missing" else _executable(tmp_path)

    def fake_run(
        argv: list[str], *, timeout: float, cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout, cwd
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 1, output=b"token=secret")
        output = Path(
            next(item for item in argv if item.startswith("--output-path=")).split("=", 1)[1]
        )
        output.write_bytes(b"not-json" if mode == "invalid" else b"x" * 200)
        return subprocess.CompletedProcess(argv, 2 if mode == "failure" else 0, b"", b"secret")

    result = LighthouseRunner(
        root,
        executable=executable,
        limits=LighthouseLimits(max_json_bytes=100),
        subprocess_runner=fake_run,
    ).run("https://example.com/")

    assert result.status == ("unavailable" if mode == "missing" else "failed")
    assert result.metrics is None
    assert result.reason in {
        "executable-unavailable",
        "process-failed",
        "process-timeout",
        "invalid-report",
        "report-too-large",
    }
    assert list(root.iterdir()) == []


def test_runner_rejects_symlink_output(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("creating symlinks is privilege-dependent on Windows")
    executable = _executable(tmp_path)
    target = tmp_path / "outside.json"
    target.write_text(json.dumps(_document()), encoding="utf-8")

    def fake_run(
        argv: list[str], *, timeout: float, cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout, cwd
        output = Path(
            next(item for item in argv if item.startswith("--output-path=")).split("=", 1)[1]
        )
        output.symlink_to(target)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    result = LighthouseRunner(
        tmp_path / "reports", executable=executable, subprocess_runner=fake_run
    ).run("https://example.com/")

    assert result.status == "failed"
    assert result.reason == "unsafe-report"


def test_runner_rejects_symlink_output_root(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("creating symlinks is privilege-dependent on Windows")
    executable = _executable(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "reports"
    root.symlink_to(outside, target_is_directory=True)

    result = LighthouseRunner(root, executable=executable).run("https://example.com/")

    assert result.status == "failed"
    assert result.reason == "unsafe-output-root"
    assert list(outside.iterdir()) == []


def test_runner_contains_non_directory_output_root(tmp_path: Path) -> None:
    executable = _executable(tmp_path)
    root = tmp_path / "reports"
    root.write_text("occupied", encoding="utf-8")

    result = LighthouseRunner(root, executable=executable).run("https://example.com/")

    assert result.status == "failed"
    assert result.reason == "unsafe-output-root"


def test_runner_rejects_report_changed_during_descriptor_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path)
    report_bytes = json.dumps(_document()).encode()
    report: Path | None = None

    def fake_run(
        argv: list[str], *, timeout: float, cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal report
        del timeout, cwd
        report = Path(
            next(item for item in argv if item.startswith("--output-path=")).split("=", 1)[1]
        )
        report.write_bytes(report_bytes)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    real_read = os.read
    changed = False

    def racing_read(descriptor: int, maximum: int) -> bytes:
        nonlocal changed
        if not changed:
            assert report is not None
            report.write_bytes(report_bytes + b" ")
            changed = True
        return real_read(descriptor, maximum)

    monkeypatch.setattr(os, "read", racing_read)
    result = LighthouseRunner(
        tmp_path / "reports", executable=executable, subprocess_runner=fake_run
    ).run("https://example.com/")

    assert result.status == "failed"
    assert result.reason == "unsafe-report"


def test_runner_contains_unexpected_adapter_failure(tmp_path: Path) -> None:
    executable = _executable(tmp_path)

    def fake_run(
        argv: list[str], *, timeout: float, cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        del argv, timeout, cwd
        raise RuntimeError("password=secret")

    result = LighthouseRunner(
        tmp_path / "reports", executable=executable, subprocess_runner=fake_run
    ).run("https://example.com/")

    assert result.status == "failed"
    assert result.reason == "process-failed"
    assert list((tmp_path / "reports").iterdir()) == []


def test_runner_contains_malformed_adapter_result(tmp_path: Path) -> None:
    executable = _executable(tmp_path)

    def fake_run(argv: list[str], *, timeout: float, cwd: Path) -> object:
        del argv, timeout, cwd
        return object()

    result = LighthouseRunner(
        tmp_path / "reports",
        executable=executable,
        subprocess_runner=fake_run,  # type: ignore[arg-type]
    ).run("https://example.com/")

    assert result.status == "failed"
    assert result.reason == "process-failed"
    assert list((tmp_path / "reports").iterdir()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": 601},
        {"max_json_bytes": 50_000_001},
        {"max_json_depth": 65},
        {"max_json_nodes": 1_000_001},
    ],
)
def test_lighthouse_limits_reject_values_that_disable_resource_bounds(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        LighthouseLimits(**changes)  # type: ignore[arg-type]


def test_audit_service_is_partial_when_lighthouse_is_unavailable(tmp_path: Path) -> None:
    lighthouse = LighthouseRunner(tmp_path / "reports", executable=tmp_path / "missing")

    result = AuditService(lighthouse).audit(PageAuditObservation(page=_page()))

    assert result.status == "partial"
    assert result.lighthouse.status == "unavailable"
    assert result.findings == result.builtin.findings
