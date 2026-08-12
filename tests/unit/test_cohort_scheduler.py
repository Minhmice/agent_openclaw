from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from openclaw_web import discovery
from openclaw_web.discovery.scheduler import (
    APPROVED_COHORTS,
    CohortCandidate,
    CohortConfigError,
    SchedulerCursor,
    SchedulerResult,
    allocate_cohort_budget,
    assign_cohort,
    load_cohort_config,
    schedule_candidates,
    schedule_candidates_detailed,
)
from openclaw_web.models import ClaimStatus


def test_equal_cohort_budget_is_deterministic() -> None:
    cohorts = ["manufacturer", "local-service", "ecommerce", "other"]
    assert allocate_cohort_budget(cohorts, 10) == {
        "manufacturer": 3,
        "local-service": 3,
        "ecommerce": 2,
        "other": 2,
    }


def test_budget_edge_cases_and_validation() -> None:
    assert allocate_cohort_budget([], 4) == {}
    assert allocate_cohort_budget(["a", "b", "c"], 2) == {"a": 1, "b": 1, "c": 0}
    assert allocate_cohort_budget(["a"], 0) == {"a": 0}
    for cohorts, total in [(["a"], -1), (["a"], True), (["", "b"], 2), (["a", "a"], 2)]:
        with pytest.raises((TypeError, ValueError)):
            allocate_cohort_budget(cohorts, total)  # type: ignore[arg-type]


def test_approved_cohorts_have_exact_stable_order() -> None:
    assert APPROVED_COHORTS == (
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


def test_keyword_assignment_is_normalized_and_precedence_wins_overlap() -> None:
    result = assign_cohort(
        name="CÔNG TY luật & thương mại điện tử",
        description="Tư vấn pháp lý và bán hàng online",
    )
    assert result.cohort == "professional-services"
    assert result.deterministic is True
    assert result.claim_status is ClaimStatus.OBSERVED
    assert "luat" in result.evidence_keywords
    with pytest.raises(FrozenInstanceError):
        result.cohort = "other"  # type: ignore[misc]


def test_hospitality_keyword_does_not_match_healthcare_hospital_substring() -> None:
    result = assign_cohort(industry_hint="hospitality")

    assert result.cohort == "hospitality"
    assert result.evidence_keywords == ("hospitality",)


@pytest.mark.parametrize("cohort", APPROVED_COHORTS)
def test_exact_approved_industry_hint_maps_to_itself(cohort: str) -> None:
    result = assign_cohort(industry_hint=cohort)

    assert result.cohort == cohort
    assert result.deterministic is True
    assert result.claim_status is ClaimStatus.OBSERVED
    assert result.evidence_keywords == (cohort,)
    assert result.evidence_fields == ("industry_hint",)


def test_exact_name_and_description_use_keyword_precedence() -> None:
    result = assign_cohort(industry_hint="factory", description="ecommerce")
    assert result.cohort == "manufacturer"
    assert result.evidence_keywords == ("factory",)
    assert result.evidence_fields == ("industry_hint",)


@pytest.mark.parametrize(
    ("name", "description"),
    (
        ("Acme online", "shop solutions"),
        ("Surreal real", "estate photography"),
        ("Phong", "Kham Media"),
    ),
)
def test_multiword_keywords_do_not_span_classification_fields(name: str, description: str) -> None:
    result = assign_cohort(name=name, description=description)

    assert result.cohort == "other"
    assert result.evidence_keywords == ()


@pytest.mark.parametrize(
    ("phrase", "cohort", "keyword"),
    (
        ("Premium online shop platform", "ecommerce", "online shop"),
        ("Premium real estate listings", "real-estate", "real estate"),
        ("Dich vu phong kham", "healthcare", "phong kham"),
    ),
)
@pytest.mark.parametrize("field", ("industry_hint", "name", "description"))
def test_multiword_keyword_matches_within_each_classification_field(
    field: str, phrase: str, cohort: str, keyword: str
) -> None:
    result = assign_cohort(**{field: phrase})

    assert result.cohort == cohort
    assert result.evidence_keywords == (keyword,)
    assert result.evidence_fields == (field,)


def test_cohort_precedence_wins_when_keywords_match_in_different_fields() -> None:
    result = assign_cohort(
        industry_hint="online shop",
        name="Acme legal",
        description="factory equipment",
    )

    assert result.cohort == "manufacturer"
    assert result.evidence_keywords == ("factory",)
    assert result.evidence_fields == ("description",)


@pytest.mark.parametrize(
    "text",
    (
        "hospitality suite",
        "space planning",
        "microfactory tooling",
        "surreal estate photography",
    ),
)
def test_keywords_do_not_match_inside_longer_words(text: str) -> None:
    expected = "hospitality" if text == "hospitality suite" else "other"
    assert assign_cohort(description=text).cohort == expected


@pytest.mark.parametrize(
    ("text", "cohort", "keyword"),
    (
        ("Dịch vụ tư-vấn pháp lý", "professional-services", "tu van"),
        ("Nền tảng THƯƠNG-MẠI, ĐIỆN.TỬ", "ecommerce", "thuong mai dien tu"),
        ("Dự án bất_động/sản", "real-estate", "bat dong san"),
        ("Premium real...estate listings", "real-estate", "real estate"),
    ),
)
def test_multiword_keywords_match_across_punctuation_and_accents(
    text: str, cohort: str, keyword: str
) -> None:
    result = assign_cohort(description=text)

    assert result.cohort == cohort
    assert keyword in result.evidence_keywords


def test_deterministic_match_beats_ai_and_ai_requires_inferred_status() -> None:
    deterministic = assign_cohort(
        industry_hint="nhà máy sản xuất",
        ai_suggestion="ecommerce",
        ai_claim_status=ClaimStatus.INFERRED,
    )
    accepted_ai = assign_cohort(
        name="Acme",
        ai_suggestion="education",
        ai_claim_status=ClaimStatus.INFERRED,
    )
    rejected_ai = assign_cohort(
        name="Acme",
        ai_suggestion="education",
        ai_claim_status=ClaimStatus.UNVERIFIED,
    )
    invalid_ai = assign_cohort(
        name="Acme", ai_suggestion="made-up", ai_claim_status=ClaimStatus.INFERRED
    )
    assert deterministic.cohort == "manufacturer"
    assert accepted_ai.cohort == "education"
    assert accepted_ai.deterministic is False
    assert accepted_ai.claim_status is ClaimStatus.INFERRED
    assert rejected_ai.cohort == invalid_ai.cohort == "other"
    assert rejected_ai.claim_status is ClaimStatus.UNVERIFIED
    assert rejected_ai.reason == "no_deterministic_or_inferred_cohort_evidence"


def test_other_is_observed_only_when_explicitly_present() -> None:
    observed = assign_cohort(name="other")
    fallback = assign_cohort(name="Acme")
    assert observed.claim_status is ClaimStatus.OBSERVED
    assert observed.evidence_fields == ("name",)
    assert fallback.claim_status is ClaimStatus.UNVERIFIED


def test_scheduler_round_robins_stably_with_caps_deduplication_and_fallback() -> None:
    candidates = [
        CohortCandidate("m1", "manufacturer"),
        CohortCandidate("m2", "manufacturer"),
        CohortCandidate("e1", "ecommerce"),
        CohortCandidate("m1", "ecommerce"),
        CohortCandidate("x1", "unknown"),
        CohortCandidate("e2", "ecommerce"),
    ]
    scheduled = schedule_candidates(
        candidates,
        {"manufacturer": 2, "ecommerce": 2, "other": 1},
        overall_cap=4,
    )
    assert [item.identity for item in scheduled] == ["m1", "e1", "x1", "m2"]
    assert scheduled[2].cohort == "other"
    assert schedule_candidates(candidates, {"manufacturer": 0, "other": 0}) == []


def test_scheduler_rejects_invalid_budgets_and_cap() -> None:
    candidate = [CohortCandidate("a", "other")]
    for budgets, cap in [({"other": -1}, None), ({"other": True}, None), ({"": 1}, None)]:
        with pytest.raises((TypeError, ValueError)):
            schedule_candidates(candidate, budgets, overall_cap=cap)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        schedule_candidates(candidate, {"other": 1}, overall_cap=-1)
    with pytest.raises(ValueError, match="approved"):
        schedule_candidates(candidate, {"alien": 1})
    with pytest.raises((TypeError, ValueError)):
        schedule_candidates(candidate, {"other": 1}, rotation_offset=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("budgets", ("other", [("other", 1)], None))
def test_scheduler_apis_require_budget_mapping(budgets: object) -> None:
    candidates = [CohortCandidate("a", "other")]

    with pytest.raises(TypeError, match="budget_by_cohort must be a mapping"):
        schedule_candidates(candidates, budgets)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="budget_by_cohort must be a mapping"):
        schedule_candidates_detailed(candidates, budgets)  # type: ignore[arg-type]


@pytest.mark.parametrize("candidates", ("candidate", b"candidate", bytearray(b"candidate"), 1))
def test_scheduler_apis_require_candidate_iterable(candidates: object) -> None:
    budgets = {"other": 1}

    with pytest.raises(TypeError, match="candidates must be a non-string iterable"):
        schedule_candidates(candidates, budgets)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="candidates must be a non-string iterable"):
        schedule_candidates_detailed(candidates, budgets)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", ("candidate", object(), None))
def test_scheduler_apis_validate_every_candidate_before_attribute_access(invalid: object) -> None:
    candidates = [CohortCandidate("a", "other"), invalid]
    budgets = {"other": 1}

    with pytest.raises(TypeError, match="contain only CohortCandidate"):
        schedule_candidates(candidates, budgets)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="contain only CohortCandidate"):
        schedule_candidates_detailed(candidates, budgets)  # type: ignore[arg-type]


def test_candidate_unknown_cohort_is_normalized_to_other() -> None:
    assert CohortCandidate("x", "alien").cohort == "other"


def test_scheduler_order_is_canonical_not_mapping_insertion_order() -> None:
    candidates = [
        CohortCandidate("m", "manufacturer"),
        CohortCandidate("e", "ecommerce"),
        CohortCandidate("o", "other"),
    ]
    canonical = {"manufacturer": 1, "ecommerce": 1, "other": 1}
    reversed_budget = {"other": 1, "ecommerce": 1, "manufacturer": 1}
    assert schedule_candidates(candidates, canonical) == schedule_candidates(
        candidates, reversed_budget
    )


def test_scheduler_deduplication_is_independent_of_candidate_input_order() -> None:
    candidates = [
        CohortCandidate("duplicate", "ecommerce"),
        CohortCandidate("other", "other"),
        CohortCandidate("duplicate", "manufacturer"),
    ]
    budgets = {"manufacturer": 1, "ecommerce": 1, "other": 1}
    assert schedule_candidates(candidates, budgets) == schedule_candidates(
        reversed(candidates), budgets
    )


def test_rotation_offset_distributes_overall_cap_without_starvation() -> None:
    candidates = [CohortCandidate(cohort, cohort) for cohort in APPROVED_COHORTS]
    budgets = dict.fromkeys(APPROVED_COHORTS, 1)
    selected = [
        schedule_candidates(candidates, budgets, overall_cap=1, rotation_offset=offset)[0].cohort
        for offset in range(len(APPROVED_COHORTS))
    ]
    assert selected == list(APPROVED_COHORTS)


def test_sparse_scheduler_rotates_only_across_eligible_nonempty_cohorts() -> None:
    candidates = [
        CohortCandidate("o1", "other"),
        CohortCandidate("m1", "manufacturer"),
        CohortCandidate("o2", "other"),
        CohortCandidate("m2", "manufacturer"),
    ]
    budgets = dict.fromkeys(APPROVED_COHORTS, 2)
    assert [
        schedule_candidates(candidates, budgets, overall_cap=1, rotation_offset=offset)[0].cohort
        for offset in range(4)
    ] == ["manufacturer", "other", "manufacturer", "other"]
    assert schedule_candidates(candidates, budgets, overall_cap=3, rotation_offset=1) == [
        CohortCandidate("o1", "other"),
        CohortCandidate("m2", "manufacturer"),
        CohortCandidate("o2", "other"),
    ]
    assert schedule_candidates(candidates, budgets, overall_cap=3) == schedule_candidates(
        [candidates[1], candidates[0], candidates[3], candidates[2]],
        dict(reversed(tuple(budgets.items()))),
        overall_cap=3,
    )


def test_rotation_offset_advances_candidate_slots_within_one_cohort() -> None:
    candidates = [CohortCandidate(identity, "manufacturer") for identity in ("a", "b", "c")]
    assert [
        schedule_candidates(
            candidates,
            {"manufacturer": 1},
            overall_cap=1,
            rotation_offset=offset,
        )[0].identity
        for offset in range(6)
    ] == ["a", "b", "c", "a", "b", "c"]


def test_persisted_scheduler_cursor_survives_changing_eligible_cohorts() -> None:
    cursor = SchedulerCursor()
    first = schedule_candidates_detailed(
        [CohortCandidate("m-a", "manufacturer"), CohortCandidate("m-b", "manufacturer")],
        {"manufacturer": 1, "ecommerce": 1},
        overall_cap=1,
        cursor=cursor,
    )
    second = schedule_candidates_detailed(
        [CohortCandidate("e-a", "ecommerce")],
        {"manufacturer": 1, "ecommerce": 1},
        overall_cap=1,
        cursor=first.next_cursor,
    )
    third = schedule_candidates_detailed(
        [CohortCandidate("m-a", "manufacturer"), CohortCandidate("m-b", "manufacturer")],
        {"manufacturer": 1, "ecommerce": 1},
        overall_cap=1,
        cursor=second.next_cursor,
    )

    assert [
        first.selected[0].identity,
        second.selected[0].identity,
        third.selected[0].identity,
    ] == [
        "m-a",
        "e-a",
        "m-b",
    ]
    assert first.next_cursor != cursor


def _run_single_cohort_window(
    identities: tuple[str, ...], cursor: SchedulerCursor
) -> SchedulerResult:
    return schedule_candidates_detailed(
        [CohortCandidate(identity, "manufacturer") for identity in identities],
        {"manufacturer": 1},
        overall_cap=1,
        cursor=cursor,
    )


def test_scheduler_cursor_uses_identity_anchor_when_candidate_is_inserted_before_it() -> None:
    first = _run_single_cohort_window(("b", "c"), SchedulerCursor())
    second = _run_single_cohort_window(("a", "b", "c"), first.next_cursor)

    assert [item.identity for item in first.selected] == ["b"]
    assert [item.identity for item in second.selected] == ["c"]


def test_scheduler_cursor_uses_identity_anchor_when_candidate_before_it_is_removed() -> None:
    first = _run_single_cohort_window(("a", "b", "c"), SchedulerCursor())
    second = _run_single_cohort_window(("b", "c"), first.next_cursor)

    assert [item.identity for item in first.selected + second.selected] == ["a", "b"]


def test_scheduler_cursor_uses_lexical_successor_when_anchor_is_removed() -> None:
    anchor_index = APPROVED_COHORTS.index("manufacturer")
    anchors: list[str | None] = [None] * len(APPROVED_COHORTS)
    anchors[anchor_index] = "b"

    result = _run_single_cohort_window(("a", "c", "d"), SchedulerCursor(0, anchors))

    assert [item.identity for item in result.selected] == ["c"]


def test_scheduler_cursor_wraps_and_same_identity_can_return() -> None:
    cursor = SchedulerCursor()
    selected: list[str] = []
    for identities in (("a", "b"), ("a", "b"), ("a", "b"), ("b",), ("a", "b")):
        result = _run_single_cohort_window(identities, cursor)
        selected.append(result.selected[0].identity)
        cursor = result.next_cursor

    assert selected == ["a", "b", "a", "b", "a"]


def test_scheduler_cursor_selects_returning_identity_after_temporary_absence() -> None:
    first = _run_single_cohort_window(("a", "b"), SchedulerCursor())
    second = _run_single_cohort_window(("a", "b"), first.next_cursor)
    absent = _run_single_cohort_window(("a",), second.next_cursor)
    returned = _run_single_cohort_window(("a", "b"), absent.next_cursor)

    assert [result.selected[0].identity for result in (first, second, absent, returned)] == [
        "a",
        "b",
        "a",
        "b",
    ]


def test_scheduler_cursor_and_result_deeply_freeze_iterable_inputs() -> None:
    source_anchors: list[str | None] = [None] * len(APPROVED_COHORTS)
    source_anchors[0] = "m-a"
    cursor = SchedulerCursor(1, source_anchors)
    source_selected = [CohortCandidate("e-a", "ecommerce")]
    result = SchedulerResult(source_selected, cursor)

    source_anchors[0] = "changed"
    source_selected.append(CohortCandidate("e-b", "ecommerce"))

    assert cursor.candidate_anchors[0] == "m-a"
    assert result.selected == (CohortCandidate("e-a", "ecommerce"),)
    assert hash(cursor)
    assert hash(result)


def test_scheduler_cursor_accepts_json_like_anchor_list() -> None:
    anchors: list[str | None] = [None] * len(APPROVED_COHORTS)
    anchors[0] = "  manufacturer-anchor  "

    cursor = SchedulerCursor(candidate_anchors=anchors)

    assert cursor.candidate_anchors == ("manufacturer-anchor",) + (None,) * (
        len(APPROVED_COHORTS) - 1
    )


@pytest.mark.parametrize("anchors", ("anchors", b"anchors", bytearray(b"anchors")))
def test_scheduler_cursor_rejects_string_like_anchor_sequences(anchors: object) -> None:
    with pytest.raises(TypeError, match="candidate_anchors must be a non-string iterable"):
        SchedulerCursor(candidate_anchors=anchors)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "anchors",
    (
        [None] * (len(APPROVED_COHORTS) - 1),
        [None] * (len(APPROVED_COHORTS) - 1) + [""],
        [None] * (len(APPROVED_COHORTS) - 1) + [1],
    ),
)
def test_scheduler_cursor_rejects_invalid_anchor_sequences(anchors: list[object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        SchedulerCursor(candidate_anchors=anchors)  # type: ignore[arg-type]


@pytest.mark.parametrize("cohort_index", (True, -1, len(APPROVED_COHORTS), 1.5))
def test_scheduler_cursor_rejects_invalid_cohort_index(cohort_index: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        SchedulerCursor(cohort_index=cohort_index)  # type: ignore[arg-type]


def test_scheduler_result_rejects_non_candidates_and_invalid_cursor() -> None:
    with pytest.raises(TypeError):
        SchedulerResult(["not-a-candidate"], SchedulerCursor())  # type: ignore[list-item]
    with pytest.raises(TypeError):
        SchedulerResult([], object())  # type: ignore[arg-type]


@pytest.mark.parametrize("selected", ("candidate", b"candidate", bytearray(b"candidate")))
def test_scheduler_result_rejects_string_like_selected_sequences(selected: object) -> None:
    with pytest.raises(TypeError, match="selected candidates must be a non-string iterable"):
        SchedulerResult(selected, SchedulerCursor())  # type: ignore[arg-type]


def test_discovery_facade_exports_exact_public_api() -> None:
    assert discovery.__all__ == [
        "APPROVED_COHORTS",
        "CohortAssignment",
        "CohortCandidate",
        "CohortConfig",
        "SchedulerCursor",
        "SchedulerResult",
        "allocate_cohort_budget",
        "assign_cohort",
        "load_cohort_config",
        "schedule_candidates",
        "schedule_candidates_detailed",
    ]
    assert discovery.SchedulerCursor is SchedulerCursor
    assert discovery.SchedulerResult is SchedulerResult
    assert discovery.schedule_candidates_detailed is schedule_candidates_detailed


def test_all_cohort_yaml_contracts_are_strict_and_complete() -> None:
    root = Path(__file__).parents[2] / "config" / "scoring" / "cohorts"
    files = sorted(root.glob("*.yaml"))
    assert {path.stem for path in files} == set(APPROVED_COHORTS)
    for path in files:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(payload) == {
            "cohort_id",
            "version",
            "business_scoring_overrides",
            "money_scoring_overrides",
            "required_evidence_categories",
            "conversion_intent_vocabulary",
        }
        assert payload["cohort_id"] == path.stem
        assert payload["version"] == "cohort-v1"
        assert isinstance(payload["business_scoring_overrides"], dict)
        assert payload["business_scoring_overrides"]
        assert isinstance(payload["money_scoring_overrides"], dict)
        assert payload["money_scoring_overrides"]
        for key in ("required_evidence_categories", "conversion_intent_vocabulary"):
            values = payload[key]
            assert isinstance(values, list) and values
            assert all(isinstance(value, str) and value.strip() for value in values)
            assert len(values) == len(set(values))
        vocabulary = " ".join(payload["conversion_intent_vocabulary"])
        assert any(ord(character) > 127 for character in vocabulary)
        assert any(term in vocabulary.lower() for term in ("quote", "book", "buy", "contact"))
        config = load_cohort_config(path)
        assert config.cohort_id == path.stem
        assert set(config.money_scoring_overrides) == {
            "search_demand",
            "aov_ltv",
            "trust_dependency",
            "online_conversion_fit",
            "business_strength",
            "web_gap",
        }


@pytest.mark.parametrize(
    "mutation",
    (
        "extra: true\n",
        "",
    ),
)
def test_cohort_yaml_loader_rejects_extra_or_missing_top_level_keys(
    tmp_path: Path, mutation: str
) -> None:
    source = Path(__file__).parents[2] / "config/scoring/cohorts/manufacturer.yaml"
    text = source.read_text(encoding="utf-8")
    if mutation:
        text += mutation
    else:
        text = text.replace("required_evidence_categories:", "removed_categories:")
    path = tmp_path / "manufacturer.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_cohort_config(path)


def test_cohort_yaml_loader_rejects_nonfinite_out_of_bounds_and_duplicate_normalized_text(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parents[2] / "config/scoring/cohorts/manufacturer.yaml"
    text = source.read_text(encoding="utf-8")
    text = text.replace("search_demand: 1.00", "search_demand: .inf")
    text = text.replace(
        "required_evidence_categories: [products, facilities, certifications]",
        "required_evidence_categories: [products, ' PRODUCTS ', certifications]",
    )
    path = tmp_path / "manufacturer.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_cohort_config(path)


def test_cohort_yaml_loader_rejects_undefined_business_modifier(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "config/scoring/cohorts/manufacturer.yaml"
    text = source.read_text(encoding="utf-8").replace(
        "operational_scale_weight:", "undefined_business_key:"
    )
    path = tmp_path / "manufacturer.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CohortConfigError):
        load_cohort_config(path)


@pytest.mark.parametrize("nested", (False, True))
def test_cohort_yaml_loader_rejects_duplicate_keys_without_echoing_content(
    tmp_path: Path, nested: bool
) -> None:
    source = Path(__file__).parents[2] / "config/scoring/cohorts/manufacturer.yaml"
    text = source.read_text(encoding="utf-8")
    secret = "do-not-echo-this-value"
    if nested:
        text = text.replace(
            "  operational_scale_weight: 1.25",
            f"  operational_scale_weight: 1.25\n  operational_scale_weight: {secret}",
        )
    else:
        text += f"\ncohort_id: {secret}\n"
    path = tmp_path / "manufacturer.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(CohortConfigError) as error:
        load_cohort_config(path)
    assert path.name in str(error.value)
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    "filename",
    ("manufacturer.yml", "manufacturer.YAML", "manufacturer.yaml.txt", "manufacturer.test.yaml"),
)
def test_cohort_yaml_loader_requires_exact_approved_yaml_filename(
    tmp_path: Path, filename: str
) -> None:
    source = Path(__file__).parents[2] / "config/scoring/cohorts/manufacturer.yaml"
    path = tmp_path / filename
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(CohortConfigError):
        load_cohort_config(path)


def test_cohort_yaml_loader_rejects_made_up_weight_even_with_valid_suffix(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "config/scoring/cohorts/manufacturer.yaml"
    text = source.read_text(encoding="utf-8").replace(
        "  operational_scale_weight: 1.25", "  made_up_weight: 1.25"
    )
    path = tmp_path / "manufacturer.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CohortConfigError):
        load_cohort_config(path)


def test_cohort_yaml_loader_rejects_another_cohorts_known_business_dimension(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parents[2] / "config/scoring/cohorts/manufacturer.yaml"
    text = source.read_text(encoding="utf-8").replace(
        "operational_scale_weight", "authority_weight"
    )
    path = tmp_path / "manufacturer.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CohortConfigError) as error:
        load_cohort_config(path)
    assert error.value.__cause__ is None
