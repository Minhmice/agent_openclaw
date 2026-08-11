"""Deterministic SQLite connection setup."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _is_memory_database(location: str) -> bool:
    return location == ":memory:" or (
        location.startswith("file:") and ("mode=memory" in location or location.startswith("file::memory:"))
    )


def connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open a configured autocommit connection for explicit repository transactions.

    The caller owns the returned connection and must call ``close()``. Use
    :func:`managed_connection` when lexical close-on-exit behavior is preferred.
    """

    location = os.fspath(path)
    is_uri = location.startswith("file:")
    is_memory = _is_memory_database(location)
    if not is_memory and not is_uri:
        Path(location).expanduser().parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        location,
        timeout=5.0,
        isolation_level=None,
        uri=is_uri,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not is_memory:
            connection.execute("PRAGMA journal_mode = WAL")
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
