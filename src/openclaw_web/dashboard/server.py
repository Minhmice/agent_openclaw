"""Standard-library, loopback-only HTTP server for the live dashboard."""

from __future__ import annotations

import json
import mimetypes
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from openclaw_web.db import Repository, connect

from .actions import DashboardActionError, DashboardActionService, DashboardFeedbackRecorder
from .auth import TokenAuthenticator
from .coordinator import WorkflowDashboardCoordinator
from .projection import project_dashboard
from .read_repository import DashboardDataUnavailable, DashboardReadRepository

MAX_BODY_BYTES = 65_536
MAX_ASSET_BYTES = 8 * 1024 * 1024
STATIC_FILES = frozenset({"index.html", "app.js", "styles.css"})
ALLOWLISTED_ASSETS = frozenset(
    {"screenshot.png", "screenshot.jpg", "screenshot.jpeg", "screenshot.webp"}
)


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class DashboardServer:
    """Serve dashboard reads and authenticated actions from one origin."""

    def __init__(
        self,
        *,
        db_path: Path,
        host: str = "127.0.0.1",
        port: int = 18_080,
        dashboard_root: Path | None = None,
        asset_root: Path | None = None,
        authenticator: TokenAuthenticator | None = None,
        coordinator: object | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("dashboard server must bind to loopback")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
            raise ValueError("port must be between 0 and 65535")
        self.db_path = Path(db_path)
        self.host = host
        self.dashboard_root = (dashboard_root or Path(__file__).resolve().parents[3] / "dashboard").resolve()
        self.asset_root = (asset_root or self.db_path.parent / "artifacts").resolve()
        self.read_repository = DashboardReadRepository(self.db_path, asset_root=self.asset_root)
        self.authenticator = authenticator or TokenAuthenticator.from_environment()
        self._write_connection: sqlite3.Connection | None = None
        self._action_service: DashboardActionService | None = None
        if self.db_path.is_file():
            self._write_connection = connect(self.db_path, check_same_thread=False)
            write_repository = Repository(self._write_connection)
            effective_coordinator = coordinator
            adapter_path = getattr(coordinator, "coordinator_path", None)
            if (
                coordinator is not None
                and not hasattr(coordinator, "apply")
                and hasattr(coordinator, "action")
                and hasattr(coordinator, "execute")
                and isinstance(adapter_path, Path)
            ):
                effective_coordinator = WorkflowDashboardCoordinator(
                    command_adapter=coordinator,
                    repository=write_repository,
                    read_repository=self.read_repository,
                    workflow_root=adapter_path.resolve().parent,
                )
            self._action_service = DashboardActionService(
                repository=write_repository,
                read_repository=self.read_repository,
                authenticator=self.authenticator,
                coordinator=effective_coordinator,
                feedback_sink=DashboardFeedbackRecorder(write_repository, self.read_repository),
            )
        self.httpd = _DashboardHTTPServer((host, port), self._handler_factory())
        self.httpd.dashboard_owner = self  # type: ignore[attr-defined]

    @property
    def server_address(self) -> tuple[str, int]:
        address = self.httpd.server_address
        return str(address[0]), int(address[1])

    def _handler_factory(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "OpenClawWebDashboard/1"
            sys_version = ""

            def log_message(self, format: str, *args: object) -> None:
                # Deliberately omit headers, query values and exception details;
                # especially never log Authorization or request bodies.
                return

            def do_GET(self) -> None:
                owner._handle(self, "GET")

            def do_HEAD(self) -> None:
                owner._handle(self, "HEAD")

            def do_POST(self) -> None:
                owner._handle(self, "POST")

        return Handler

    @staticmethod
    def _json_bytes(payload: object) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    def _send(self, handler: BaseHTTPRequestHandler, status: int, payload: object, *, head: bool = False) -> None:
        body = self._json_bytes(payload)
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(body)))
        if handler.close_connection:
            handler.send_header("Connection", "close")
        handler.end_headers()
        if not head:
            handler.wfile.write(body)

    def _send_bytes(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        body: bytes,
        content_type: str,
        *,
        head: bool = False,
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(body)))
        if handler.close_connection:
            handler.send_header("Connection", "close")
        handler.end_headers()
        if not head:
            handler.wfile.write(body)

    @staticmethod
    def _error_payload(code: str, message: str) -> dict[str, str]:
        return {"error": code, "message_vi": message}

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parsed = urlsplit(handler.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/v1/"):
                self._handle_api(handler, method, path, parsed.query)
                return
            if method == "POST":
                self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy endpoint."))
                return
            self._handle_static(handler, path, head=method == "HEAD")
        except DashboardActionError as error:
            self._send(handler, error.status_code, self._error_payload(error.code, error.message_vi), head=method == "HEAD")
        except DashboardDataUnavailable:
            self._send(handler, HTTPStatus.SERVICE_UNAVAILABLE, self._error_payload("unavailable", "Dashboard data chưa sẵn sàng."), head=method == "HEAD")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001 - HTTP boundary never exposes internals
            self._send(handler, HTTPStatus.INTERNAL_SERVER_ERROR, self._error_payload("internal_error", "Dashboard gặp lỗi nội bộ."), head=method == "HEAD")

    def _handle_api(self, handler: BaseHTTPRequestHandler, method: str, path: str, query: str) -> None:
        head = method == "HEAD"
        if method not in {"GET", "HEAD", "POST"}:
            self._send(handler, HTTPStatus.METHOD_NOT_ALLOWED, self._error_payload("method_not_allowed", "Method không được hỗ trợ."), head=head)
            return
        if path == "/api/v1/health":
            if method not in {"GET", "HEAD"}:
                self._send(
                    handler,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    self._error_payload("method_not_allowed", "Method không được hỗ trợ."),
                    head=head,
                )
                return
            schema_ready = self.db_path.is_file() and self.read_repository.schema_ready()
            self._send(
                handler,
                HTTPStatus.OK,
                {
                    "status": "ok" if schema_ready else "unavailable",
                    "bind": self.host,
                    "port": self.server_address[1],
                    "database": "ready" if schema_ready else "unavailable",
                },
                head=head,
            )
            return
        if method == "POST" and path == "/api/v1/actions":
            self._handle_action(handler, head=head)
            return
        if method != "GET" and method != "HEAD":
            self._send(handler, HTTPStatus.METHOD_NOT_ALLOWED, self._error_payload("method_not_allowed", "Method không được hỗ trợ."), head=head)
            return
        if path == "/api/v1/dashboard":
            snapshot = project_dashboard(self.read_repository)
            self._send(handler, HTTPStatus.OK, snapshot.model_dump(mode="json"), head=head)
            return
        if path == "/api/v1/runs":
            limit = self._query_limit(query)
            self._send(handler, HTTPStatus.OK, [run.model_dump(mode="json") for run in self.read_repository.list_runs(limit=limit)], head=head)
            return
        if path.startswith("/api/v1/runs/"):
            run_id = path.removeprefix("/api/v1/runs/")
            run = self.read_repository.get_run(run_id)
            if run is None:
                self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy run."), head=head)
            else:
                self._send(handler, HTTPStatus.OK, run.model_dump(mode="json"), head=head)
            return
        if path == "/api/v1/leads":
            params = parse_qs(query, keep_blank_values=False)
            state = params.get("state", [None])[0]
            limit = self._query_limit(query)
            self._send(handler, HTTPStatus.OK, [lead.model_dump(mode="json") for lead in self.read_repository.list_leads(state=state, limit=limit)], head=head)
            return
        if path.startswith("/api/v1/leads/"):
            target_id = path.removeprefix("/api/v1/leads/")
            lead = self.read_repository.get_lead(target_id)
            if lead is None:
                self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy lead."), head=head)
            else:
                self._send(handler, HTTPStatus.OK, lead.model_dump(mode="json"), head=head)
            return
        if path == "/api/v1/events":
            limit = self._query_limit(query)
            self._send(handler, HTTPStatus.OK, [event.model_dump(mode="json") for event in self.read_repository.list_events(limit=limit)], head=head)
            return
        if path.startswith("/api/v1/assets/"):
            self._handle_asset(handler, path, head=head)
            return
        self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy endpoint."), head=head)

    @staticmethod
    def _query_limit(query: str) -> int:
        raw = parse_qs(query, keep_blank_values=False).get("limit", ["50"])[0]
        try:
            value = int(raw)
        except (TypeError, ValueError) as error:
            raise DashboardActionError("invalid_query", "Limit không hợp lệ.", 400) from error
        if value < 1 or value > 100:
            raise DashboardActionError("invalid_query", "Limit phải từ 1 đến 100.", 400)
        return value

    def _handle_action(self, handler: BaseHTTPRequestHandler, *, head: bool) -> None:
        authorization = handler.headers.get("Authorization")
        if self.authenticator.authenticate(authorization) is None:
            # The request body is intentionally not read before authentication.
            # Close this request explicitly so an unauthenticated body cannot be
            # parsed as a second HTTP request on the same connection.
            self._reject_body_and_close(handler)
            raise DashboardActionError("unauthorized", "Bearer token không hợp lệ.", 401)
        if self._action_service is None:
            self._send(handler, HTTPStatus.SERVICE_UNAVAILABLE, self._error_payload("unavailable", "Action service chưa sẵn sàng."), head=head)
            return
        raw_length = handler.headers.get("Content-Length")
        try:
            length = int(raw_length or "-1")
        except ValueError as error:
            raise DashboardActionError("invalid_body", "Request body không hợp lệ.", 400) from error
        if length < 0 or length > MAX_BODY_BYTES:
            # Reject by header before allocating/reading an unbounded body and
            # make the connection lifecycle explicit for clients on Windows.
            self._reject_body_and_close(handler, length=length)
            raise DashboardActionError("body_too_large", "Request body quá lớn.", 413)
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise DashboardActionError("invalid_body", "Request body không đầy đủ.", 400)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise DashboardActionError("invalid_body", "Request body phải là JSON UTF-8.", 400) from error
        if not isinstance(payload, dict):
            raise DashboardActionError("invalid_body", "Request body phải là object.", 400)
        response = self._action_service.execute(payload, authorization)
        self._send(handler, HTTPStatus.OK, response.model_dump(mode="json"), head=head)

    @staticmethod
    def _reject_body_and_close(
        handler: BaseHTTPRequestHandler,
        *,
        length: int | None = None,
    ) -> None:
        """Drain only a small declared body before closing an early rejection.

        Leaving a small request body unread can cause Windows peers to observe
        a connection abort instead of the intended 4xx response.  Never drain
        an unbounded body: the hard limit is also a slowloris protection.
        """

        handler.close_connection = True
        if length is None:
            raw_length = handler.headers.get("Content-Length")
            try:
                length = int(raw_length or "-1")
            except ValueError:
                return
        if not 0 <= length <= MAX_BODY_BYTES + 1:
            return
        remaining = length
        while remaining:
            chunk = handler.rfile.read(min(8192, remaining))
            if not chunk:
                return
            remaining -= len(chunk)

    def _handle_asset(self, handler: BaseHTTPRequestHandler, path: str, *, head: bool) -> None:
        parts = tuple(item for item in path.split("/") if item)
        if len(parts) != 5 or parts[0:3] != ("api", "v1", "assets"):
            self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy asset."), head=head)
            return
        candidate_id, asset_name = parts[3], parts[4]
        if (
            not candidate_id
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in candidate_id)
            or asset_name not in ALLOWLISTED_ASSETS
        ):
            self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy asset."), head=head)
            return
        candidate_dir = self.asset_root / candidate_id
        asset = candidate_dir / asset_name
        if candidate_dir.is_symlink() or asset.is_symlink() or not asset.is_file():
            self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy asset."), head=head)
            return
        try:
            root = self.asset_root.resolve(strict=True)
            resolved = asset.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.stat().st_size > MAX_ASSET_BYTES:
                raise DashboardActionError("asset_too_large", "Asset quá lớn.", 413)
            body = resolved.read_bytes()
        except DashboardActionError:
            raise
        except (OSError, ValueError) as error:
            raise DashboardActionError("not_found", "Không tìm thấy asset.", 404) from error
        content_type = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
        self._send_bytes(handler, HTTPStatus.OK, body, content_type, head=head)

    def _handle_static(self, handler: BaseHTTPRequestHandler, path: str, *, head: bool) -> None:
        name = "index.html" if path in {"", "/"} else path.removeprefix("/")
        if name not in STATIC_FILES:
            self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy tài nguyên."), head=head)
            return
        root = self.dashboard_root
        file_path = root / name
        if root.is_symlink() or file_path.is_symlink() or not file_path.is_file():
            self._send(handler, HTTPStatus.NOT_FOUND, self._error_payload("not_found", "Không tìm thấy tài nguyên."), head=head)
            return
        try:
            resolved = file_path.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
            body = resolved.read_bytes()
        except (OSError, ValueError) as error:
            raise DashboardActionError("not_found", "Không tìm thấy tài nguyên.", 404) from error
        content_type = mimetypes.guess_type(name)[0] or "text/plain"
        self._send_bytes(handler, HTTPStatus.OK, body, content_type, head=head)

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        self.httpd.shutdown()

    def server_close(self) -> None:
        self.httpd.server_close()
        if self._write_connection is not None:
            self._write_connection.close()
            self._write_connection = None


def create_dashboard_server(**kwargs: Any) -> DashboardServer:
    """Factory kept small for CLI and test embedding."""

    return DashboardServer(**kwargs)


__all__ = ["DashboardServer", "create_dashboard_server"]
