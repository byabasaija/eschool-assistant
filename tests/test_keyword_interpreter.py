import pytest

from eschool_assistant.interpreter.keyword import KeywordInterpreter
from eschool_assistant.models import Accommodation, AdmissionStatus, Gender, Importance, Level

interpret = KeywordInterpreter().interpret
EXAMPLE = (
    "I'm looking for a girls' boarding school in Kampala, preferably around Central Division, "
    "charging no more than UGX 1 million per term and currently admitting Senior One students."
)


def test_brief_example():
    request = interpret(EXAMPLE)
    assert request.district == "Kampala"
    assert request.district_importance == Importance.REQUIRED
    assert request.area == "Central"
    assert request.area_importance == Importance.PREFERRED
    assert request.max_fees_ugx == 1_000_000
    assert request.min_fees_ugx is None
    assert request.gender == Gender.GIRLS
    assert request.accommodation == Accommodation.BOARDING
    assert request.levels == [Level.SECONDARY]
    assert request.admission_class == "Senior One"
    assert request.admission_status == AdmissionStatus.OPEN


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("boarding school under 800k", 800_000),
        ("school up to shs 1.5m", 1_500_000),
        ("school with a budget of 2,000,000", 2_000_000),
    ],
)
def test_fee_amounts(text, expected):
    assert interpret(text).max_fees_ugx == expected


def test_yearly_fees_become_per_term():
    request = interpret("secondary school under 3 million per year")
    assert request.max_fees_ugx == 1_000_000
    assert request.fee_period_stated == "year"
    assert request.assumptions


def test_mentioning_a_daughter_does_not_imply_girls_only():
    assert interpret("a primary school for my daughter in Wakiso").gender is None


def test_day_before_level_is_recognised():
    assert interpret("a mixed day primary school").accommodation == Accommodation.DAY


def test_unrecognised_place_is_kept_not_dropped():
    assert interpret("A primary school in Kitgum").district == "Kitgum"


def test_division_is_not_mistaken_for_a_district():
    request = interpret("A school in Nakawa Division")
    assert (request.district, request.area) == ("Kampala", "Nakawa")


def test_unspecified_stays_empty():
    request = interpret("a school in Mukono")
    assert request.gender is None
    assert request.accommodation is None
    assert request.levels == []
    assert request.max_fees_ugx is None
    assert request.admission_status is None


def test_vague_budget_asks_instead_of_guessing():
    request = interpret("an affordable secondary school in Jinja")
    assert request.max_fees_ugx is None
    assert request.clarifying_questions[0].field == "fees"


def test_unsupported_wish_is_recorded():
    assert (
        "academic results or rankings"
        in interpret("the best school in Kampala for UCE results").unsupported_preferences
    )


def test_off_topic_message():
    assert not interpret("what is the weather like?").is_school_search


def test_an_answer_joined_to_the_request_fills_the_gaps():
    request = KeywordInterpreter().interpret("A good school. Kampala, S.1")
    assert request.district == "Kampala"
    assert Level.SECONDARY in request.levels
