"""Deterministic cohort assignment, allocation, and candidate scheduling."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

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

# This order is the explicit overlap precedence. Specific professional and
# operational signals precede broad retail/ecommerce language.
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


@dataclass(frozen=True, slots=True)
class CohortAssignment:
    cohort: str
    deterministic: bool
    claim_status: ClaimStatus
    evidence_keywords: tuple[str, ...]


def assign_cohort(
    *,
    industry_hint: str | None = None,
    name: str | None = None,
    description: str | None = None,
    ai_suggestion: str | None = None,
    ai_claim_status: ClaimStatus | None = None,
) -> CohortAssignment:
    """Assign one approved cohort from observed text, with a gated AI fallback."""

    corpus = " ".join(
        filter(None, (_normalize(industry_hint), _normalize(name), _normalize(description)))
    )
    for cohort, keywords in _KEYWORDS:
        matched = tuple(keyword for keyword in keywords if keyword in corpus)
        if matched:
            return CohortAssignment(cohort, True, ClaimStatus.OBSERVED, matched)
    if ai_claim_status is ClaimStatus.INFERRED and ai_suggestion in APPROVED_COHORTS:
        return CohortAssignment(ai_suggestion, False, ClaimStatus.INFERRED, ())
    return CohortAssignment("other", True, ClaimStatus.OBSERVED, ())


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
    """Minimal stable candidate identity and its assigned cohort."""

    identity: str
    cohort: str = "other"

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise ValueError("candidate identity must be a nonblank string")
        if not isinstance(self.cohort, str) or not self.cohort.strip():
            raise ValueError("candidate cohort must be a nonblank string")
        object.__setattr__(self, "identity", self.identity.strip())
        object.__setattr__(self, "cohort", self.cohort.strip())


def _validate_budget(budget_by_cohort: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for raw_cohort, budget in budget_by_cohort.items():
        if not isinstance(raw_cohort, str) or not raw_cohort.strip():
            raise ValueError("budget cohort names must be nonblank strings")
        if isinstance(budget, bool) or not isinstance(budget, int):
            raise TypeError("cohort budgets must be integers")
        if budget < 0:
            raise ValueError("cohort budgets must be nonnegative")
        cohort = raw_cohort.strip()
        if cohort in result:
            raise ValueError("normalized budget cohort names must be unique")
        result[cohort] = budget
    return result


def schedule_candidates(
    candidates: Iterable[CohortCandidate],
    budget_by_cohort: Mapping[str, int],
    *,
    overall_cap: int | None = None,
) -> list[CohortCandidate]:
    """Select candidates by stable cohort round-robin within budgets and an optional cap."""

    budgets = _validate_budget(budget_by_cohort)
    if overall_cap is not None:
        if isinstance(overall_cap, bool) or not isinstance(overall_cap, int):
            raise TypeError("overall_cap must be an integer")
        if overall_cap < 0:
            raise ValueError("overall_cap must be nonnegative")
    limit = (
        sum(budgets.values()) if overall_cap is None else min(overall_cap, sum(budgets.values()))
    )
    buckets: dict[str, list[CohortCandidate]] = {cohort: [] for cohort in budgets}
    queued_identities: set[str] = set()
    for candidate in candidates:
        if candidate.identity in queued_identities:
            continue
        cohort = candidate.cohort if candidate.cohort in budgets else "other"
        if cohort not in buckets:
            continue
        queued_identities.add(candidate.identity)
        buckets[cohort].append(
            candidate if candidate.cohort == cohort else CohortCandidate(candidate.identity, cohort)
        )
    selected: list[CohortCandidate] = []
    used = {cohort: 0 for cohort in budgets}
    offsets = {cohort: 0 for cohort in budgets}
    while len(selected) < limit:
        progressed = False
        for cohort in budgets:
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
