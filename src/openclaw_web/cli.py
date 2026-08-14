"""Command-line contract for the website discovery and audit workflow."""

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Protocol

import typer

CommandHandler = Callable[[], object]


class ServiceUnavailable(RuntimeError):
    """Raised when a CLI command has no configured runtime service."""

    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        super().__init__(f"Runtime dependency unavailable: {dependency}")


class ServiceRegistry(Protocol):
    """Resolve CLI dependency names to concrete command handlers."""

    def require(self, dependency: str) -> CommandHandler:
        """Return the configured handler or raise ``ServiceUnavailable``."""
        ...


class DefaultServiceRegistry:
    """Small mapping-backed registry with no services configured by default."""

    def __init__(self, services: Mapping[str, CommandHandler] | None = None) -> None:
        self._services = dict(services or {})

    def require(self, dependency: str) -> CommandHandler:
        try:
            return self._services[dependency]
        except KeyError:
            raise ServiceUnavailable(dependency) from None


service_registry: ServiceRegistry = DefaultServiceRegistry()

app = typer.Typer(
    help="Kham pha, danh gia va chuan bi de xuat nang cap website quanh Ha Noi.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _run_service(dependency: str) -> None:
    try:
        handler = service_registry.require(dependency)
        handler()
    except ServiceUnavailable as exc:
        typer.echo(f"Thieu runtime dependency: {exc.dependency}", err=True)
        raise typer.Exit(code=2) from exc


def _emit(payload: Mapping[str, object], *, json_output: bool) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
    else:
        for key, value in payload.items():
            typer.echo(f"{key}: {value}")


@app.command(help="Kiem tra tinh trang cac dependency cua workflow.")
def health(json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted.")) -> None:
    if json_output:
        from openclaw_web.health import HealthService, HealthSettings

        report = HealthService(HealthSettings.from_environment()).check()
        _emit({
            "manual_audit_ready": report.manual_audit_ready,
            "discovery_ready": report.discovery_ready,
            "checks": report.checks,
        }, json_output=True)
        return
    _run_service("health")


@app.command(help="Danh gia mot website va tao ho so bang chung.")
def audit(
    dry_run: bool = typer.Option(False, "--dry-run", help="Chi kiem tra wiring, khong crawl."),
    json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted."),
) -> None:
    if dry_run:
        _emit({"command": "audit", "status": "dry-run", "external_io": False}, json_output=json_output)
        return
    _run_service("audit")


@app.command(help="Kham pha doanh nghiep va website trong thi truong da chon.")
def discover(
    dry_run: bool = typer.Option(False, "--dry-run", help="Chi kiem tra market/geofence, khong goi provider."),
    json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted."),
) -> None:
    if dry_run:
        _emit({"command": "discover", "status": "dry-run", "market": "hanoi-80km", "external_io": False}, json_output=json_output)
        return
    _run_service("discover")


@app.command(help="Tao website brief tu ho so da dat dieu kien.")
def brief() -> None:
    _run_service("brief")


@app.command(help="Kiem tra schema va tinh nhat quan cua artifact.")
def validate() -> None:
    _run_service("validate")


@app.command(help="Ghi nhan ket qua va phan hoi van hanh.")
def feedback() -> None:
    _run_service("feedback")


@app.command(help="Tao bao cao hieu chinh scoring tu phan hoi.")
def calibration() -> None:
    _run_service("calibration")


@app.command(help="Gui artifact da duyet qua delivery outbox.")
def delivery(
    json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted."),
) -> None:
    if json_output:
        from openclaw_web.runtime import drain_delivery_outbox

        result = drain_delivery_outbox()
        _emit(result, json_output=True)
        if result["status"] == "failed":
            raise typer.Exit(code=1)
        return
    _run_service("delivery")


@app.command("legacy-review", help="Gui mot Curie legacy project thanh review card native.")
def legacy_review(
    project_id: str = typer.Option(..., "--project-id", help="Project ID trong workflow root."),
    json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted."),
) -> None:
    from openclaw_web.review.legacy import LegacyReviewError
    from openclaw_web.runtime import run_legacy_review

    try:
        result = run_legacy_review(project_id)
    except (LegacyReviewError, OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Khong the gui legacy review card: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _emit(result, json_output=json_output)


@app.command("cron-run", help="Chay discovery dinh ky voi khoa chong chay trung.")
def cron_run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Khong acquire lock, crawl hoac gui Discord."),
    json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted."),
) -> None:
    if dry_run:
        _emit({"command": "cron-run", "status": "dry-run", "market": "hanoi-80km", "external_io": False}, json_output=json_output)
        return
    if json_output:
        from openclaw_web.runtime import run_daily_discovery

        result = run_daily_discovery()
        _emit(
            {
                "command": "cron-run",
                "exit_code": result.exit_code,
                "market": "hanoi-80km",
                "status": result.status,
            },
            json_output=True,
        )
        if result.exit_code:
            raise typer.Exit(code=result.exit_code)
        return
    _run_service("cron-run")


@app.command(
    "component-action",
    help="Xu ly mot action tu Discord Components v2.",
)
def component_action(
    input_path: Annotated[
        Path | None,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Doc mot JSON envelope tu file; mac dinh doc stdin.",
        ),
    ] = None,
    json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted."),
) -> None:
    if input_path is not None or json_output:
        from openclaw_web.runtime import run_component_action

        if input_path is not None and not sys.stdin.isatty():
            probe = sys.stdin.read(1)
            if probe:
                typer.echo("Chi duoc dung mot trong --input hoac stdin.", err=True)
                raise typer.Exit(code=2)
        try:
            payload = (
                input_path.read_text(encoding="utf-8")
                if input_path is not None
                else sys.stdin.read(65_537)
            )
        except (OSError, UnicodeError) as exc:
            typer.echo("Khong doc duoc component envelope UTF-8.", err=True)
            raise typer.Exit(code=2) from exc
        if len(payload.encode("utf-8")) > 65_536:
            typer.echo("Component envelope qua lon.", err=True)
            raise typer.Exit(code=2)
        try:
            result = run_component_action(payload)
        except (KeyError, RuntimeError, ValueError) as exc:
            typer.echo(f"Khong the xu ly component action: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        _emit(result, json_output=json_output)
        return
    _run_service("component-action")


@app.command(
    "component-callback",
    help="Xu ly callback Discord da xac thuc qua lookup message durable.",
)
def component_callback(
    json_output: bool = typer.Option(False, "--json", help="Xuat JSON redacted."),
) -> None:
    if not json_output:
        _run_service("component-callback")
        return
    from openclaw_web.runtime import run_component_callback

    try:
        payload = sys.stdin.buffer.read(65_537)
    except OSError as exc:
        typer.echo("Khong doc duoc callback envelope UTF-8.", err=True)
        raise typer.Exit(code=2) from exc
    if len(payload) > 65_536:
        typer.echo("Callback envelope qua lon.", err=True)
        raise typer.Exit(code=2)
    try:
        text = payload.decode("utf-8", errors="strict")
        result = run_component_callback(text)
    except UnicodeError as exc:
        typer.echo("Khong doc duoc callback envelope UTF-8.", err=True)
        raise typer.Exit(code=2) from exc
    except (KeyError, RuntimeError, ValueError) as exc:
        typer.echo("Khong the xu ly component callback.", err=True)
        raise typer.Exit(code=2) from exc
    _emit(result, json_output=True)
