"""Deterministic cohort assignment, strict configuration, and fair scheduling."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml  # type: ignore[import-untyped]

from openclaw_web.models import ClaimStatus

APPROVED_COHORTS = (
    "manufacturer",
    "professional-services",
    "local-service",
    "showroom-retail",
    "ecommerce",
    "education",
    "healthcare",
    "hospitality",
    "real-estate",
    "other",
)
MONEY_SCORING_DIMENSIONS = (
    "search_demand",
    "aov_ltv",
    "trust_dependency",
    "online_conversion_fit",
    "business_strength",
    "web_gap",
)
COHORT_CONFIG_KEYS = frozenset(
    {
        "cohort_id",
        "version",
        "business_scoring_overrides",
        "money_scoring_overrides",
        "required_evidence_categories",
        "conversion_intent_vocabulary",
    }
)
_OVERRIDE_MIN = 0.0
_OVERRIDE_MAX = 2.0

# Classification hierarchy:
# 1. exact approved ``industry_hint``;
# 2. keyword cohort precedence across industry_hint, name, then description;
# 3. explicitly inferred AI suggestion;
# 4. deterministic but unverified ``other`` fallback.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("manufacturer", ("nha may", "san xuat", "manufacturer", "factory", "industrial")),
    ("professional-services", ("luat", "legal", "ke toan", "accounting", "tu van", "consulting")),
    ("local-service", ("sua chua", "ve sinh", "repair", "cleaning", "spa", "salon")),
    ("showroom-retail", ("showroom", "cua hang", "retail", "trung bay")),
    ("ecommerce", ("thuong mai dien tu", "ban hang online", "ecommerce", "online shop")),
    ("education", ("giao duc", "dao tao", "truong hoc", "education", "training", "academy")),
    ("healthcare", ("y te", "benh vien", "phong kham", "healthcare", "clinic", "hospital")),
    ("hospitality", ("khach san", "nha hang", "hospitality", "hotel", "restaurant", "resort")),
    ("real-estate", ("bat dong san", "real estate", "property", "chung cu")),
)


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("classification text must be a string")
    text = unicodedata.normalize("NFKD", value)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.replace("đ", "d").replace("Đ", "D").casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_APPROVED_COHORT_BY_NORMALIZED_ID = {_normalize(cohort): cohort for cohort in APPROVED_COHORTS}


def _contains_keyword(corpus: str, keyword: str) -> bool:
    return f" {keyword} " in f" {corpus} "


def _first_matching_field(fields: Sequence[tuple[str, str]], keyword: str) -> str | None:
    return next(
        (field for field, text in fields if _contains_keyword(text, keyword)),
        None,
    )


@dataclass(frozen=True, slots=True)
class CohortAssignment:
    cohort: str
    deterministic: bool
    claim_status: ClaimStatus
    evidence_keywords: tuple[str, ...]
    evidence_fields: tuple[str, ...] = ()
    reason: str | None = None


def assign_cohort(
    *,
    industry_hint: str | None = None,
    name: str | None = None,
    description: str | None = None,
    ai_suggestion: str | None = None,
    ai_claim_status: ClaimStatus | None = None,
) -> CohortAssignment:
    """Assign one approved cohort according to the documented evidence hierarchy."""

    normalized_industry_hint = _normalize(industry_hint)
    if cohort := _APPROVED_COHORT_BY_NORMALIZED_ID.get(normalized_industry_hint):
        return CohortAssignment(
            cohort,
            True,
            ClaimStatus.OBSERVED,
            (cohort,),
            ("industry_hint",),
            "exact_approved_industry_hint",
        )

    normalized_fields = tuple(
        (field, normalized)
        for field, value in (
            ("industry_hint", industry_hint),
            ("name", name),
            ("description", description),
        )
        if (normalized := _normalize(value))
    )
    for cohort, keywords in _KEYWORDS:
        matches = tuple(
            (keyword, matched_field)
            for keyword in keywords
            if (matched_field := _first_matching_field(normalized_fields, keyword)) is not None
        )
        if matches:
            return CohortAssignment(
                cohort,
                True,
                ClaimStatus.OBSERVED,
                tuple(keyword for keyword, _ in matches),
                tuple(field for _, field in matches),
                "keyword_precedence",
            )

    explicit_other_field = next(
        (field for field, text in normalized_fields if text == "other"),
        None,
    )
    if explicit_other_field is not None:
        return CohortAssignment(
            "other",
            True,
            ClaimStatus.OBSERVED,
            ("other",),
            (explicit_other_field,),
            "explicit_other",
        )

    if ai_claim_status is ClaimStatus.INFERRED and ai_suggestion in APPROVED_COHORTS:
        return CohortAssignment(
            ai_suggestion,
            False,
            ClaimStatus.INFERRED,
            (),
            (),
            "explicit_inferred_ai_suggestion",
        )
    return CohortAssignment(
        "other",
        True,
        ClaimStatus.UNVERIFIED,
        (),
        (),
        "no_deterministic_or_inferred_cohort_evidence",
    )


def allocate_cohort_budget(cohorts: Sequence[str], total: int) -> dict[str, int]:
    """Split a nonnegative integer budget equally, distributing remainder in input order."""

    if isinstance(total, bool) or not isinstance(total, int):
        raise TypeError("total must be an integer")
    if total < 0:
        raise ValueError("total must be nonnegative")
    normalized: list[str] = []
    for cohort in cohorts:
        if not isinstance(cohort, str):
            raise TypeError("cohort names must be strings")
        name = cohort.strip()
        if not name:
            raise ValueError("cohort names must not be blank")
        if name in normalized:
            raise ValueError("cohort names must be unique")
        normalized.append(name)
    if not normalized:
        return {}
    floor, remainder = divmod(total, len(normalized))
    return {
        cohort: floor + (1 if index < remainder else 0) for index, cohort in enumerate(normalized)
    }


@dataclass(frozen=True, slots=True)
class CohortCandidate:
    """Minimal stable candidate identity with an approved cohort."""

    identity: str
    cohort: str = "other"

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise ValueError("candidate identity must be a nonblank string")
        if not isinstance(self.cohort, str) or not self.cohort.strip():
            raise ValueError("candidate cohort must be a nonblank string")
        object.__setattr__(self, "identity", self.identity.strip())
        normalized_cohort = self.cohort.strip()
        object.__setattr__(
            self,
            "cohort",
            normalized_cohort if normalized_cohort in APPROVED_COHORTS else "other",
        )


def _validate_budget(budget_by_cohort: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for raw_cohort, budget in budget_by_cohort.items():
        if not isinstance(raw_cohort, str) or not raw_cohort.strip():
            raise ValueError("budget cohort names must be nonblank strings")
        cohort = raw_cohort.strip()
        if cohort not in APPROVED_COHORTS:
            raise ValueError(f"budget cohort must be one of the approved cohorts: {cohort}")
        if isinstance(budget, bool) or not isinstance(budget, int):
            raise TypeError("cohort budgets must be integers")
        if budget < 0:
            raise ValueError("cohort budgets must be nonnegative")
        if cohort in result:
            raise ValueError("normalized budget cohort names must be unique")
        result[cohort] = budget
    return result


def schedule_candidates(
    candidates: Iterable[CohortCandidate],
    budget_by_cohort: Mapping[str, int],
    *,
    overall_cap: int | None = None,
    rotation_offset: int = 0,
) -> list[CohortCandidate]:
    """Select by canonical round-robin, rotated per persisted schedule window.

    Callers using an overall cap must persist or deterministically derive
    ``rotation_offset`` for each schedule window to prevent cross-run starvation.
    """

    budgets = _validate_budget(budget_by_cohort)
    if overall_cap is not None:
        if isinstance(overall_cap, bool) or not isinstance(overall_cap, int):
            raise TypeError("overall_cap must be an integer")
        if overall_cap < 0:
            raise ValueError("overall_cap must be nonnegative")
    if isinstance(rotation_offset, bool) or not isinstance(rotation_offset, int):
        raise TypeError("rotation_offset must be an integer")
    if rotation_offset < 0:
        raise ValueError("rotation_offset must be nonnegative")

    canonical = [cohort for cohort in APPROVED_COHORTS if cohort in budgets]
    if canonical:
        offset = rotation_offset % len(canonical)
        traversal = canonical[offset:] + canonical[:offset]
    else:
        traversal = []
    budget_total = sum(budgets.values())
    limit = budget_total if overall_cap is None else min(overall_cap, budget_total)
    buckets: dict[str, list[CohortCandidate]] = {cohort: [] for cohort in canonical}
    queued_identities: set[str] = set()
    for candidate in candidates:
        if candidate.identity in queued_identities:
            continue
        if candidate.cohort not in buckets:
            continue
        queued_identities.add(candidate.identity)
        buckets[candidate.cohort].append(candidate)

    selected: list[CohortCandidate] = []
    used = {cohort: 0 for cohort in canonical}
    offsets = {cohort: 0 for cohort in canonical}
    while len(selected) < limit:
        progressed = False
        for cohort in traversal:
            if len(selected) >= limit:
                break
            if used[cohort] >= budgets[cohort] or offsets[cohort] >= len(buckets[cohort]):
                continue
            selected.append(buckets[cohort][offsets[cohort]])
            offsets[cohort] += 1
            used[cohort] += 1
            progressed = True
        if not progressed:
            break
    return selected


@dataclass(frozen=True, slots=True)
class CohortConfig:
    """Strict, immutable cohort configuration; validation only, not a scoring engine."""

    cohort_id: str
    version: str
    business_scoring_overrides: Mapping[str, float]
    money_scoring_overrides: Mapping[str, float]
    required_evidence_categories: tuple[str, ...]
    conversion_intent_vocabulary: tuple[str, ...]


def _strict_string_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a nonempty list")
    normalized_seen: set[str] = set()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} entries must be nonblank strings")
        normalized = _normalize(item)
        if normalized in normalized_seen:
            raise ValueError(f"{field} entries must be unique after normalization")
        normalized_seen.add(normalized)
        result.append(item.strip())
    return tuple(result)


def _strict_override_mapping(
    value: object,
    *,
    field: str,
    exact_keys: frozenset[str] | None = None,
    key_pattern: re.Pattern[str] | None = None,
) -> Mapping[str, float]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a nonempty mapping")
    if exact_keys is not None and set(value) != exact_keys:
        raise ValueError(f"{field} must contain exactly {sorted(exact_keys)}")
    result: dict[str, float] = {}
    normalized_keys: set[str] = set()
    for key, raw_number in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{field} keys must be nonblank strings")
        normalized_key = _normalize(key)
        if key_pattern is not None and key_pattern.fullmatch(key.strip()) is None:
            raise ValueError(f"{field} keys must match {key_pattern.pattern}")
        if normalized_key in normalized_keys:
            raise ValueError(f"{field} keys must be unique after normalization")
        normalized_keys.add(normalized_key)
        if isinstance(raw_number, bool) or not isinstance(raw_number, int | float):
            raise TypeError(f"{field} values must be finite real numbers")
        number = float(raw_number)
        if not math.isfinite(number) or not _OVERRIDE_MIN <= number <= _OVERRIDE_MAX:
            raise ValueError(
                f"{field} values must be finite and between {_OVERRIDE_MIN} and {_OVERRIDE_MAX}"
            )
        result[key.strip()] = number
    return MappingProxyType(result)


def load_cohort_config(path: Path) -> CohortConfig:
    """Load and strictly validate a Task 5 cohort YAML contract."""

    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid cohort YAML: {path.name}") from exc
    if not isinstance(payload, dict) or set(payload) != COHORT_CONFIG_KEYS:
        raise ValueError(f"cohort YAML must contain exactly {sorted(COHORT_CONFIG_KEYS)}")
    cohort_id = payload["cohort_id"]
    if cohort_id not in APPROVED_COHORTS or cohort_id != path.stem:
        raise ValueError("cohort_id must be an approved cohort matching the filename")
    if payload["version"] != "cohort-v1":
        raise ValueError("unsupported cohort version")
    return CohortConfig(
        cohort_id=cohort_id,
        version=payload["version"],
        business_scoring_overrides=_strict_override_mapping(
            payload["business_scoring_overrides"],
            field="business_scoring_overrides",
            key_pattern=re.compile(r"[a-z][a-z0-9_]*_weight"),
        ),
        money_scoring_overrides=_strict_override_mapping(
            payload["money_scoring_overrides"],
            field="money_scoring_overrides",
            exact_keys=frozenset(MONEY_SCORING_DIMENSIONS),
        ),
        required_evidence_categories=_strict_string_list(
            payload["required_evidence_categories"],
            field="required_evidence_categories",
        ),
        conversion_intent_vocabulary=_strict_string_list(
            payload["conversion_intent_vocabulary"],
            field="conversion_intent_vocabulary",
        ),
    )
