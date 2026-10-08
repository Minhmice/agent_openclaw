"""Deterministic SQLite connection setup."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from urllib.request import url2pathname


class ConnectionConfigurationError(RuntimeError):
    """Raised when a SQLite location cannot satisfy required connection invariants."""


@dataclass(frozen=True)
class _DatabaseLocation:
    target: str
    is_uri: bool
    is_memory: bool
    is_readonly: bool
    writable_parent: Path | None


def _sqlite_boolean(value: str, parameter: str) -> bool:
    normalized = value.casefold()
    if normalized in {"1", "on", "true", "yes"}:
        return True
    if normalized in {"0", "off", "false", "no"}:
        return False
    raise ConnectionConfigurationError(
        f"file URI parameter {parameter!r} must be a SQLite boolean"
    )


def _single_query_value(query: list[tuple[str, str]], name: str) -> str | None:
    values = [value for key, value in query if key == name]
    if len(values) > 1:
        raise ConnectionConfigurationError(f"file URI parameter {name!r} must not repeat")
    return values[0] if values else None


def _parse_location(path: str | os.PathLike[str]) -> _DatabaseLocation:
    raw = os.fspath(path)
    if raw == ":memory:":
        return _DatabaseLocation(raw, False, True, False, None)
    if not raw.startswith("file:"):
        normalized = os.path.abspath(os.path.expanduser(raw))
        return _DatabaseLocation(
            normalized,
            False,
            False,
            False,
            Path(normalized).parent,
        )

    parsed = urlsplit(raw)
    if parsed.netloc:
        raise ConnectionConfigurationError("file URI authorities and UNC paths are unsupported")
    if parsed.fragment:
        raise ConnectionConfigurationError("file URI fragments are unsupported")
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ConnectionConfigurationError("file URI contains a malformed query") from error

    mode = _single_query_value(query, "mode")
    if mode is not None and mode not in {"memory", "ro", "rw", "rwc"}:
        raise ConnectionConfigurationError(f"unsupported SQLite file URI mode: {mode!r}")
    immutable_value = _single_query_value(query, "immutable")
    immutable = (
        _sqlite_boolean(immutable_value, "immutable")
        if immutable_value is not None
        else False
    )
    is_memory = parsed.path == ":memory:" or mode == "memory"
    if is_memory:
        return _DatabaseLocation(raw, True, True, False, None)

    decoded_path = url2pathname(unquote(parsed.path))
    if not decoded_path:
        raise ConnectionConfigurationError("file URI must identify a database path")
    if decoded_path.startswith("~"):
        raise ConnectionConfigurationError(
            "tilde expansion in file URIs is unsupported; pass a filesystem path instead"
        )
    normalized_path = Path(os.path.abspath(decoded_path))
    readonly = mode == "ro" or immutable
    return _DatabaseLocation(
        raw,
        True,
        False,
        readonly,
        None if readonly else normalized_path.parent,
    )


def connect(
    path: str | os.PathLike[str], *, check_same_thread: bool = True
) -> sqlite3.Connection:
    """Open a configured autocommit connection for explicit repository transactions.

    The caller owns the returned connection and must call ``close()``. Use
    :func:`managed_connection` when lexical close-on-exit behavior is preferred.
    """

    location = _parse_location(path)
    if location.writable_parent is not None:
        location.writable_parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        location.target,
        timeout=5.0,
        isolation_level=None,
        uri=location.is_uri,
        check_same_thread=check_same_thread,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not location.is_memory and not location.is_readonly:
            row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            journal_mode = None if row is None else str(row[0]).casefold()
            if journal_mode != "wal":
                raise ConnectionConfigurationError(
                    f"writable SQLite databases require WAL journal mode, got {journal_mode!r}"
                )
        connection.execute("PRAGMA synchronous = NORMAL")
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def managed_connection(path: str | os.PathLike[str]) -> Iterator[sqlite3.Connection]:
    """Yield a configured connection and always close it on context exit."""

    connection = connect(path)
    try:
        yield connection
    finally:
        connection.close()
