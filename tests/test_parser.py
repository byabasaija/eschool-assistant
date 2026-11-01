import pytest

from eschool_assistant.eschool.parser import (
    UnexpectedResponse,
    parse_profile,
    parse_search_page,
)
from eschool_assistant.models import School


def blank_school(**overrides):
    return School(**({"name": "x", "slug": "x", "url": "u"} | overrides))


def test_search_page_reads_real_total_not_waitlist_counter(fixture_html):
    # Regression: the page also says "0 of 5 schools selected" before the real count.
    page = parse_search_page(fixture_html("search_kampala_central"))
    assert page.total == 31
    assert len(page.schools) == 24


def test_search_card_fields(fixture_html):
    school = parse_search_page(fixture_html("search_kampala_central")).schools[0]
    assert school.name == "The Hungry Caterpillar Primary School"
    assert school.slug == "the-hungry-caterpillar-primary-school"
    assert school.level == "Nursery & Primary"
    assert school.district == "Kampala"
    assert school.admissions_badge == "Admissions Closed"
    assert school.rating == 4.3


def test_search_page_exposes_site_vocabulary(fixture_html):
    page = parse_search_page(fixture_html("search_kampala_central"))
    assert "KAMPALA" in page.districts
    assert "KAMPALA CENTRAL DIVISION" in page.counties


def test_empty_result_is_a_valid_page(fixture_html):
    page = parse_search_page(fixture_html("search_empty"))
    assert page.total == 0
    assert page.schools == []


def test_page_that_is_not_a_search_page_raises():
    with pytest.raises(UnexpectedResponse):
        parse_search_page("<html><body>Checking your browser…</body></html>")


def test_profile_with_published_fees(fixture_html):
    school = parse_profile(fixture_html("profile_published_fees"), blank_school())
    assert school.profile_loaded
    assert (school.fee_min_ugx, school.fee_max_ugx) == (2_015_000, 2_515_000)
    assert school.county == "Kampala Central Division"
    assert school.district == "Kampala"
    assert school.gender == "mixed"
    assert school.section == "Day"


def test_contact_for_fees_stays_unknown(fixture_html):
    school = parse_profile(fixture_html("profile_contact_for_fees"), blank_school())
    assert school.fee_min_ugx is None and school.fee_max_ugx is None
    assert school.fee_text == "Contact School for Fees"
    assert school.gender == "girls"
    assert school.section == "Boarding"


def test_admission_fee_is_never_read_as_tuition(fixture_html):
    # This school passes eSchool's fee filter but only publishes a one-time admission fee.
    school = parse_profile(fixture_html("profile_admission_fee_only"), blank_school())
    assert school.fee_min_ugx is None


def test_profile_lists_only_known_facilities(fixture_html):
    school = parse_profile(fixture_html("profile_published_fees"), blank_school())
    assert school.facilities == ["Computer Lab", "Library", "Sports Facilities"]


def test_unknown_profile_layout_raises():
    with pytest.raises(UnexpectedResponse):
        parse_profile("<html><body>School not found</body></html>", blank_school())
