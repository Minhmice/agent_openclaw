"""Domain and platform persistence errors."""

from __future__ import annotations


class RepositoryError(RuntimeError):
    """Base class for persistence-level failures."""


class RepositoryConflict(RepositoryError):
    """An immutable identity already exists with different content."""


RepositoryConflictError = RepositoryConflict


class RunConfigMismatchError(RepositoryConflict):
    """A run idempotency key was resumed with a different configuration."""
