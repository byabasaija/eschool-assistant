"""Turns eSchool's server-rendered HTML into `School` records. Pure functions, no I/O.

Anything absent from the page stays `None`. A page that no longer looks like an eSchool page
raises `UnexpectedResponse` instead of reading as "no schools found".
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from ..models import Facility, School


class UnexpectedResponse(RuntimeError):
    """The page structure is not what this parser was written against."""


@dataclass
class SearchPage:
    total: int
    schools: list[School]
    districts: list[str] = field(default_factory=list)
    counties: list[str] = field(default_factory=list)


# Anchored on "Showing" because the waitlist widget also renders "0 of 5 schools selected".
_COUNT_RE = re.compile(r"Showing\s+(\d[\d,]*)\s+of\s+(\d[\d,]*)\s+schools", re.IGNORECASE)
_FEE_RE = re.compile(r"\d[\d,]*")
_KNOWN_FACILITIES = {facility.value for facility in Facility}
_PROFILE_LABELS = ("Curriculum", "Gender", "Section", "Religion")
# Labels that follow a value in the profile, so an empty field is not read as the next label.
_NOT_A_VALUE = {*_PROFILE_LABELS, "Estimated Entry Budget"}


def _clean(text: str | None) -> str | None:
    """Collapse whitespace and undo the double HTML-escaping present in some school names."""
    if text is None:
        return None
    cleaned = " ".join(html.unescape(text).split())
    return cleaned or None


def _select_options(soup: BeautifulSoup, name: str) -> list[str]:
    select = soup.find("select", attrs={"name": name})
    if select is None:
        return []
    return [opt["value"] for opt in select.find_all("option") if opt.get("value")]


def _parse_card(card: Tag) -> School | None:
    link = card.find("a", href=re.compile(r"slug="))
    if link is None:
        return None
    url = link["href"]
    slug = parse_qs(urlparse(url).query).get("slug", [None])[0]
    title = card.find("h3")
    name = _clean(title.get_text()) if title else None
    if not slug or not name:
        return None

    rating = card.select_one(".bg-warning-subtle")
    pin = card.select_one("i.fi-map-pin")
    location = pin.find_next_sibling("span") if pin else None
    badge = card.select_one("span.badge span.fw-semibold")
    level = card.select_one("span.badge.bg-body-secondary")
    try:
        rating_value = float(rating.get_text(strip=True)) if rating else None
    except ValueError:
        rating_value = None
    return School(
        name=name,
        slug=slug,
        url=url,
        level=_clean(level.get_text()) if level else None,
        rating=rating_value,
        district=_clean(location.get_text()) if location else None,
        admissions_badge=_clean(badge.get_text()) if badge else None,
    )


def parse_search_page(page_html: str) -> SearchPage:
    soup = BeautifulSoup(page_html, "html.parser")
    if soup.find("form", id="searchFilterForm") is None:
        raise UnexpectedResponse("eSchool search form not found on the results page")
    match = _COUNT_RE.search(soup.get_text(" "))
    if match is None:
        raise UnexpectedResponse("eSchool result count not found on the results page")
    schools = [school for card in soup.find_all("article") if (school := _parse_card(card))]
    return SearchPage(
        total=int(match.group(2).replace(",", "")),
        schools=schools,
        districts=_select_options(soup, "location"),
        counties=_select_options(soup, "county"),
    )


def _value_after(lines: list[str], label: str) -> str | None:
    if label not in lines:
        return None
    position = lines.index(label) + 1
    if position >= len(lines) or lines[position] in _NOT_A_VALUE:
        return None
    return lines[position]


def _parse_fees(lines: list[str]) -> tuple[int | None, int | None, str | None]:
    label = "Estimated Tuition Fee"
    if label not in lines:
        return None, None, None
    position = lines.index(label) + 1
    parts: list[str] = []
    while position < len(lines) and not lines[position].startswith("/"):
        parts.append(lines[position])
        position += 1
        if len(parts) >= 3:
            break
    text = _clean(" ".join(parts))
    amounts = [int(a.replace(",", "")) for a in _FEE_RE.findall(text or "")]
    # Zero or implausibly low figures are placeholders on the site, not real fees.
    amounts = [amount for amount in amounts if amount >= 1000]
    if not amounts:
        return None, None, text
    return min(amounts), max(amounts), text


def parse_profile(page_html: str, school: School) -> School:
    """Return `school` enriched with the facts from its profile page."""
    soup = BeautifulSoup(page_html, "html.parser")
    for element in soup(["script", "style", "form"]):
        element.decompose()
    lines = [" ".join(text.split()) for text in soup.stripped_strings]
    lines = [line for line in lines if line]
    if "Quick Admission Guide" not in lines and "County & District" not in lines:
        raise UnexpectedResponse(f"profile page for {school.slug} has an unknown layout")

    county, district = None, school.district
    place = _value_after(lines, "County & District")
    if place and "," in place:
        county, district = (part.strip() or None for part in place.rsplit(",", 1))
    elif place:
        district = place

    fee_min, fee_max, fee_text = _parse_fees(lines)
    start = lines.index("Facilities & Amenities") if "Facilities & Amenities" in lines else None
    end = next(
        (i for i, line in enumerate(lines) if line == "Fees & Financials"),
        len(lines),
    )
    facility_lines = lines[start:end] if start is not None else []

    return school.model_copy(
        update={
            "county": county,
            "district": district or school.district,
            "gender": _value_after(lines, "Gender"),
            "section": _value_after(lines, "Section"),
            "curriculum": _value_after(lines, "Curriculum"),
            "religion": _value_after(lines, "Religion"),
            "fee_min_ugx": fee_min,
            "fee_max_ugx": fee_max,
            "fee_text": fee_text,
            "facilities": sorted(_KNOWN_FACILITIES.intersection(facility_lines)),
            "profile_loaded": True,
        }
    )
