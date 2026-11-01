from eschool_assistant.matching import check_school, rank_key
from eschool_assistant.models import (
    Accommodation,
    AdmissionStatus,
    Facility,
    Gender,
    Importance,
    Level,
    MatchStatus,
    ParentRequest,
    Religion,
)


def request(**values) -> ParentRequest:
    return ParentRequest(is_school_search=True, **values)


def status_of(match, criterion):
    return next(c.status for c in match.checks if c.criterion == criterion)


def test_all_criteria_confirmed(make_school):
    match = check_school(
        make_school(),
        request(
            gender=Gender.GIRLS,
            accommodation=Accommodation.BOARDING,
            levels=[Level.SECONDARY],
            max_fees_ugx=1_000_000,
        ),
        "KAMPALA",
        ("KAMPALA CENTRAL DIVISION",),
    )
    assert match.fully_verified
    assert not match.has_conflict


def test_unpublished_fees_are_unknown_not_met(make_school):
    school = make_school(fee_min_ugx=None, fee_max_ugx=None, fee_text="Contact School for Fees")
    match = check_school(school, request(max_fees_ugx=1_000_000), None, ())
    assert status_of(match, "Fees per term") == MatchStatus.UNKNOWN
    assert not match.fully_verified
    assert not match.has_conflict
    assert "Contact School for Fees" in match.checks[0].evidence


def test_fees_above_budget(make_school):
    match = check_school(
        make_school(fee_min_ugx=2_000_000, fee_max_ugx=2_500_000),
        request(max_fees_ugx=1_000_000),
        None,
        (),
    )
    assert status_of(match, "Fees per term") == MatchStatus.NOT_MET
    assert match.has_conflict


def test_fee_range_straddling_budget_is_partial(make_school):
    match = check_school(
        make_school(fee_min_ugx=800_000, fee_max_ugx=1_200_000),
        request(max_fees_ugx=1_000_000),
        None,
        (),
    )
    assert status_of(match, "Fees per term") == MatchStatus.PARTIAL


def test_school_in_other_district_is_a_conflict(make_school):
    # eSchool's KAMPALA filter really does return Wakiso schools.
    match = check_school(make_school(district="Wakiso"), request(), "KAMPALA", ())
    assert status_of(match, "District") == MatchStatus.NOT_MET
    assert match.has_conflict


def test_preferred_area_miss_is_not_a_conflict(make_school):
    match = check_school(
        make_school(county="Nakawa Division"),
        request(area="Central", area_importance=Importance.PREFERRED),
        "KAMPALA",
        ("KAMPALA CENTRAL DIVISION",),
    )
    assert status_of(match, "Area") == MatchStatus.NOT_MET
    assert not match.has_conflict


def test_day_and_boarding_satisfies_boarding(make_school):
    match = check_school(
        make_school(section="Day & Boarding"),
        request(accommodation=Accommodation.BOARDING),
        None,
        (),
    )
    assert match.fully_verified


def test_closed_school_fails_open_admission_request(make_school):
    match = check_school(make_school(), request(admission_status=AdmissionStatus.OPEN), None, ())
    assert status_of(match, "Admission status") == MatchStatus.NOT_MET


def test_admission_class_is_never_claimed(make_school):
    match = check_school(make_school(), request(admission_class="Senior One"), None, ())
    assert status_of(match, "Admitting Senior One") == MatchStatus.UNKNOWN


def test_missing_facility_is_unknown_not_absent(make_school):
    match = check_school(make_school(), request(facilities=[Facility.TRANSPORT]), None, ())
    assert status_of(match, "School Transport") == MatchStatus.UNKNOWN


def test_religion_vocabularies_are_reconciled(make_school):
    match = check_school(make_school(), request(religions=[Religion.CATHOLIC]), None, ())
    assert status_of(match, "Religion") == MatchStatus.MET


def test_unloaded_profile_claims_nothing(make_school):
    school = make_school(profile_loaded=False, gender=None, section=None)
    match = check_school(school, request(gender=Gender.GIRLS), None, ())
    assert status_of(match, "Gender") == MatchStatus.UNKNOWN


def test_contradicted_schools_rank_last(make_school):
    good = check_school(make_school(), request(max_fees_ugx=1_000_000), None, ())
    bad = check_school(
        make_school(fee_min_ugx=3_000_000, fee_max_ugx=3_000_000, rating=5.0),
        request(max_fees_ugx=1_000_000),
        None,
        (),
    )
    assert min([bad, good], key=rank_key) is good
