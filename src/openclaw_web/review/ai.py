"""Schema-constrained, evidence-bounded AI review service."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class AsyncReviewModel(Protocol):
    async def complete(self, prompt: str) -> str:
        """Return a model response for one bounded prompt."""


_ALLOWED_FIELDS = frozenset(
    {
        "summary_vi",
        "business_evidence",
        "page_audits",
        "conversion_hypothesis",
        "top_issues",
        "redesign_angle",
        "evidence_matrix",
        "image_evidence",
        "confidence_gaps",
        "next_action",
    }
)
_PROTECTED_OUTPUT_FIELDS = frozenset({"lead_score", "rule_matches", "measured_metrics", "deterministic_rule_matches"})
_SECRET_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|private[_-]?key)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ReviewResult:
    status: str
    data: dict[str, Any]
    deterministic_fallback: bool
    protected_inputs: dict[str, Any]
    validation_error: str | None = None


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact(item) for item in value]
    return value


def _json_object(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("model output must be a JSON object")
    unknown = sorted(set(value) - _ALLOWED_FIELDS - _PROTECTED_OUTPUT_FIELDS)
    if unknown:
        raise ValueError(f"unsupported review fields: {', '.join(map(str, unknown))}")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("review field names must be strings")
        if key in _PROTECTED_OUTPUT_FIELDS:
            continue
        if key in {"summary_vi", "conversion_hypothesis", "redesign_angle", "next_action"} and not isinstance(item, str):
            raise TypeError(f"{key} must be a string")
        if key in {"confidence_gaps", "business_evidence", "page_audits", "top_issues", "evidence_matrix", "image_evidence"} and not isinstance(item, list):
            raise TypeError(f"{key} must be an array")
        result[key] = item
    return result


def _parse_response(response: str) -> dict[str, Any]:
    if not isinstance(response, str):
        raise TypeError("model response must be text")
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        return _json_object(json.loads(cleaned))
    except json.JSONDecodeError as exc:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return _json_object(json.loads(cleaned[start : end + 1]))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        raise ValueError("model response is not valid JSON") from exc


def _prompt(bundle: Mapping[str, object], validation_error: str | None = None) -> str:
    safe_bundle = _redact(bundle)
    retry = (
        f"\nValidation error from the previous response: {validation_error[:400]}\n"
        "Return a corrected JSON object only.\n"
        if validation_error
        else ""
    )
    return (
        "You are reviewing a website lead. Chỉ trả về JSON theo các field cho phép; "
        "không thay đổi số đo hoặc kết quả luật xác định.\n"
        "Allowed fields: "
        + ", ".join(sorted(_ALLOWED_FIELDS))
        + ".\n"
        + retry
        + "BEGIN_UNTRUSTED_EVIDENCE\n"
        + json.dumps(safe_bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\nEND_UNTRUSTED_EVIDENCE"
    )


async def _complete(model: AsyncReviewModel, prompt: str) -> str:
    result = model.complete(prompt)
    if inspect.isawaitable(result):
        return await result
    return str(result)


class ReviewService:
    """Validate one model response and retry once before deterministic fallback."""

    def __init__(self, model: AsyncReviewModel) -> None:
        self._model = model

    async def review(self, bundle: Mapping[str, object]) -> ReviewResult:
        if not isinstance(bundle, Mapping):
            raise TypeError("review bundle must be a mapping")
        protected = {
            key: _redact(bundle[key])
            for key in ("measured_metrics", "deterministic_rule_matches")
            if key in bundle
        }
        error: str | None = None
        for _attempt in range(2):
            try:
                data = _parse_response(await _complete(self._model, _prompt(bundle, error)))
                return ReviewResult("complete", data, False, protected)
            except (TypeError, ValueError) as exc:
                error = str(exc)
        fallback: dict[str, Any] = {
            "summary_vi": "Không thể hoàn tất đánh giá AI; giữ nguyên bằng chứng xác định.",
            "confidence_gaps": ["Đầu ra AI không hợp lệ sau một lần thử lại."],
            "next_action": "Rà soát thủ công bằng evidence đã thu thập.",
        }
        if isinstance(bundle.get("project_id"), str):
            fallback["project_id"] = bundle["project_id"]
        return ReviewResult("failed", fallback, True, protected, error)
