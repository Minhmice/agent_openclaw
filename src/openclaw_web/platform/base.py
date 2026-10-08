"""Shared persistence utilities and base store for SQLite transactional stores."""

from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import tldextract
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter
from openclaw_web.models import WebUrl

from openclaw_web.platform.errors import RepositoryConflict, RepositoryError

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_WEB_URL_ADAPTER = TypeAdapter(WebUrl)
_DOMAIN_EXTRACTOR = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    fallback_to_snapshot=True,
    include_psl_private_domains=True,
)


def _canonical_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_mapping_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_nonblank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _lease_expiry(now: datetime, lease_seconds: int) -> tuple[str, str]:
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    normalized_now = now.astimezone(UTC) if now.tzinfo is not None else now
    now_text = _utc_text(normalized_now)
    return now_text, _utc_text(normalized_now + timedelta(seconds=lease_seconds))


def _validated_url(value: str | AnyHttpUrl) -> AnyHttpUrl:
    try:
        validated = _WEB_URL_ADAPTER.validate_python(str(value))
    except Exception as exc:
        raise ValueError(f"invalid website URL: {exc}") from exc
    if validated.username is not None or validated.password is not None:
        raise ValueError("website URL must not include credentials")
    if validated.host is None:
        raise ValueError("website URL must include a host")
    return validated


def _canonical_domain(value: str | AnyHttpUrl) -> tuple[AnyHttpUrl, str]:
    url = _validated_url(value)
    raw_host = url.host
    if raw_host is None:
        raise ValueError("website URL must include a host")
    host = raw_host.lower().rstrip(".").removeprefix("www.")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise ValueError("website URL must include a host")

    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        extracted = _DOMAIN_EXTRACTOR(host)
        registrable = extracted.top_domain_under_public_suffix
        if registrable:
            host = registrable.lower().rstrip(".")
    else:
        host = f"[{parsed_ip.compressed}]" if parsed_ip.version == 6 else parsed_ip.compressed

    return url, host


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    without_punctuation = "".join(
        " " if unicodedata.category(character).startswith(("P", "Z")) else character
        for character in normalized
    )
    return re.sub(r"\s+", " ", without_punctuation).strip()


@contextmanager
def _immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise RepositoryError("repository mutation cannot run inside another transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException as error:
        try:
            connection.rollback()
        except BaseException as rollback_error:  # noqa: BLE001 - keep the primary failure
            error.add_note(f"rollback also failed: {rollback_error!r}")
        raise


def _deserialize(model_type: type[_ModelT], snapshot: str) -> _ModelT:
    return model_type.model_validate_json(snapshot)


class BaseStore:
    """Base persistence store wrapping an open SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _append_snapshot(
        self,
        *,
        table: str,
        identity_column: str,
        record_id: str,
        model: BaseModel,
        model_type: type[_ModelT],
        insert_sql: str,
        values: tuple[Any, ...],
    ) -> None:
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                f"SELECT snapshot_json FROM {table} WHERE {identity_column} = ?", (record_id,)
            ).fetchone()
            if row is not None:
                persisted = _deserialize(model_type, str(row["snapshot_json"]))
                if persisted != model:
                    raise RepositoryConflict(
                        f"{table} identity already exists with different immutable content"
                    )
                return
            self.connection.execute(insert_sql, values)
