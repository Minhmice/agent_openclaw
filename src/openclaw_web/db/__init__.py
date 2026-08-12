"""SQLite persistence primitives for the OpenClaw website workflow."""

from openclaw_web.db.connection import ConnectionConfigurationError, connect, managed_connection
from openclaw_web.db.migrations import MigrationError, migrate
from openclaw_web.db.repository import (
    DiscoverySeedDisposition,
    DiscoverySeedBatch,
    DiscoverySeedUpsertResult,
    Repository,
    RepositoryConflict,
    RepositoryConflictError,
    RepositoryError,
    RunConfigMismatchError,
)

__all__ = [
    "ConnectionConfigurationError",
    "DiscoverySeedDisposition",
    "DiscoverySeedBatch",
    "DiscoverySeedUpsertResult",
    "MigrationError",
    "Repository",
    "RepositoryConflict",
    "RepositoryConflictError",
    "RepositoryError",
    "RunConfigMismatchError",
    "connect",
    "managed_connection",
    "migrate",
]
