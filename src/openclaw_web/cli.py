"""Command-line contract for the website discovery and audit workflow."""

from collections.abc import Callable, Mapping
from typing import Protocol

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


@app.command(help="Kiem tra tinh trang cac dependency cua workflow.")
def health() -> None:
    _run_service("health")


@app.command(help="Danh gia mot website va tao ho so bang chung.")
def audit() -> None:
    _run_service("audit")


@app.command(help="Kham pha doanh nghiep va website trong thi truong da chon.")
def discover() -> None:
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
def delivery() -> None:
    _run_service("delivery")


@app.command("cron-run", help="Chay discovery dinh ky voi khoa chong chay trung.")
def cron_run() -> None:
    _run_service("cron-run")


@app.command(
    "component-action",
    help="Xu ly mot action tu Discord Components v2.",
)
def component_action() -> None:
    _run_service("component-action")
