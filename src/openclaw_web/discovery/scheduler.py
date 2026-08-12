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
BUSINESS_SCORING_DIMENSIONS = {
    "manufacturer": frozenset({"operational_scale_weight", "technical_credibility_weight"}),
    "professional-services": frozenset({"authority_weight", "case_study_weight"}),
    "local-service": frozenset({"local_trust_weight", "response_speed_weight"}),
    "showroom-retail": frozenset({"product_discovery_weight", "store_visit_weight"}),
    "ecommerce": frozenset({"merchandising_weight", "checkout_clarity_weight"}),
    "education": frozenset({"program_clarity_weight", "outcome_evidence_weight"}),
    "healthcare": frozenset({"clinical_trust_weight", "care_access_weight"}),
    "hospitality": frozenset({"experience_weight", "availability_weight"}),
    "real-estate": frozenset({"listing_quality_weight", "agent_trust_weight"}),
    "other": frozenset({"proposition_clarity_weight", "trust_weight"}),
}
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


@dataclass(frozen=True, slots=True)
class SchedulerCursor:
    """Stable cross-window position in the canonical cohort/candidate universe."""

    cohort_index: int = 0
    candidate_offsets: tuple[int, ...] = (0,) * len(APPROVED_COHORTS)

    def __post_init__(self) -> None:
        if (
            isinstance(self.cohort_index, bool)
            or not isinstance(self.cohort_index, int)
            or not 0 <= self.cohort_index < len(APPROVED_COHORTS)
        ):
            raise ValueError("cursor cohort_index is invalid")
        if len(self.candidate_offsets) != len(APPROVED_COHORTS) or any(
            isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            for offset in self.candidate_offsets
        ):
            raise ValueError("cursor candidate_offsets are invalid")


@dataclass(frozen=True, slots=True)
class SchedulerResult:
    """Selected candidates and the immutable cursor to persist for the next window."""

    selected: tuple[CohortCandidate, ...]
    next_cursor: SchedulerCursor


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


def _candidate_buckets(
    candidates: Iterable[CohortCandidate],
) -> dict[str, list[CohortCandidate]]:
    buckets: dict[str, list[CohortCandidate]] = {cohort: [] for cohort in APPROVED_COHORTS}
    queued_by_identity: dict[str, CohortCandidate] = {}
    for candidate in candidates:
        existing = queued_by_identity.get(candidate.identity)
        if existing is None or APPROVED_COHORTS.index(candidate.cohort) < APPROVED_COHORTS.index(
            existing.cohort
        ):
            queued_by_identity[candidate.identity] = candidate
    for candidate in queued_by_identity.values():
        buckets[candidate.cohort].append(candidate)
    for bucket in buckets.values():
        bucket.sort(key=lambda candidate: candidate.identity)
    return buckets


def _validate_cap(overall_cap: int | None) -> None:
    if overall_cap is not None:
        if isinstance(overall_cap, bool) or not isinstance(overall_cap, int):
            raise TypeError("overall_cap must be an integer")
        if overall_cap < 0:
            raise ValueError("overall_cap must be nonnegative")


def _cursor_after_offset(
    cursor: SchedulerCursor,
    buckets: Mapping[str, Sequence[CohortCandidate]],
    budgets: Mapping[str, int],
    rotation_offset: int,
) -> SchedulerCursor:
    """Advance a compatibility offset over candidate slots, without consuming a run budget."""

    if isinstance(rotation_offset, bool) or not isinstance(rotation_offset, int):
        raise TypeError("rotation_offset must be an integer")
    if rotation_offset < 0:
        raise ValueError("rotation_offset must be nonnegative")
    eligible = tuple(
        index
        for index, cohort in enumerate(APPROVED_COHORTS)
        if budgets.get(cohort, 0) > 0 and buckets[cohort]
    )
    if not eligible or rotation_offset == 0:
        return cursor

    traversal = tuple(index for index in eligible if index >= cursor.cohort_index) + tuple(
        index for index in eligible if index < cursor.cohort_index
    )
    rounds, remainder = divmod(rotation_offset, len(traversal))
    offsets = list(cursor.candidate_offsets)
    for index in traversal:
        offsets[index] += rounds
    for index in traversal[:remainder]:
        offsets[index] += 1
    last_index = traversal[remainder - 1] if remainder else traversal[-1]
    return SchedulerCursor((last_index + 1) % len(APPROVED_COHORTS), tuple(offsets))


def schedule_candidates_detailed(
    candidates: Iterable[CohortCandidate],
    budget_by_cohort: Mapping[str, int],
    *,
    overall_cap: int | None = None,
    cursor: SchedulerCursor | None = None,
) -> SchedulerResult:
    """Select fairly and return stable state for a later, possibly different candidate set.

    The canonical cohort index advances only when a slot is selected. Each cohort's
    candidate offset is retained while that cohort is absent. Persist ``next_cursor``
    when candidate eligibility may change between windows.
    """

    budgets = _validate_budget(budget_by_cohort)
    _validate_cap(overall_cap)
    if cursor is None:
        cursor = SchedulerCursor()
    if not isinstance(cursor, SchedulerCursor):
        raise TypeError("cursor must be SchedulerCursor")

    budget_total = sum(budgets.values())
    limit = budget_total if overall_cap is None else min(overall_cap, budget_total)
    buckets = _candidate_buckets(candidates)
    selected: list[CohortCandidate] = []
    selected_identities: set[str] = set()
    used = dict.fromkeys(APPROVED_COHORTS, 0)
    offsets = list(cursor.candidate_offsets)
    cohort_index = cursor.cohort_index
    while len(selected) < limit:
        chosen_index: int | None = None
        chosen: CohortCandidate | None = None
        for distance in range(len(APPROVED_COHORTS)):
            index = (cohort_index + distance) % len(APPROVED_COHORTS)
            cohort = APPROVED_COHORTS[index]
            bucket = buckets[cohort]
            if used[cohort] >= budgets.get(cohort, 0) or not bucket:
                continue
            for candidate_distance in range(len(bucket)):
                candidate = bucket[(offsets[index] + candidate_distance) % len(bucket)]
                if candidate.identity not in selected_identities:
                    offsets[index] += candidate_distance + 1
                    chosen_index = index
                    chosen = candidate
                    break
            if chosen is not None:
                break
        if chosen_index is None or chosen is None:
            break
        selected.append(chosen)
        selected_identities.add(chosen.identity)
        used[APPROVED_COHORTS[chosen_index]] += 1
        cohort_index = (chosen_index + 1) % len(APPROVED_COHORTS)
    return SchedulerResult(tuple(selected), SchedulerCursor(cohort_index, tuple(offsets)))


def schedule_candidates(
    candidates: Iterable[CohortCandidate],
    budget_by_cohort: Mapping[str, int],
    *,
    overall_cap: int | None = None,
    rotation_offset: int = 0,
) -> list[CohortCandidate]:
    """Compatibility API using a stateless offset over deterministic candidate slots.

    Persist the cursor from :func:`schedule_candidates_detailed` instead when the
    eligible candidate set can change between windows; no integer offset can encode
    per-cohort progress across arbitrary changing sets.
    """

    budgets = _validate_budget(budget_by_cohort)
    _validate_cap(overall_cap)
    buckets = _candidate_buckets(candidates)
    cursor = _cursor_after_offset(SchedulerCursor(), buckets, budgets, rotation_offset)
    flattened = (candidate for bucket in buckets.values() for candidate in bucket)
    return list(
        schedule_candidates_detailed(
            flattened,
            budgets,
            overall_cap=overall_cap,
            cursor=cursor,
        ).selected
    )


@dataclass(frozen=True, slots=True)
class CohortConfig:
    """Strict, immutable cohort configuration; validation only, not a scoring engine."""

    cohort_id: str
    version: str
    business_scoring_overrides: Mapping[str, float]
    money_scoring_overrides: Mapping[str, float]
    required_evidence_categories: tuple[str, ...]
    conversion_intent_vocabulary: tuple[str, ...]


class CohortConfigError(ValueError):
    """Sanitized cohort contract error containing only the input filename."""


class _UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                None, None, "unhashable mapping key", key_node.start_mark
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                None, None, "duplicate mapping key", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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
    path = Path(path)
    if (
        path.suffix != ".yaml"
        or path.name != f"{path.stem}.yaml"
        or path.stem not in APPROVED_COHORTS
    ):
        raise CohortConfigError(f"invalid cohort filename: {path.name}")
    try:
        payload: Any = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeySafeLoader,
        )
        if not isinstance(payload, dict) or set(payload) != COHORT_CONFIG_KEYS:
            raise ValueError("invalid top-level keys")
        cohort_id = payload["cohort_id"]
        if cohort_id not in APPROVED_COHORTS or cohort_id != path.stem:
            raise ValueError("cohort_id mismatch")
        if payload["version"] != "cohort-v1":
            raise ValueError("unsupported cohort version")
        return CohortConfig(
            cohort_id=cohort_id,
            version=payload["version"],
            business_scoring_overrides=_strict_override_mapping(
                payload["business_scoring_overrides"],
                field="business_scoring_overrides",
                exact_keys=BUSINESS_SCORING_DIMENSIONS[cohort_id],
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
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError) as exc:
        if isinstance(exc, CohortConfigError):
            raise
        raise CohortConfigError(f"invalid cohort config: {path.name}") from None
