"""Redacted JSON events and conservative retention helpers."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_SECRET_KEYS = {"authorization", "cookie", "password", "secret", "token", "api_key", "key"}
def _clean(value: Any, key: str = "") -> Any:
    if key.casefold() in _SECRET_KEYS: return "[REDACTED]"
    if isinstance(value, dict): return {str(k): _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, list): return [_clean(v) for v in value]
    return value
def redact(event: object) -> str:
    return json.dumps(_clean(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def log_event(event: str, **fields: object) -> str:
    return redact({"event": event, "timestamp": datetime.now(UTC).isoformat(), **fields})
def apply_retention(root: Path, *, now: datetime | None = None, days: int = 30) -> tuple[Path, ...]:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days); removed = []
    for pattern in ("rejected/**/screenshots/*", "runs/**/*.log", "tmp/failed/**"):
        for path in root.glob(pattern):
            if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                path.unlink(); removed.append(path)
    return tuple(removed)
