"""Translates interpreted criteria into eSchool's search parameters.

Every mapping here encodes behaviour observed on the live site (see docs/ESCHOOL_INTEGRATION.md).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field, replace

from .models import Accommodation, Level, ParentRequest

# `Nursery & Kindergaten` is the site's own spelling, and the filter needs it.
LEVEL_VALUES = {
    Level.NURSERY: ("Nursery & Kindergaten", "Nursery & Primary"),
    Level.PRIMARY: ("Primary", "Nursery & Primary"),
    Level.SECONDARY: ("Secondary",),
    Level.INTERNATIONAL: ("International",),
}
# The site's single-valued `accomadation` field partitions schools, so "boarding" means two
# separate requests. An array form of the parameter returns nothing.
ACCOMMODATION_VALUES = {
    Accommodation.BOARDING: ("Boarding", "Day & Boarding"),
    Accommodation.DAY: ("Day", "Day & Boarding"),
}
_AREA_NOISE = {"division", "county", "municipality", "town", "council", "city", "around", "near"}


@dataclass(frozen=True)
class Filters:
    """The filters of one search attempt. Removing a field relaxes the search."""

    district: str | None = None
    counties: tuple[str, ...] = ()
    min_fees: int | None = None
    max_fees: int | None = None
    gender: str | None = None
    accommodation: tuple[str, ...] = ()
    levels: tuple[str, ...] = ()
    admission_status: str | None = None
    curriculums: tuple[str, ...] = ()
    facilities: tuple[str, ...] = ()
    religions: tuple[str, ...] = ()
    school_name: str | None = None
    sorting: str | None = None

    def without(self, *names: str) -> Filters:
        changes = {}
        for name in names:
            changes[name] = () if isinstance(getattr(self, name), tuple) else None
        return replace(self, **changes)


@dataclass(frozen=True)
class Relaxable:
    """A filter that can be removed, named in the parent's terms."""

    label: str
    fields: tuple[str, ...]


@dataclass
class ResolvedArea:
    counties: tuple[str, ...] = ()
    unresolved: str | None = None
    via_assumption: bool = False
    candidates: tuple[str, ...] = field(default_factory=tuple)


def _area_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", text.lower())} - _AREA_NOISE


def resolve_area(area: str, suggested: list[str], counties: list[str]) -> ResolvedArea:
    """Match a parent's wording to the site's own county list. Never trusts the model alone."""
    wanted = _area_tokens(area)
    exact = [c for c in counties if wanted and wanted <= _area_tokens(c)]
    if exact:
        return ResolvedArea(counties=tuple(exact))
    by_name = {county.upper(): county for county in counties}
    confirmed = [by_name[s.upper()] for s in suggested if s.upper() in by_name]
    if confirmed:
        return ResolvedArea(counties=tuple(confirmed), via_assumption=True)
    return ResolvedArea(unresolved=area)


def build_filters(
    request: ParentRequest, district: str | None, counties: tuple[str, ...]
) -> Filters:
    return Filters(
        district=district,
        counties=counties,
        min_fees=request.min_fees_ugx,
        max_fees=request.max_fees_ugx,
        gender=request.gender.value if request.gender else None,
        accommodation=ACCOMMODATION_VALUES.get(request.accommodation, ()),
        levels=tuple(dict.fromkeys(v for lv in request.levels for v in LEVEL_VALUES[lv])),
        admission_status=request.admission_status.value if request.admission_status else None,
        curriculums=tuple(c.value for c in request.curriculums),
        facilities=tuple(f.value for f in request.facilities),
        religions=tuple(r.value for r in request.religions),
        school_name=request.school_name,
        sorting=request.sorting.value if request.sorting else None,
    )


def relaxation_order(request: ParentRequest, filters: Filters) -> list[Relaxable]:
    """Removable filters, in the order they should be given up.

    Soft preferences go first, then the hard filters a family could plausibly trade away. A
    required gender or level is never relaxed: a girls' school cannot take a parent's son, and
    a school for the wrong age is no near match. Neither is the district, because a school in
    another part of the country is not a useful substitute.
    """
    area = Relaxable("area", ("counties",))
    fees = Relaxable("fees", ("min_fees", "max_fees"))
    gender = Relaxable("gender", ("gender",))
    accommodation = Relaxable("day/boarding", ("accommodation",))
    levels = Relaxable("school level", ("levels",))
    admission = Relaxable("admission status", ("admission_status",))
    extras = [
        Relaxable(label, (name,))
        for label, name in (
            ("facilities", "facilities"),
            ("curriculum", "curriculums"),
            ("religion", "religions"),
        )
    ]
    ranked = [
        (area, request.area_importance),
        (fees, request.fees_importance),
        (gender, request.gender_importance),
        (accommodation, request.accommodation_importance),
        (levels, request.levels_importance),
        (admission, request.admission_importance),
    ]
    active = [(item, imp) for item, imp in ranked if any(getattr(filters, f) for f in item.fields)]
    preferred = [item for item, imp in active if imp == "preferred"]
    fixed = {gender.label, levels.label}
    required = [item for item, imp in active if imp != "preferred" and item.label not in fixed]
    order_hint = {admission.label: 0, area.label: 1, fees.label: 2}
    required.sort(key=lambda item: order_hint.get(item.label, 3))
    present_extras = [item for item in extras if getattr(filters, item.fields[0])]
    return preferred + present_extras + required


def to_param_sets(filters: Filters, page: int = 1) -> list[list[tuple[str, str]]]:
    """Expand filters into one parameter list per request the site needs.

    Fees are sent as plain digits. The site's own form submits `1,000,000`, which the server
    misreads (`min=400,000&max=1,000,000` returns nothing; plain digits return schools).
    """
    base: list[tuple[str, str]] = []
    if filters.district:
        base.append(("location", filters.district))
    if filters.min_fees is not None:
        base.append(("min_fees", str(filters.min_fees)))
    if filters.max_fees is not None:
        base.append(("max_fees", str(filters.max_fees)))
    if filters.gender:
        base.append(("gender", filters.gender))
    if filters.admission_status:
        base.append(("admission_status", filters.admission_status))
    if filters.school_name:
        base.append(("q", filters.school_name))
    for name, values in (
        ("levels[]", filters.levels),
        ("curriculums[]", filters.curriculums),
        ("facilities[]", filters.facilities),
        ("religions[]", filters.religions),
    ):
        base.extend((name, value) for value in values)
    if filters.sorting:
        base.append(("sorting", filters.sorting))
    if page > 1:
        base.append(("page", str(page)))

    county_options = filters.counties or (None,)
    accommodation_options = filters.accommodation or (None,)
    param_sets = []
    for county, accommodation in itertools.product(county_options, accommodation_options):
        params = list(base)
        if county:
            params.append(("county", county))
        if accommodation:
            params.append(("accomadation", accommodation))
        param_sets.append(params)
    return param_sets
