"""Keyword fallback, used only when Gemini is unavailable or unconfigured.

It handles plainly worded requests so that the app degrades instead of failing. It is not the
product's interpreter: it cannot judge ambiguity or nuance, and every response it produces says so.
"""

from __future__ import annotations

import re

from ..models import (
    Accommodation,
    AdmissionStatus,
    Clarification,
    Curriculum,
    Facility,
    Gender,
    Importance,
    Level,
    ParentRequest,
    Religion,
    Sorting,
)

_SOFT = r"(?:preferably|ideally|around|if possible|maybe|would be nice)"
_DISTRICTS = (
    "kampala|wakiso|mukono|jinja|mbarara|gulu|masaka|mbale|lira|arua|hoima|fort portal|kabale"
)
_DIVISIONS = "central|kawempe|makindye|nakawa|rubaga"
_NAMED_PLACE = re.compile(r"\b(?:in|at|around|near)\s+([A-Z][a-z]+)\b")
_MONEY = r"(\d+(?:[.,]\d+)*)\s*(m|million|k|thousand)?\b"
_FACILITIES = {
    r"science lab": Facility.SCIENCE_LABS,
    r"computer": Facility.COMPUTER_LAB,
    r"librar": Facility.LIBRARY,
    r"sports?|football": Facility.SPORTS,
    r"cafeteria|canteen": Facility.CAFETERIA,
    r"transport|school bus": Facility.TRANSPORT,
    r"clinic|sick bay": Facility.CLINIC,
    r"music|arts": Facility.MUSIC_ARTS,
}
_CURRICULUMS = {
    r"\buneb\b|\bnational curriculum\b": Curriculum.UNEB,
    r"cambridge|igcse": Curriculum.CAMBRIDGE,
    r"\bib\b|baccalaureate": Curriculum.IB,
    r"montessori": Curriculum.MONTESSORI,
}
_RELIGIONS = {
    r"catholic": Religion.CATHOLIC,
    r"anglican|church of uganda": Religion.ANGLICAN,
    r"adventist|\bsda\b": Religion.SDA,
    r"pentecostal|born[- ]again": Religion.PENTECOSTAL,
    r"muslim|islamic": Religion.MUSLIM,
}
_UNSUPPORTED = {
    r"best|top[- ]performing|results|uce|ple|uace": "academic results or rankings",
    r"special needs|autis|disabilit": "special needs support",
    r"scholarship|bursar": "scholarships or bursaries",
    r"near my|close to|walking distance": "distance from a specific place",
}
_VAGUE_FEES = r"affordable|cheap|not too expensive|reasonable fees|low fees"


def _amount(number: str, unit: str | None) -> int:
    value = float(number.replace(",", ""))
    multiplier = {"m": 1_000_000, "million": 1_000_000, "k": 1_000, "thousand": 1_000}
    return int(value * multiplier.get((unit or "").lower(), 1))


def _soft_before(text: str, term: str) -> Importance:
    # Softness after a term must follow without punctuation: in "Kampala, preferably around
    # Central", the word softens Central, not Kampala.
    pattern = rf"{_SOFT}\W+(?:\w+\W+){{0,3}}{term}|{term}\s+(?:\w+\s+){{0,2}}{_SOFT}"
    return Importance.PREFERRED if re.search(pattern, text) else Importance.REQUIRED


def _fees(text: str, request: ParentRequest) -> None:
    upper = re.search(
        rf"(?:under|below|less than|up to|at most|maximum(?: of)?|max|no more than|within|"
        rf"not more than|budget(?: of| is)?)\s*(?:ugx|shs?\.?)?\s*{_MONEY}",
        text,
    )
    lower = re.search(
        rf"(?:over|above|at least|minimum(?: of)?|from)\s*(?:ugx|shs?\.?)?\s*{_MONEY}", text
    )
    if upper:
        request.max_fees_ugx = _amount(*upper.groups())
    if lower:
        request.min_fees_ugx = _amount(*lower.groups())
    if not (upper or lower):
        return
    if re.search(r"per year|a year|annually|yearly", text):
        request.fee_period_stated = "year"
        for name in ("max_fees_ugx", "min_fees_ugx"):
            if (value := getattr(request, name)) is not None:
                setattr(request, name, value // 3)
        request.assumptions.append("Converted the yearly amount to a per-term amount (÷3).")
    elif re.search(r"per term|a term|termly", text):
        request.fee_period_stated = "term"
    else:
        request.assumptions.append("Treated the amount as per term.")


class KeywordInterpreter:
    name = "keyword fallback"

    def interpret(self, query: str) -> ParentRequest:
        text = " ".join(query.lower().replace("’", "'").split())
        request = ParentRequest(
            is_school_search=bool(
                re.search(
                    r"school|academy|college|boarding|nursery|kindergarten|primary|secondary|"
                    r"senior|\bs\.?[1-6]\b|\bp\.?[1-7]\b|admission",
                    text,
                )
            )
        )
        if not request.is_school_search:
            return request

        if match := re.search(rf"\b({_DISTRICTS})\b", text):
            request.district = match.group(1).title()
            request.district_importance = _soft_before(text, match.group(1))
        elif (match := _NAMED_PLACE.search(" ".join(query.split()))) and not re.fullmatch(
            _DIVISIONS, match.group(1).lower()
        ):
            # Any other place is passed on, so the site's district list decides. Dropping it would
            # silently turn a local request into a nationwide search.
            request.district = match.group(1)
        if match := re.search(rf"\b({_DIVISIONS})\s+(?:division|county)?", text):
            request.area = match.group(1).title()
            request.area_importance = _soft_before(text, match.group(1))
            request.district = request.district or "Kampala"

        if re.search(r"\bgirls?'?\b|\bgirls'", text) and not re.search(r"\bboys\b", text):
            if re.search(r"girls?'? (?:only|boarding|day|school|secondary|primary)", text):
                request.gender = Gender.GIRLS
        elif re.search(r"boys'? (?:only|boarding|day|school|secondary|primary)", text):
            request.gender = Gender.BOYS
        elif re.search(r"\bmixed\b|co-?ed", text):
            request.gender = Gender.MIXED

        if "boarding" in text:
            request.accommodation = Accommodation.BOARDING
        elif re.search(r"\bday (?:school|primary|secondary|scholar)", text):
            request.accommodation = Accommodation.DAY

        senior = re.search(r"\b(?:senior|s\.?)\s*(one|two|three|four|five|six|[1-6])\b", text)
        primary = re.search(
            r"\b(?:primary|p\.?)\s*(one|two|three|four|five|six|seven|[1-7])\b", text
        )
        if senior:
            request.levels = [Level.SECONDARY]
            request.admission_class = f"Senior {senior.group(1).title()}"
        elif primary:
            request.levels = [Level.PRIMARY]
            request.admission_class = f"Primary {primary.group(1).title()}"
        elif re.search(r"secondary|o[- ]level|a[- ]level", text):
            request.levels = [Level.SECONDARY]
        elif "primary" in text:
            request.levels = [Level.PRIMARY]
        elif re.search(r"nursery|kindergarten|\bbaby class\b|\btop class\b", text):
            request.levels = [Level.NURSERY]

        if re.search(
            r"currently admitting|admitting|taking students|admissions? open|open for", text
        ):
            request.admission_status = AdmissionStatus.OPEN

        _fees(text, request)
        if "cheapest" in text:
            request.sorting = Sorting.FEES_LOW
        if re.search(_VAGUE_FEES, text) and request.max_fees_ugx is None:
            request.clarifying_questions.append(
                Clarification(field="fees", question="What is the most you can pay per term?")
            )

        request.facilities = [v for p, v in _FACILITIES.items() if re.search(p, text)]
        request.curriculums = [v for p, v in _CURRICULUMS.items() if re.search(p, text)]
        request.religions = [v for p, v in _RELIGIONS.items() if re.search(p, text)]
        request.unsupported_preferences = [v for p, v in _UNSUPPORTED.items() if re.search(p, text)]
        return request
