"""Data contracts shared by the interpreter, the search layer and the presentation layer.

`ParentRequest` is the only structure the AI produces. Everything after it is ordinary,
deterministic code, so a wrong or malicious model output can at worst produce a bad search
query, never a fabricated school fact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Importance(StrEnum):
    """How firmly the parent stated a criterion. Drives what may be relaxed."""

    REQUIRED = "required"
    PREFERRED = "preferred"


class Gender(StrEnum):
    GIRLS = "girls"
    BOYS = "boys"
    MIXED = "mixed"


class Accommodation(StrEnum):
    BOARDING = "boarding"
    DAY = "day"


class Level(StrEnum):
    NURSERY = "nursery"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    INTERNATIONAL = "international"


class AdmissionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class Curriculum(StrEnum):
    UNEB = "UNEB"
    CAMBRIDGE = "Cambridge"
    IB = "IB"
    MONTESSORI = "Montessori"
    ISLAMIC = "Islamic"


class Facility(StrEnum):
    SCIENCE_LABS = "Science Labs"
    COMPUTER_LAB = "Computer Lab"
    LIBRARY = "Library"
    SPORTS = "Sports Facilities"
    CAFETERIA = "Cafeteria"
    TRANSPORT = "School Transport"
    CLINIC = "School Clinic"
    MUSIC_ARTS = "Music & Arts"


class Religion(StrEnum):
    CATHOLIC = "Catholic Founded"
    ANGLICAN = "Church of Uganda (Anglican)"
    SDA = "Seventh-day Adventist (SDA)"
    PENTECOSTAL = "Pentecostal / Born-Again"
    MUSLIM = "Islamic / Muslim"
    SECULAR = "Non-Denominational / Secular"


class Sorting(StrEnum):
    POPULAR = "Popular"
    FEES_LOW = "FeesLow"
    NEWEST = "Newest"


class Clarification(BaseModel):
    """A question for the parent, tied to the criterion it would settle."""

    field: str
    question: str


class ParentRequest(BaseModel):
    """What the parent asked for. `None` or empty always means "not specified"."""

    is_school_search: bool = Field(
        description="False if the message is not a request to find a school in Uganda."
    )
    district: str | None = Field(
        default=None,
        description="Ugandan district as written or implied, e.g. 'Kampala'. A district name only.",
    )
    district_importance: Importance = Importance.REQUIRED
    area: str | None = Field(
        default=None,
        description=(
            "Division, county, municipality or neighbourhood inside the district, as the parent "
            "wrote it, e.g. 'Central Division', 'Ntinda'."
        ),
    )
    area_importance: Importance = Importance.REQUIRED
    area_counties: list[str] = Field(
        default_factory=list,
        description=(
            "If `area` is a neighbourhood, the official county/division names you believe "
            "contain it. Record that belief in `assumptions` too. Empty if `area` is itself "
            "an official name."
        ),
    )
    min_fees_ugx: int | None = Field(
        default=None, description="Lower fee bound in UGX per term, only if stated."
    )
    max_fees_ugx: int | None = Field(
        default=None, description="Upper fee bound in UGX per term, only if stated."
    )
    fees_importance: Importance = Importance.REQUIRED
    fee_period_stated: Literal["term", "year", "not_stated"] = Field(
        default="not_stated",
        description="Period the parent quoted. Yearly amounts are divided by 3 in *_fees_ugx.",
    )
    gender: Gender | None = None
    gender_importance: Importance = Importance.REQUIRED
    accommodation: Accommodation | None = None
    accommodation_importance: Importance = Importance.REQUIRED
    levels: list[Level] = Field(default_factory=list)
    levels_importance: Importance = Importance.REQUIRED
    admission_class: str | None = Field(
        default=None, description="Entry class in the parent's terms, e.g. 'S.1', 'P.3', 'Baby'."
    )
    admission_status: AdmissionStatus | None = Field(
        default=None, description="'open' only if the parent wants schools admitting now."
    )
    admission_importance: Importance = Importance.REQUIRED
    curriculums: list[Curriculum] = Field(default_factory=list)
    facilities: list[Facility] = Field(default_factory=list)
    religions: list[Religion] = Field(default_factory=list)
    school_name: str | None = Field(
        default=None, description="A specific school name, if the parent named one."
    )
    sorting: Sorting | None = Field(
        default=None, description="'FeesLow' only if the parent asks for the cheapest schools."
    )
    unsupported_preferences: list[str] = Field(
        default_factory=list,
        description=(
            "Things the parent wants that no eSchool filter covers, e.g. 'best UCE results', "
            "'special needs support', 'near my office'."
        ),
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Every interpretation the parent did not literally state, in plain language.",
    )
    clarifying_questions: list[Clarification] = Field(
        default_factory=list,
        description="Questions whose answers would materially change the search.",
    )


class School(BaseModel):
    """A school exactly as eSchool shows it. Unknown facts stay `None`, never guessed."""

    name: str
    slug: str
    url: str
    level: str | None = None
    rating: float | None = None
    district: str | None = None
    admissions_badge: str | None = None
    # Populated only from the school's own profile page.
    county: str | None = None
    gender: str | None = None
    section: str | None = None
    curriculum: str | None = None
    religion: str | None = None
    fee_min_ugx: int | None = None
    fee_max_ugx: int | None = None
    fee_text: str | None = None
    facilities: list[str] = Field(default_factory=list)
    profile_loaded: bool = False


class MatchStatus(StrEnum):
    MET = "met"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_MET = "not_met"


class CriterionCheck(BaseModel):
    """One criterion checked against one school, with the evidence that decided it."""

    criterion: str
    status: MatchStatus
    evidence: str
    required: bool = True


class SchoolMatch(BaseModel):
    school: School
    checks: list[CriterionCheck]
    fully_verified: bool
    has_conflict: bool


class SearchStep(BaseModel):
    """One attempt against eSchool, kept so that every relaxation is auditable."""

    description: str
    urls: list[str]
    total_found: int
    dropped: list[str] = Field(default_factory=list)


class Outcome(StrEnum):
    MATCHES = "matches"
    NEAR_MATCHES = "near_matches"
    NO_RESULTS = "no_results"
    NEEDS_CLARIFICATION = "needs_clarification"
    NOT_A_SCHOOL_SEARCH = "not_a_school_search"
    SEARCH_FAILED = "search_failed"


class AssistantResponse(BaseModel):
    query: str
    outcome: Outcome
    message: str
    interpreter: str
    criteria: ParentRequest | None = None
    applied_filters: dict[str, str] = Field(default_factory=dict)
    not_specified: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    clarifying_questions: list[Clarification] = Field(default_factory=list)
    steps: list[SearchStep] = Field(default_factory=list)
    matches: list[SchoolMatch] = Field(default_factory=list)
    total_found: int = 0
