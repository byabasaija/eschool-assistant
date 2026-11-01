from eschool_assistant.models import Accommodation, Gender, Importance, Level, ParentRequest
from eschool_assistant.query import (
    Filters,
    build_filters,
    relaxation_order,
    resolve_area,
    to_param_sets,
)

KAMPALA_COUNTIES = [
    "KAWEMPE DIVISION SOUTH",
    "KAMPALA CENTRAL DIVISION",
    "KAWEMPE DIVISION NORTH",
    "MAKINDYE DIVISION EAST",
    "NAKAWA DIVISION",
    "MAKINDYE DIVISION WEST",
]


def request(**values) -> ParentRequest:
    return ParentRequest(is_school_search=True, **values)


def test_fees_are_sent_as_plain_digits():
    params = to_param_sets(Filters(max_fees=1_000_000))[0]
    assert ("max_fees", "1000000") in params


def test_boarding_expands_to_both_site_categories():
    filters = build_filters(request(accommodation=Accommodation.BOARDING), "KAMPALA", ())
    values = {dict(p)["accomadation"] for p in to_param_sets(filters)}
    assert values == {"Boarding", "Day & Boarding"}


def test_primary_includes_combined_nursery_and_primary_schools():
    filters = build_filters(request(levels=[Level.PRIMARY]), None, ())
    assert filters.levels == ("Primary", "Nursery & Primary")


def test_multi_value_filters_repeat_the_parameter():
    params = to_param_sets(Filters(levels=("Primary", "Secondary")))[0]
    assert params.count(("levels[]", "Primary")) == 1
    assert params.count(("levels[]", "Secondary")) == 1


def test_counties_and_accommodation_multiply():
    filters = Filters(counties=("A", "B"), accommodation=("Boarding", "Day & Boarding"))
    assert len(to_param_sets(filters)) == 4


def test_no_filters_means_one_plain_search():
    assert to_param_sets(Filters()) == [[]]


def test_resolve_division_by_partial_name():
    assert resolve_area("Central Division", [], KAMPALA_COUNTIES).counties == (
        "KAMPALA CENTRAL DIVISION",
    )


def test_resolve_area_spanning_two_counties():
    assert resolve_area("Makindye", [], KAMPALA_COUNTIES).counties == (
        "MAKINDYE DIVISION EAST",
        "MAKINDYE DIVISION WEST",
    )


def test_model_suggestion_is_used_only_if_the_site_knows_it():
    resolved = resolve_area("Ntinda", ["Nakawa Division"], KAMPALA_COUNTIES)
    assert resolved.counties == ("NAKAWA DIVISION",)
    assert resolved.via_assumption


def test_invented_county_is_rejected():
    resolved = resolve_area("Ntinda", ["Ntinda Division"], KAMPALA_COUNTIES)
    assert resolved.counties == ()
    assert resolved.unresolved == "Ntinda"


def test_preferred_filters_are_relaxed_first():
    parent = request(
        district="Kampala",
        area="Central",
        area_importance=Importance.PREFERRED,
        max_fees_ugx=1_000_000,
        gender=Gender.GIRLS,
    )
    filters = build_filters(parent, "KAMPALA", ("KAMPALA CENTRAL DIVISION",))
    labels = [item.label for item in relaxation_order(parent, filters)]
    assert labels == ["area", "fees"]


def test_required_gender_and_level_are_never_relaxed():
    parent = request(gender=Gender.BOYS, levels=[Level.SECONDARY])
    filters = build_filters(parent, "GULU", ())
    assert relaxation_order(parent, filters) == []


def test_preferred_gender_can_be_relaxed():
    parent = request(gender=Gender.GIRLS, gender_importance=Importance.PREFERRED)
    filters = build_filters(parent, "GULU", ())
    assert [item.label for item in relaxation_order(parent, filters)] == ["gender"]


def test_without_removes_only_named_filters():
    filters = Filters(district="KAMPALA", gender="girls", levels=("Secondary",))
    relaxed = filters.without("gender", "levels")
    assert relaxed == Filters(district="KAMPALA")
