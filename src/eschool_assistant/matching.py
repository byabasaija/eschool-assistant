"""Checks schools against criteria using only what eSchool publishes about them.

This is the anti-fabrication layer. A school passing an eSchool filter is a candidate, not a
proof: the site's filters have measurable gaps (its fee filter admits schools with no published
tuition, its Kampala filter returns other districts). Every claim shown to a parent comes from
a `CriterionCheck` whose evidence quotes the page. Silence on the page is "unknown", never "no".
"""

from __future__ import annotations

import re

from .models import (
    Accommodation,
    AdmissionStatus,
    CriterionCheck,
    Gender,
    Importance,
    Level,
    MatchStatus,
    ParentRequest,
    School,
    SchoolMatch,
)
from .query import LEVEL_VALUES

_NO_PROFILE = "The school's profile could not be loaded, so this is unconfirmed."


def _money(amount: int) -> str:
    return f"UGX {amount:,}"


def _district(school: School, wanted: str) -> tuple[MatchStatus, str]:
    if not school.district:
        return MatchStatus.UNKNOWN, "eSchool does not list a district for this school."
    if school.district.casefold() == wanted.casefold():
        return MatchStatus.MET, f"Listed in {school.district} district."
    return MatchStatus.NOT_MET, f"Listed in {school.district} district, not {wanted.title()}."


def _area(school: School, counties: tuple[str, ...]) -> tuple[MatchStatus, str]:
    if not school.profile_loaded:
        return MatchStatus.UNKNOWN, _NO_PROFILE
    if not school.county:
        return MatchStatus.UNKNOWN, "The profile does not state a county or division."
    if school.county.casefold() in {county.casefold() for county in counties}:
        return MatchStatus.MET, f"Profile lists {school.county}."
    return MatchStatus.NOT_MET, f"Profile lists {school.county}."


def _fees(school: School, low: int | None, high: int | None) -> tuple[MatchStatus, str]:
    if not school.profile_loaded:
        return MatchStatus.UNKNOWN, _NO_PROFILE
    if school.fee_min_ugx is None or school.fee_max_ugx is None:
        shown = school.fee_text or "no tuition figure"
        return MatchStatus.UNKNOWN, f'Tuition is not published (profile says "{shown}").'
    if school.fee_min_ugx == school.fee_max_ugx:
        published = f"{_money(school.fee_min_ugx)} per term"
    else:
        published = f"{_money(school.fee_min_ugx)} to {_money(school.fee_max_ugx)} per term"
    published = f"Estimated tuition is {published}, excluding requirements"
    if high is not None and school.fee_min_ugx > high:
        return MatchStatus.NOT_MET, f"{published}, above {_money(high)}."
    if low is not None and school.fee_max_ugx < low:
        return MatchStatus.NOT_MET, f"{published}, below {_money(low)}."
    if (high is not None and school.fee_max_ugx > high) or (
        low is not None and school.fee_min_ugx < low
    ):
        return (
            MatchStatus.PARTIAL,
            f"{published}. Only part of this range fits; it varies by class.",
        )
    return MatchStatus.MET, f"{published}."


def _gender(school: School, wanted: Gender) -> tuple[MatchStatus, str]:
    if not school.profile_loaded:
        return MatchStatus.UNKNOWN, _NO_PROFILE
    if not school.gender:
        return MatchStatus.UNKNOWN, "The profile does not state the school's gender policy."
    status = MatchStatus.MET if school.gender.casefold() == wanted else MatchStatus.NOT_MET
    return status, f"Profile lists gender as {school.gender}."


def _accommodation(school: School, wanted: Accommodation) -> tuple[MatchStatus, str]:
    if not school.profile_loaded:
        return MatchStatus.UNKNOWN, _NO_PROFILE
    if not school.section:
        return MatchStatus.UNKNOWN, "The profile does not state day or boarding."
    status = MatchStatus.MET if wanted in school.section.casefold() else MatchStatus.NOT_MET
    return status, f"Profile lists section as {school.section}."


def _levels(school: School, wanted: list[Level]) -> tuple[MatchStatus, str]:
    if not school.level:
        return MatchStatus.UNKNOWN, "eSchool does not list a level for this school."
    accepted = {value for level in wanted for value in LEVEL_VALUES[level]}
    status = MatchStatus.MET if school.level in accepted else MatchStatus.NOT_MET
    return status, f"Listed as {school.level}."


def _admission(school: School, wanted: AdmissionStatus) -> tuple[MatchStatus, str]:
    badge = school.admissions_badge
    if not badge:
        return MatchStatus.UNKNOWN, "eSchool shows no admissions status for this school."
    is_open = "closed" not in badge.casefold()
    matches = is_open == (wanted == AdmissionStatus.OPEN)
    return (MatchStatus.MET if matches else MatchStatus.NOT_MET), f'eSchool shows "{badge}".'


def _words(text: str) -> set[str]:
    """Significant words, so 'Catholic Founded' matches 'Catholic' but 'of' matches nothing."""
    return {word for word in re.findall(r"\w+", text.casefold()) if len(word) > 2} - {"other"}


def _listed(actual: str | None, wanted: list[str], what: str, school: School):
    if not school.profile_loaded:
        return MatchStatus.UNKNOWN, _NO_PROFILE
    if not actual:
        return MatchStatus.UNKNOWN, f"The profile does not state the {what}."
    status = MatchStatus.MET if _words(" ".join(wanted)) & _words(actual) else MatchStatus.NOT_MET
    return status, f"Profile lists {what} as {actual}."


def check_school(
    school: School,
    request: ParentRequest,
    district: str | None,
    counties: tuple[str, ...],
) -> SchoolMatch:
    def add(label: str, result: tuple[MatchStatus, str], importance: str = Importance.REQUIRED):
        status, evidence = result
        checks.append(
            CriterionCheck(
                criterion=label,
                status=status,
                evidence=evidence,
                required=importance == Importance.REQUIRED,
            )
        )

    checks: list[CriterionCheck] = []
    if district:
        add("District", _district(school, district), request.district_importance)
    if counties:
        add("Area", _area(school, counties), request.area_importance)
    if request.min_fees_ugx is not None or request.max_fees_ugx is not None:
        add(
            "Fees per term",
            _fees(school, request.min_fees_ugx, request.max_fees_ugx),
            request.fees_importance,
        )
    if request.gender:
        add("Gender", _gender(school, request.gender), request.gender_importance)
    if request.accommodation:
        add(
            "Day/boarding",
            _accommodation(school, request.accommodation),
            request.accommodation_importance,
        )
    if request.levels:
        add("School level", _levels(school, request.levels), request.levels_importance)
    if request.admission_class:
        add(
            f"Admitting {request.admission_class}",
            (
                MatchStatus.UNKNOWN,
                "eSchool does not publish intake by class. Confirm with the school.",
            ),
        )
    if request.admission_status:
        add(
            "Admission status",
            _admission(school, request.admission_status),
            request.admission_importance,
        )
    if request.curriculums:
        add(
            "Curriculum",
            _listed(
                school.curriculum,
                [curriculum.value for curriculum in request.curriculums],
                "curriculum",
                school,
            ),
        )
    if request.religions:
        add(
            "Religion",
            _listed(
                school.religion,
                [religion.value for religion in request.religions],
                "religion",
                school,
            ),
        )
    for facility in request.facilities:
        if not school.profile_loaded:
            add(facility.value, (MatchStatus.UNKNOWN, _NO_PROFILE))
        elif facility.value in school.facilities:
            add(facility.value, (MatchStatus.MET, "Listed under the school's facilities."))
        else:
            add(facility.value, (MatchStatus.UNKNOWN, "Not listed on the profile."))

    return SchoolMatch(
        school=school,
        checks=checks,
        fully_verified=all(check.status == MatchStatus.MET for check in checks),
        has_conflict=any(
            check.status == MatchStatus.NOT_MET and check.required for check in checks
        ),
    )


def rank_key(match: SchoolMatch) -> tuple:
    """Best first: nothing contradicted, then most confirmed, then eSchool's rating."""

    def count(status: MatchStatus, required: bool | None = None) -> int:
        return sum(
            check.status == status and (required is None or check.required == required)
            for check in match.checks
        )

    return (
        count(MatchStatus.NOT_MET, required=True),
        count(MatchStatus.NOT_MET),
        count(MatchStatus.UNKNOWN) + count(MatchStatus.PARTIAL),
        -(match.school.rating or 0.0),
        match.school.name,
    )
