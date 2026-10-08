#!/usr/bin/env python3
"""Read 9Router Codex quota and optionally send a compact Discord report."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import html as html_lib
import json
import math
import os
import random
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Self, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_BASE_URL = "http://192.168.0.136:20128"
DEFAULT_QUOTA_PATH = "/dashboard/quota"
DEFAULT_API_URL = (
    f"{DEFAULT_BASE_URL}/api/providers/client"
    "?page=1&pageSize=100&accountStatus=all&sort=priority&provider=codex"
)
DEFAULT_CHANNEL = "1533643473486348458"
DEFAULT_TIMEZONE = "Asia/Bangkok"
DEFAULT_AUTHOR = "Codex Coach"
DEFAULT_MODEL_TIMEOUT = 8.0
DEFAULT_HTTP_TIMEOUT = 10.0
DEFAULT_INTERVAL = dt.timedelta(hours=3)
WORKFLOW_ROOT = Path(os.environ.get("OPENCLAW_WORKFLOW_ROOT", "/home/minhmice/.openclaw/workflow"))
DEFAULT_STATE_PATH = WORKFLOW_ROOT / "codex-quota-state.json"
DEFAULT_QUOTES_PATH = Path(__file__).with_name("quotes.json")


class QuotaError(RuntimeError):
    """A safe, non-secret collector error."""

    def __init__(self, message: str, code: str = "collector_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QuotaSnapshot:
    total_remaining_percent: int
    valid_accounts: int
    source: str
    observed_at: str


@dataclass(frozen=True)
class Quote:
    quote: str
    author: str = DEFAULT_AUTHOR


@dataclass(frozen=True)
class FailureUpdate:
    should_alert: bool


@dataclass(frozen=True)
class HttpResponse:
    status: int
    content_type: str
    body: bytes


def parse_interval(value: str) -> dt.timedelta:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([hm])", value.strip().lower())
    if not match:
        raise ValueError("interval must use a positive value followed by h or m")
    amount = float(match.group(1))
    if amount <= 0:
        raise ValueError("interval must be positive")
    seconds = amount * (3600 if match.group(2) == "h" else 60)
    return dt.timedelta(seconds=seconds)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_datetime(value: str | dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def _normal_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if not match:
            return None
        number = float(match.group(0))
    else:
        return None
    return number if math.isfinite(number) else None


def _clamp_percent(value: float) -> int:
    return max(0, min(100, round(value)))


def display_timezone() -> dt.tzinfo:
    try:
        return ZoneInfo(os.environ.get("QUOTA_TIMEZONE", DEFAULT_TIMEZONE))
    except ZoneInfoNotFoundError:
        return dt.timezone(dt.timedelta(hours=7))


def _provider_hint(record: Mapping[str, Any]) -> str | None:
    marker_keys = {
        "provider",
        "model",
        "source",
        "type",
        "service",
        "kind",
        "name",
    }
    values = [
        str(value)
        for key, value in record.items()
        if _normal_key(key) in marker_keys and isinstance(value, (str, int, float))
    ]
    return " ".join(values).lower() if values else None


def _is_provider_match(hint: str | None, provider: str) -> bool:
    if not hint:
        return True
    return provider.lower() in hint


def _value_for_keys(record: Mapping[str, Any], keys: set[str]) -> object | None:
    for key, value in record.items():
        if _normal_key(key) in keys:
            return cast(object, value)
    return None


def _remaining_from_record(record: Mapping[str, Any]) -> int | None:
    direct_keys = {
        "remaining_percent",
        "remaining_percentage",
        "remaining_pct",
        "remaining",
        "percent",
        "percentage",
        "pct",
    }
    for key, value in record.items():
        if _normal_key(key) not in direct_keys:
            continue
        number = _numeric(value)
        if number is None:
            continue
        if number <= 1 and _normal_key(key) in {"remaining", "remaining_percent"}:
            number *= 100
        return _clamp_percent(number)

    used = _numeric(_value_for_keys(record, {"used", "used_count", "consumed", "current"}))
    limit = _numeric(_value_for_keys(record, {"limit", "total", "max", "capacity", "quota"}))
    if used is not None and limit is not None and limit > 0:
        return _clamp_percent(100 - (used / limit * 100))

    for key, value in record.items():
        if _normal_key(key) in {"usage", "session", "quota", "limits"} and isinstance(value, Mapping):
            nested = _remaining_from_record(value)
            if nested is not None:
                return nested
    return None


def _iter_percentages(node: object, provider: str, inherited_hint: str | None = None) -> Iterable[int]:
    if isinstance(node, Mapping):
        own_hint = _provider_hint(node) or inherited_hint
        direct = _remaining_from_record(node)
        if direct is not None and _is_provider_match(own_hint, provider):
            yield direct
            return
        for child in node.values():
            yield from _iter_percentages(child, provider, own_hint)
    elif isinstance(node, list):
        for child in node:
            yield from _iter_percentages(child, provider, inherited_hint)


def _snapshot(percentages: Sequence[int], source: str, observed_at: str) -> QuotaSnapshot:
    if not percentages:
        raise QuotaError("no valid quota records", "empty_payload")
    return QuotaSnapshot(
        total_remaining_percent=sum(percentages),
        valid_accounts=len(percentages),
        source=source,
        observed_at=observed_at,
    )


def parse_quota_payload(
    payload: object,
    observed_at: str | None = None,
    provider: str = "codex",
) -> QuotaSnapshot:
    if not isinstance(payload, (Mapping, list)):
        raise QuotaError("quota payload is not an object or list", "invalid_payload")
    percentages = list(_iter_percentages(payload, provider))
    return _snapshot(percentages, "api", observed_at or iso_now())


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def parse_html_quota(
    document: str,
    observed_at: str | None = None,
    provider: str = "codex",
) -> QuotaSnapshot:
    parser = _VisibleTextParser()
    try:
        parser.feed(document)
        parser.close()
        visible = " ".join(parser.parts)
    except Exception as error:
        raise QuotaError("quota HTML could not be parsed", "html_parse_error") from error
    visible = re.sub(r"\s+", " ", html_lib.unescape(visible)).strip()
    if provider.lower() not in visible.lower():
        raise QuotaError("quota HTML does not contain the expected provider", "provider_not_found")
    pattern = re.compile(
        r"(?P<used>\d+(?:\.\d+)?)\s*/\s*(?P<limit>\d+(?:\.\d+)?)\s*"
        r"(?P<remaining>\d+(?:\.\d+)?)\s*%",
        re.IGNORECASE,
    )
    percentages: list[int] = []
    for match in pattern.finditer(visible):
        used = float(match.group("used"))
        limit = float(match.group("limit"))
        explicit_remaining = float(match.group("remaining"))
        if limit <= 0:
            continue
        computed = _clamp_percent(100 - (used / limit * 100))
        explicit = _clamp_percent(explicit_remaining)
        percentages.append(explicit if abs(explicit - computed) <= 1 else computed)
    return _snapshot(percentages, "html", observed_at or iso_now())


def _join_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def fetch_http(
    url: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    auth_token: str | None = None,
    auth_cookie: str | None = None,
) -> HttpResponse:
    headers = {"Accept": "application/json, text/html;q=0.9"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if auth_cookie:
        if "\r" in auth_cookie or "\n" in auth_cookie:
            raise QuotaError("quota authentication header is invalid", "auth_invalid")
        headers["Cookie"] = auth_cookie
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(1_048_577)
            if len(body) > 1_048_576:
                raise QuotaError("quota response is too large", "response_too_large")
            return HttpResponse(
                status=int(response.status),
                content_type=response.headers.get("Content-Type", ""),
                body=body,
            )
    except HTTPError as error:
        if error.code in {401, 403}:
            raise QuotaError("quota authentication failed", "auth_required") from error
        raise QuotaError(f"quota HTTP status {error.code}", f"http_{error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise QuotaError("quota endpoint is unreachable", "connection_error") from error


class QuotaCollector:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        quota_path: str = DEFAULT_QUOTA_PATH,
        api_url: str | None = None,
        provider: str = "codex",
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        auth_token: str | None = None,
        auth_cookie: str | None = None,
        fetcher: Callable[..., HttpResponse] = fetch_http,
    ) -> None:
        self.page_url = _join_url(base_url, quota_path)
        self.api_url = api_url
        self.provider = provider
        self.timeout = timeout
        self.auth_token = auth_token
        self.auth_cookie = auth_cookie
        self.fetcher = fetcher

    def _fetch(self, url: str) -> HttpResponse:
        try:
            response = self.fetcher(
                url,
                timeout=self.timeout,
                auth_token=self.auth_token,
                auth_cookie=self.auth_cookie,
            )
        except TypeError:
            response = self.fetcher(url)
        if response.status >= 400:
            code = "auth_required" if response.status in {401, 403} else f"http_{response.status}"
            raise QuotaError(f"quota HTTP status {response.status}", code)
        return response

    def _collect_connection_usage(self, payload: Mapping[str, Any], observed_at: str) -> QuotaSnapshot:
        connections = payload.get("connections")
        if not isinstance(connections, list):
            raise QuotaError("quota connection list is invalid", "connection_list_invalid")

        connection_ids: list[str] = []
        for connection in connections:
            if not isinstance(connection, Mapping):
                continue
            if not _is_provider_match(_provider_hint(connection), self.provider):
                continue
            connection_id = _value_for_keys(connection, {"id", "connection_id", "connectionid"})
            if connection_id is not None and str(connection_id).strip():
                connection_ids.append(str(connection_id).strip())
        if not connection_ids:
            raise QuotaError("no valid Codex connections", "connection_list_empty")

        total_percent = 0
        valid_accounts = 0
        usage_path = "/api/usage/{}"
        for connection_id in connection_ids:
            usage_url = urljoin(self.page_url, usage_path.format(quote(connection_id, safe="")))
            try:
                response = self._fetch(usage_url)
            except QuotaError:
                continue
            text = response.body.decode("utf-8", errors="replace")
            try:
                usage_payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            try:
                usage_snapshot = parse_quota_payload(usage_payload, observed_at, self.provider)
            except QuotaError:
                continue
            total_percent += usage_snapshot.total_remaining_percent
            valid_accounts += usage_snapshot.valid_accounts

        if valid_accounts == 0:
            raise QuotaError("no account quotas were returned", "account_quota_error")
        return QuotaSnapshot(total_percent, valid_accounts, "api", observed_at)

    def collect(self, observed_at: str | None = None) -> QuotaSnapshot:
        timestamp = observed_at or iso_now()
        primary_url = self.api_url or self.page_url
        primary_error: QuotaError | None = None
        try:
            response = self._fetch(primary_url)
            text = response.body.decode("utf-8", errors="replace")
            is_json = "json" in response.content_type.lower() or text.lstrip().startswith(("{", "["))
            if is_json:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    primary_error = QuotaError("quota JSON could not be parsed", "json_parse_error")
                else:
                    if isinstance(payload, Mapping) and "connections" in payload:
                        return self._collect_connection_usage(payload, timestamp)
                    try:
                        return parse_quota_payload(payload, timestamp, self.provider)
                    except QuotaError as error:
                        primary_error = error
            else:
                return parse_html_quota(text, timestamp, self.provider)
        except QuotaError as error:
            if error.code.startswith(("account_", "connection_list_")):
                raise
            primary_error = error

        if self.api_url and self.page_url != self.api_url:
            try:
                response = self._fetch(self.page_url)
                return parse_html_quota(response.body.decode("utf-8", errors="replace"), timestamp, self.provider)
            except QuotaError as fallback_error:
                raise fallback_error from primary_error
        raise primary_error or QuotaError("quota collection failed")


def reminder_for(total_percent: int) -> str:
    if total_percent < 100:
        return (
            "Quota đang dưới 100%: ưu tiên chốt việc hiện tại, tránh mở task mới "
            "và tập trung hoàn thành một kết quả cụ thể."
        )
    return "Chọn một việc quan trọng, đặt timer 25 phút, làm đến khi có kết quả cụ thể."


def format_message(
    total_percent: int,
    reminder: str,
    quote: str,
    author: str,
    next_run: dt.datetime,
) -> str:
    local_time = next_run.astimezone(display_timezone())
    next_text = local_time.strftime("%H:%M %d/%m/%Y")
    return (
        "**CODEX QUOTA CHECK**\n\n"
        f"Còn **{total_percent}%** để tiếp tục làm việc.\n\n"
        f"{reminder}\n\n"
        f'> "{quote}"\n'
        f"> — {author}\n\n"
        f"**Lần check tiếp theo:** {next_text}"
    )


def format_technical_alert(next_run: dt.datetime) -> str:
    local_time = next_run.astimezone(display_timezone())
    next_text = local_time.strftime("%H:%M %d/%m/%Y")
    return (
        "**CODEX QUOTA CHECK — TECHNICAL ALERT**\n\n"
        "Không đọc được quota 9Router trong 2 lần kiểm tra liên tiếp.\n\n"
        f"**Lần check tiếp theo:** {next_text}"
    )


def advance_schedule(
    state: dict[str, Any],
    now: dt.datetime,
    interval: dt.timedelta = DEFAULT_INTERVAL,
) -> dt.datetime:
    current = parse_datetime(now)
    activated_value = state.get("activated_at")
    next_value = state.get("next_run_at")
    if not activated_value or not next_value:
        state["activated_at"] = current.isoformat()
        next_run = current + interval
    else:
        try:
            next_run = parse_datetime(str(next_value))
        except ValueError:
            state["activated_at"] = current.isoformat()
            next_run = current + interval
        while next_run <= current:
            next_run += interval
    state["next_run_at"] = next_run.isoformat()
    return next_run


def record_failure(
    state: dict[str, Any],
    now: dt.datetime | None = None,
    error_code: str = "collector_error",
) -> FailureUpdate:
    state["last_run_at"] = (now or utc_now()).isoformat()
    state["last_status"] = "error"
    state["last_error_code"] = error_code
    state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    should_alert = state["consecutive_failures"] >= 2 and not state.get("technical_alert_sent", False)
    if should_alert:
        state["technical_alert_sent"] = True
    return FailureUpdate(should_alert=should_alert)


def record_success(
    state: dict[str, Any],
    total_percent: int,
    now: dt.datetime | None = None,
) -> None:
    state["last_run_at"] = (now or utc_now()).isoformat()
    state["last_status"] = "success"
    state["last_total_percent"] = total_percent
    state["consecutive_failures"] = 0
    state["technical_alert_sent"] = False
    state.pop("last_error_code", None)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QuotaError("quota state could not be read", "state_read_error") from error
    if not isinstance(payload, dict):
        raise QuotaError("quota state is not an object", "state_invalid")
    return payload


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class RunLock:
    def __init__(self, path: Path, stale_after: dt.timedelta = dt.timedelta(hours=6)) -> None:
        self.path = path
        self.stale_after = stale_after
        self._owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = dt.datetime.now(dt.UTC).timestamp() - self.path.stat().st_mtime
                if age > self.stale_after.total_seconds():
                    self.path.unlink()
                    return self.acquire()
            except OSError:
                pass
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\ncreated={iso_now()}\n")
        self._owned = True
        return True

    def release(self) -> None:
        if not self._owned:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._owned = False

    def __enter__(self) -> Self:
        if not self.acquire():
            raise QuotaError("quota check already running", "overlap")
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()


def validate_quote(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    quote = re.sub(r"\s+", " ", value).strip().strip('"“”')
    if not quote or len(quote) > 160 or "\n" in value:
        return None
    if re.search(
        r"https?://|www\.|\b(?:password|token|secret|cookie|api[_ -]?key)\b|```|\$\(|`|;\s*(?:ssh|curl|rm|python)",
        quote,
        re.IGNORECASE,
    ):
        return None
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", quote, re.IGNORECASE):
        return None
    return quote


def _load_quotes(path: Path) -> list[Quote]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    quotes: list[Quote] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        quote = validate_quote(item.get("quote"))
        if quote:
            quotes.append(Quote(quote=quote, author=DEFAULT_AUTHOR))
    return quotes


def _extract_model_text(payload: object) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        for key in ("text", "content", "response", "message", "output"):
            if key in payload:
                result = _extract_model_text(payload[key])
                if result:
                    return result
        for value in payload.values():
            result = _extract_model_text(value)
            if result:
                return result
    if isinstance(payload, list):
        for value in payload:
            result = _extract_model_text(value)
            if result:
                return result
    return None


def run_openclaw_quote(
    prompt: str,
    timeout: float = DEFAULT_MODEL_TIMEOUT,
    agent: str = "main",
) -> str:
    prompt_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(prompt)
            prompt_path = handle.name
        try:
            os.chmod(prompt_path, 0o600)
        except OSError:
            pass
        result = subprocess.run(
            ["openclaw", "agent", "--agent", agent, "--message-file", prompt_path, "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise QuotaError("quote model failed", "quote_model_error")
        raw = result.stdout.strip()
        try:
            return _extract_model_text(json.loads(raw)) or raw
        except json.JSONDecodeError:
            return raw
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        raise QuotaError("quote model unavailable", "quote_model_error") from error
    finally:
        if prompt_path:
            try:
                os.unlink(prompt_path)
            except FileNotFoundError:
                pass


def choose_quote(
    total_percent: int,
    observed_at: str,
    model_runner: Callable[[str], object] | None = None,
    fallback_quotes: Sequence[Quote | Mapping[str, Any]] | None = None,
) -> Quote:
    prompt = (
        "Return exactly one original Vietnamese coding motivation sentence, no more than 120 "
        f"characters. Do not include quotation marks, an author, markdown, URLs, commands, "
        f"emails, credentials, or secrets. The quota snapshot is {total_percent}% at {observed_at}."
    )
    model_candidate: object | None = None
    try:
        result = (model_runner or run_openclaw_quote)(prompt)
        model_candidate = _extract_model_text(result)
    except Exception as _error:  # noqa: BLE001 - providers expose arbitrary failures
        model_candidate = None
    validated = validate_quote(model_candidate)
    if validated:
        return Quote(validated, DEFAULT_AUTHOR)

    normalized: list[Quote] = []
    for item in fallback_quotes or []:
        if isinstance(item, Quote):
            normalized.append(item)
        elif isinstance(item, Mapping):
            quote = validate_quote(item.get("quote"))
            if quote:
                normalized.append(Quote(quote, DEFAULT_AUTHOR))
    if not normalized:
        normalized = [Quote("Một bước nhỏ vẫn đưa code tiến lên.", DEFAULT_AUTHOR)]
    return random.SystemRandom().choice(normalized)


def send_discord(
    message: str,
    channel_id: str = DEFAULT_CHANNEL,
    runner: Callable[..., Any] = subprocess.run,
    attempts: int = 2,
) -> bool:
    args = [
        "openclaw",
        "message",
        "send",
        "--channel",
        "discord",
        "--target",
        f"channel:{channel_id}",
        "--message",
        message,
        "--json",
    ]
    for _ in range(max(1, attempts)):
        try:
            result = runner(args, capture_output=True, text=True, check=False, timeout=20)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        if getattr(result, "returncode", 1) == 0:
            return True
    return False


def _load_quotes_for_cli(path: Path) -> list[Quote]:
    return _load_quotes(path) if path.exists() else _load_quotes(DEFAULT_QUOTES_PATH)


def _result_json(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def execute(
    args: argparse.Namespace,
    *,
    now: dt.datetime | None = None,
    collector: QuotaCollector | None = None,
    model_runner: Callable[[str], object] | None = None,
    discord_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    current = now or utc_now()
    dry_run = not args.send
    state_path = Path(args.state_file)
    lock = RunLock(state_path.with_suffix(state_path.suffix + ".lock"))
    if not lock.acquire():
        return _result_json(status="skipped-overlap", sent=False)
    try:
        persisted = load_state(state_path)
        working_state = copy.deepcopy(persisted)
        interval = getattr(args, "interval", DEFAULT_INTERVAL)
        next_run = advance_schedule(working_state, current, interval)
        active_collector = collector or QuotaCollector(
            base_url=args.base_url,
            quota_path=args.quota_path,
            api_url=args.api_url,
            provider=args.provider,
            timeout=args.timeout,
            auth_token=os.environ.get("NINEROUTER_AUTH_TOKEN"),
            auth_cookie=os.environ.get("NINEROUTER_AUTH_COOKIE"),
        )
        try:
            snapshot = active_collector.collect(current.isoformat())
        except QuotaError as error:
            failure = record_failure(working_state, current, error.code)
            alert_sent = False
            if failure.should_alert:
                alert = format_technical_alert(next_run)
                alert_sent = False if dry_run else send_discord(alert, args.channel, discord_runner)
            if not dry_run:
                save_state(state_path, working_state)
            result = _result_json(
                status="error",
                error_code=error.code,
                consecutive_failures=working_state["consecutive_failures"],
                technical_alert_attempted=failure.should_alert,
                technical_alert_sent=alert_sent,
                next_run_at=working_state["next_run_at"],
                sent=alert_sent,
            )
            if args.json_output:
                return result
            if dry_run and failure.should_alert:
                print(format_technical_alert(next_run))
            return result

        quotes = _load_quotes_for_cli(Path(args.quotes_file))
        selected = choose_quote(
            snapshot.total_remaining_percent,
            snapshot.observed_at,
            model_runner=(model_runner if not dry_run else lambda _prompt: None),
            fallback_quotes=quotes,
        )
        message = format_message(
            snapshot.total_remaining_percent,
            reminder_for(snapshot.total_remaining_percent),
            selected.quote,
            selected.author,
            next_run,
        )
        record_success(working_state, snapshot.total_remaining_percent, current)
        sent = False if dry_run else send_discord(message, args.channel, discord_runner)
        working_state["last_delivery_status"] = "sent" if sent else "not_sent"
        if not dry_run:
            save_state(state_path, working_state)
        result = _result_json(
            status="success" if sent or dry_run else "delivery_error",
            total_remaining_percent=snapshot.total_remaining_percent,
            valid_accounts=snapshot.valid_accounts,
            source=snapshot.source,
            next_run_at=working_state["next_run_at"],
            sent=sent,
            message=message,
        )
        if dry_run and not args.json_output:
            print(message)
        return result
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the message without sending")
    mode.add_argument("--send", action="store_true", help="send the report to Discord")
    parser.add_argument("--json", dest="json_output", action="store_true", help="emit a JSON result")
    parser.add_argument("--base-url", default=os.environ.get("NINEROUTER_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--quota-path",
        default=os.environ.get("NINEROUTER_QUOTA_PATH", DEFAULT_QUOTA_PATH),
    )
    parser.add_argument("--api-url", default=os.environ.get("NINEROUTER_API_URL", DEFAULT_API_URL))
    parser.add_argument("--provider", default=os.environ.get("NINEROUTER_PROVIDER", "codex"))
    parser.add_argument(
        "--channel",
        default=os.environ.get("QUOTA_DISCORD_CHANNEL", DEFAULT_CHANNEL),
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get("QUOTA_STATE_FILE", str(DEFAULT_STATE_PATH)),
    )
    parser.add_argument(
        "--quotes-file",
        default=os.environ.get("QUOTA_QUOTES_FILE", str(DEFAULT_QUOTES_PATH)),
    )
    parser.add_argument(
        "--interval",
        type=parse_interval,
        default=parse_interval(os.environ.get("QUOTA_INTERVAL") or "3h"),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("QUOTA_HTTP_TIMEOUT") or "10"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = execute(args)
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") not in {"error", "delivery_error"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
