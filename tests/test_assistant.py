"""End-to-end orchestration with a fake eSchool, covering each outcome in the brief."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from eschool_assistant.assistant import MAX_QUERY_CHARS, Assistant
from eschool_assistant.eschool.client import SearchUnavailable
from eschool_assistant.eschool.parser import SearchPage, UnexpectedResponse
from eschool_assistant.interpreter.base import InterpretationError
from eschool_assistant.models import (
    Accommodation,
    AdmissionStatus,
    Clarification,
    Gender,
    Importance,
    Level,
    MatchStatus,
    Outcome,
    ParentRequest,
)


@dataclass
class FakeInterpreter:
    result: ParentRequest | Exception
    name: str = "fake"

    def interpret(self, query):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class FakeClient:
    """Answers searches through `rule(params) -> schools`; profiles via `profiles[slug]`."""

    rule: object
    profiles: dict = field(default_factory=dict)
    searches: list = field(default_factory=list)
    fail_profiles: bool = False

    def search_url(self, params):
        return f"https://test/search?{params}"

    def search(self, params):
        self.searches.append(dict(params))
        schools = self.rule(dict(params))
        return SearchPage(
            total=len(schools),
            schools=schools,
            districts=["KAMPALA", "WAKISO"],
            counties=["KAMPALA CENTRAL DIVISION", "NAKAWA DIVISION"],
        )

    def load_profile(self, school):
        if self.fail_profiles:
            raise SearchUnavailable("down")
        return self.profiles.get(school.slug, school)


def parent(**values) -> ParentRequest:
    return ParentRequest(is_school_search=True, **values)


def assistant(request, client, fallback=None) -> Assistant:
    return Assistant(FakeInterpreter(request), client, fallback=fallback)


@pytest.fixture
def girls_kampala():
    return parent(
        district="Kampala",
        gender=Gender.GIRLS,
        accommodation=Accommodation.BOARDING,
        levels=[Level.SECONDARY],
        max_fees_ugx=1_000_000,
    )


def test_clear_request_returns_verified_match(make_school, girls_kampala):
    school = make_school()
    client = FakeClient(lambda p: [school], {school.slug: school})
    response = assistant(girls_kampala, client).handle("girls boarding in Kampala")
    assert response.outcome == Outcome.MATCHES
    assert response.matches[0].fully_verified
    assert response.applied_filters["location"] == "KAMPALA"
    assert "day/boarding" not in response.not_specified
    assert "admission status" in response.not_specified


def test_schools_are_deduplicated_across_split_requests(make_school, girls_kampala):
    school = make_school()
    client = FakeClient(lambda p: [school], {school.slug: school})
    response = assistant(girls_kampala, client).handle("q")
    assert [m.school.slug for m in response.matches] == [school.slug]


def test_admission_filter_relaxed_and_national_zero_is_explained(make_school):
    closed = make_school()
    request = parent(district="Kampala", admission_status=AdmissionStatus.OPEN)

    def rule(params):
        return [] if params.get("admission_status") == "open" else [closed]

    response = assistant(request, FakeClient(rule, {closed.slug: closed})).handle("q")
    assert response.outcome == Outcome.NEAR_MATCHES
    assert "admission status" in response.message
    assert any("no school in Uganda" in notice for notice in response.notices)
    assert response.matches[0].checks[-1].status == MatchStatus.NOT_MET


def test_filter_that_was_not_the_blocker_is_restored(make_school):
    school = make_school()
    request = parent(
        district="Kampala",
        area="Central",
        area_importance=Importance.PREFERRED,
        admission_status=AdmissionStatus.OPEN,
    )

    def rule(params):
        return [] if params.get("admission_status") == "open" else [school]

    response = assistant(request, FakeClient(rule, {school.slug: school})).handle("q")
    assert "area" not in response.message
    assert response.steps[-1].dropped == ["admission status"]


def test_no_results_keeps_essentials(make_school):
    request = parent(district="Kampala", gender=Gender.BOYS, levels=[Level.SECONDARY])
    client = FakeClient(lambda p: [])
    response = assistant(request, client).handle("q")
    assert response.outcome == Outcome.NO_RESULTS
    assert response.matches == []
    assert all(search.get("gender") == "boys" for search in client.searches[1:])


def test_over_budget_school_is_left_out_and_explained(make_school, girls_kampala):
    # Profile evidence outranks the search results, wherever a candidate came from.
    costly = make_school(name="Costly", slug="costly", fee_min_ugx=1_750_000, fee_max_ugx=1_750_000)
    fine = make_school(name="Fine", slug="fine", fee_min_ugx=None, fee_max_ugx=None)
    client = FakeClient(lambda p: [costly, fine], {"costly": costly, "fine": fine})
    response = assistant(girls_kampala, client).handle("q")
    assert [m.school.name for m in response.matches] == ["Fine"]
    assert any("Costly" in notice for notice in response.notices)


def test_schools_without_published_fees_are_not_hidden(make_school, girls_kampala):
    filtered = make_school(name="A", slug="a")
    unpublished = make_school(name="B", slug="b", fee_min_ugx=None, fee_max_ugx=None)

    def rule(params):
        return [filtered] if "max_fees" in params else [filtered, unpublished]

    client = FakeClient(rule, {"a": filtered, "b": unpublished})
    response = assistant(girls_kampala, client).handle("q")
    assert {m.school.name for m in response.matches} == {"A", "B"}
    b = next(m for m in response.matches if m.school.name == "B")
    assert b.checks[1].status == MatchStatus.UNKNOWN


def test_fee_filtered_schools_are_checked_before_unpublished_ones(make_school, girls_kampala):
    published = make_school(name="Zed", slug="zed", rating=1.0)
    others = [
        make_school(name=f"S{i}", slug=f"s{i}", rating=5.0, fee_min_ugx=None, fee_max_ugx=None)
        for i in range(12)
    ]

    def rule(params):
        return [published] if "max_fees" in params else [published, *others]

    client = FakeClient(rule, {"zed": published})
    response = assistant(girls_kampala, client).handle("q")
    assert "Zed" in {m.school.name for m in response.matches}


def test_other_districts_returned_by_kampala_filter_are_flagged(make_school):
    local = make_school(name="Local", slug="local")
    outside = make_school(name="Outside", slug="outside", district="Wakiso")
    client = FakeClient(lambda p: [outside, local], {"local": local, "outside": outside})
    response = assistant(parent(district="Kampala"), client).handle("q")
    assert [m.school.name for m in response.matches] == ["Local"]
    assert any("Wakiso" in notice for notice in response.notices)


def test_only_other_districts_is_a_near_match_not_a_match(make_school):
    outside = make_school(slug="outside", district="Wakiso")
    client = FakeClient(lambda p: [outside], {"outside": outside})
    response = assistant(parent(district="Kampala"), client).handle("q")
    assert response.outcome == Outcome.NEAR_MATCHES
    assert response.matches[0].has_conflict


def test_the_models_questions_replace_the_general_one(make_school):
    questions = [Clarification(field="district", question="Which district?")]
    client = FakeClient(lambda p: [make_school()])
    response = assistant(parent(clarifying_questions=questions), client).handle("q")
    assert response.outcome == Outcome.NEEDS_CLARIFICATION
    assert response.clarifying_questions == questions
    assert response.message == "I need a little more detail."


def test_a_specific_problem_stays_the_lead_question(make_school):
    questions = [Clarification(field="district", question="Which district?")]
    client = FakeClient(lambda p: [make_school()])
    request = parent(min_fees_ugx=5, max_fees_ugx=1, clarifying_questions=questions)
    response = assistant(request, client).handle("q")
    assert "above your maximum" in response.message
    assert len(response.clarifying_questions) == 2


def test_unknown_district_asks_rather_than_searching(make_school):
    client = FakeClient(lambda p: [make_school()])
    response = assistant(parent(district="Atlantis"), client).handle("q")
    assert response.outcome == Outcome.NO_RESULTS
    assert "Atlantis" in response.message
    assert response.clarifying_questions[0].field == "district"
    assert len(client.searches) == 1


def test_unresolved_area_widens_openly(make_school):
    school = make_school()
    client = FakeClient(lambda p: [school], {school.slug: school})
    response = assistant(parent(district="Kampala", area="Atlantis"), client).handle("q")
    assert any("Atlantis" in notice for notice in response.notices)
    assert "county" not in response.applied_filters


def test_empty_query_never_reaches_the_interpreter():
    bomb = FakeInterpreter(AssertionError("should not be called"))
    response = Assistant(bomb, FakeClient(lambda p: [])).handle("   ")
    assert response.outcome == Outcome.NEEDS_CLARIFICATION


def test_overlong_query_is_rejected():
    bomb = FakeInterpreter(AssertionError("should not be called"))
    response = Assistant(bomb, FakeClient(lambda p: [])).handle("x" * (MAX_QUERY_CHARS + 1))
    assert response.outcome == Outcome.NEEDS_CLARIFICATION


def test_vague_request_asks_before_searching():
    client = FakeClient(lambda p: [])
    questions = [Clarification(field="location", question="Where?")]
    response = assistant(parent(clarifying_questions=questions), client).handle("a good school")
    assert response.outcome == Outcome.NEEDS_CLARIFICATION
    assert client.searches == []


def test_contradictory_fees_ask_before_searching():
    client = FakeClient(lambda p: [])
    request = parent(district="Kampala", min_fees_ugx=2_000_000, max_fees_ugx=1_000_000)
    response = assistant(request, client).handle("q")
    assert response.outcome == Outcome.NEEDS_CLARIFICATION
    assert client.searches == []


def test_partial_request_searches_and_still_asks(make_school):
    school = make_school()
    client = FakeClient(lambda p: [school], {school.slug: school})
    questions = [Clarification(field="level", question="Which class?")]
    request = parent(district="Kampala", clarifying_questions=questions)
    response = assistant(request, client).handle("q")
    assert response.outcome == Outcome.MATCHES
    assert response.clarifying_questions == questions


def test_unsupported_preference_is_reported(make_school):
    school = make_school()
    client = FakeClient(lambda p: [school], {school.slug: school})
    request = parent(district="Kampala", unsupported_preferences=["best UCE results"])
    response = assistant(request, client).handle("q")
    assert any("best UCE results" in notice for notice in response.notices)


def test_off_topic_message_skips_search():
    client = FakeClient(lambda p: [])
    response = assistant(ParentRequest(is_school_search=False), client).handle("weather?")
    assert response.outcome == Outcome.NOT_A_SCHOOL_SEARCH
    assert client.searches == []


@pytest.mark.parametrize("error", [SearchUnavailable("down"), UnexpectedResponse("layout changed")])
def test_search_failure_is_reported_not_shown_as_no_schools(error):
    def rule(params):
        raise error

    response = assistant(parent(district="Kampala"), FakeClient(rule)).handle("q")
    assert response.outcome == Outcome.SEARCH_FAILED
    assert response.matches == []


def test_profile_outage_degrades_to_unconfirmed(make_school):
    school = make_school(profile_loaded=False, gender=None)
    client = FakeClient(lambda p: [school], fail_profiles=True)
    response = assistant(parent(district="Kampala", gender=Gender.GIRLS), client).handle("q")
    assert response.outcome == Outcome.MATCHES
    assert not response.matches[0].fully_verified
    assert any("could not be loaded" in notice for notice in response.notices)


def test_ai_failure_falls_back_and_says_so(make_school):
    school = make_school()
    client = FakeClient(lambda p: [school], {school.slug: school})
    primary = FakeInterpreter(InterpretationError("The AI service is busy."))
    backup = FakeInterpreter(parent(district="Kampala"), name="backup")
    response = Assistant(primary, client, fallback=backup).handle("q")
    assert response.interpreter == "backup"
    assert any("keyword" in notice for notice in response.notices)


def test_ai_failure_without_fallback_is_reported():
    primary = FakeInterpreter(InterpretationError("The AI service is busy."))
    response = Assistant(primary, FakeClient(lambda p: [])).handle("q")
    assert response.outcome == Outcome.SEARCH_FAILED


def test_large_result_sets_are_disclosed(make_school):
    schools = [make_school(name=f"S{i}", slug=f"s{i}") for i in range(24)]

    class Paged(FakeClient):
        def search(self, params):
            page = super().search(params)
            page.total = 500
            return page

    response = assistant(parent(district="Kampala"), Paged(lambda p: schools)).handle("q")
    assert any("500" in notice for notice in response.notices)
    assert len(response.matches) <= 8
