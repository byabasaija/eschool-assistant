"""Orchestrates one request: interpret, review, search, relax if needed, verify, explain."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .eschool.client import ESchoolClient, SearchUnavailable
from .eschool.parser import UnexpectedResponse
from .interpreter.base import InterpretationError, Interpreter
from .matching import check_school, rank_key
from .models import (
    AssistantResponse,
    Clarification,
    Outcome,
    ParentRequest,
    School,
    SchoolMatch,
    SearchStep,
    Sorting,
)
from .query import Filters, build_filters, relaxation_order, resolve_area, to_param_sets

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 1000
MAX_PAGES = 3  # 24 schools per page. Bounded because each page can take 15 seconds.
PROFILE_LIMIT = 8
DISPLAY_LIMIT = 8
ENOUGH_CANDIDATES = 5
MAX_WORKERS = 3  # The site is already slow.
MAX_QUESTIONS = 3


@dataclass
class Retrieval:
    schools: list[School] = field(default_factory=list)
    total: int = 0
    urls: list[str] = field(default_factory=list)
    # Schools that passed the fee filter, so the likeliest to publish fees worth verifying.
    fee_filtered: set[str] = field(default_factory=set)


class Assistant:
    def __init__(
        self,
        interpreter: Interpreter,
        client: ESchoolClient,
        fallback: Interpreter | None = None,
    ):
        self._interpreter = interpreter
        self._fallback = fallback
        self._client = client

    def handle(self, query: str) -> AssistantResponse:
        query = " ".join((query or "").split())
        if not query:
            return self._respond(
                query,
                Outcome.NEEDS_CLARIFICATION,
                "Please describe the school you are looking for.",
            )
        if len(query) > MAX_QUERY_CHARS:
            return self._respond(
                query,
                Outcome.NEEDS_CLARIFICATION,
                f"Please shorten your request to {MAX_QUERY_CHARS} characters or fewer.",
            )

        notices: list[str] = []
        try:
            request, interpreter_name = self._interpret(query, notices)
        except InterpretationError as exc:
            return self._respond(query, Outcome.SEARCH_FAILED, f"{exc} Please try again shortly.")

        response = self._respond(query, Outcome.NO_RESULTS, "", interpreter_name, request)
        response.notices = notices
        response.not_specified = _not_specified(request)
        response.clarifying_questions = list(request.clarifying_questions)[:MAX_QUESTIONS]
        for wish in request.unsupported_preferences:
            notices.append(f"eSchool has no filter for “{wish}”, so it was not applied.")

        if not request.is_school_search:
            response.outcome = Outcome.NOT_A_SCHOOL_SEARCH
            response.message = "I can help you find schools in Uganda. What are you looking for?"
            return response

        blocker = _blocking_problem(request)
        if blocker:
            response.outcome = Outcome.NEEDS_CLARIFICATION
            response.clarifying_questions = _merge_questions(blocker, response.clarifying_questions)
            in_list = blocker in response.clarifying_questions
            response.message = blocker.question if in_list else "I need a little more detail."
            return response

        try:
            self._search(request, response)
        except (SearchUnavailable, UnexpectedResponse) as exc:
            logger.warning("eSchool search failed: %s", exc)
            response.outcome = Outcome.SEARCH_FAILED
            response.matches = []
            response.message = (
                "eSchool's search is unavailable right now, so no schools can be confirmed. "
                "Your request was understood; please try again in a few minutes."
            )
        return response

    def _interpret(self, query: str, notices: list[str]) -> tuple[ParentRequest, str]:
        try:
            logger.info("Interpreting with %s", self._interpreter.name)
            return self._interpreter.interpret(query), self._interpreter.name
        except InterpretationError as exc:
            if self._fallback is None:
                raise
            logger.warning("Primary interpreter failed: %s", exc)
            notices.append(
                f"{exc} Basic keyword matching was used instead, so subtle details may be missed."
            )
            return self._fallback.interpret(query), self._fallback.name

    def _search(self, request: ParentRequest, response: AssistantResponse) -> None:
        catalogue = self._client.search([])
        district = _resolve_district(request.district, catalogue.districts)
        if request.district and district is None:
            response.outcome = Outcome.NO_RESULTS
            response.message = f"eSchool does not list {request.district} as a district."
            response.clarifying_questions = _merge_questions(
                Clarification(
                    field="district", question="Is there a nearby district you would consider?"
                ),
                response.clarifying_questions,
            )
            return

        counties = self._resolve_counties(request, district, response)
        filters = build_filters(request, district, counties)
        response.applied_filters = _describe(filters)

        retrieval, dropped = self._retrieve_with_relaxation(request, filters, response)
        if filters.admission_status == "open" and "admission status" in dropped:
            self._report_open_admissions(response)
        if not retrieval.schools:
            response.outcome = Outcome.NO_RESULTS
            response.message = (
                "No school on eSchool matches. I relaxed what could reasonably change, but kept "
                "the essentials such as location, school level and gender."
            )
            return

        active = filters
        for item in relaxation_order(request, filters):
            if item.label in dropped:
                active = active.without(*item.fields)
        if (filters.min_fees or filters.max_fees) and "fees" not in dropped:
            self._add_unpublished_fee_schools(active, retrieval, response)

        self._verify(request, district, counties, retrieval, dropped, response)

    def _resolve_counties(
        self, request: ParentRequest, district: str | None, response: AssistantResponse
    ) -> tuple[str, ...]:
        if not request.area or not district:
            return ()
        options = self._client.search([("location", district)]).counties
        area = resolve_area(request.area, request.area_counties, options)
        if area.via_assumption:
            response.notices.append(
                f"“{request.area}” is not an eSchool area, so it was searched as "
                f"{', '.join(c.title() for c in area.counties)}. Please check this is right."
            )
        if area.unresolved:
            response.notices.append(
                f"“{request.area}” is not an eSchool area, so all of {district.title()} was "
                "searched instead."
            )
        return area.counties

    def _fetch(self, filters: Filters) -> Retrieval:
        """Run every request that `filters` expands to, following pagination up to a limit."""
        param_sets = to_param_sets(filters)
        logger.info("Searching eSchool (%d request(s))", len(param_sets))
        retrieval = Retrieval(urls=[self._client.search_url(p) for p in param_sets])
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            first_pages = list(pool.map(self._client.search, param_sets))
        seen: set[str] = set()
        pages = list(first_pages)
        for index, page in enumerate(first_pages):
            retrieval.total += page.total
            per_page = len(page.schools)
            if per_page and page.total > per_page:
                last = min(MAX_PAGES, -(-page.total // per_page))
                params = param_sets[index]
                later = [params + [("page", str(n))] for n in range(2, last + 1)]
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    pages.extend(pool.map(self._client.search, later))
        for page in pages:
            for school in page.schools:
                if school.slug not in seen:
                    seen.add(school.slug)
                    retrieval.schools.append(school)
        return retrieval

    def _retrieve_with_relaxation(
        self, request: ParentRequest, filters: Filters, response: AssistantResponse
    ) -> tuple[Retrieval, list[str]]:
        """Search strictly, then give up filters in order, then restore any that were unneeded."""
        retrieval = self._fetch(filters)
        response.steps.append(
            SearchStep(
                description="All your criteria",
                urls=retrieval.urls,
                total_found=retrieval.total,
            )
        )
        if retrieval.schools:
            return retrieval, []

        order = relaxation_order(request, filters)
        current = filters
        dropped = []
        for item in order:
            current = current.without(*item.fields)
            dropped.append(item)
            retrieval = self._fetch(current)
            response.steps.append(
                SearchStep(
                    description=f"Without {item.label}",
                    urls=retrieval.urls,
                    total_found=retrieval.total,
                    dropped=[d.label for d in dropped],
                )
            )
            if retrieval.schools:
                break
        else:
            return retrieval, [d.label for d in dropped]

        # Dropping is cumulative, so an earlier filter may not have been the real blocker.
        for item in list(dropped[:-1]):
            remaining = [d for d in dropped if d is not item]
            candidate = filters
            for other in remaining:
                candidate = candidate.without(*other.fields)
            restored = self._fetch(candidate)
            if restored.schools:
                dropped, retrieval = remaining, restored
                response.steps.append(
                    SearchStep(
                        description=f"Restored {item.label}",
                        urls=restored.urls,
                        total_found=restored.total,
                        dropped=[d.label for d in dropped],
                    )
                )
        return retrieval, [d.label for d in dropped]

    def _report_open_admissions(self, response: AssistantResponse) -> None:
        nationwide = self._client.search([("admission_status", "open")])
        if nationwide.total == 0:
            response.notices.append(
                "eSchool currently marks no school in Uganda as open for admission, so this "
                "filter cannot be met anywhere. Schools below are listed as closed; you can join "
                "their waitlist or ask them directly."
            )

    def _add_unpublished_fee_schools(
        self, active: Filters, retrieval: Retrieval, response: AssistantResponse
    ) -> None:
        """Most schools publish no fees, so a fee filter alone would silently hide them."""
        if len(retrieval.schools) >= ENOUGH_CANDIDATES:
            return
        wider = self._fetch(active.without("min_fees", "max_fees"))
        known = {school.slug for school in retrieval.schools}
        retrieval.fee_filtered = set(known)
        extra = [school for school in wider.schools if school.slug not in known]
        if not extra:
            return
        retrieval.schools.extend(extra)
        response.steps.append(
            SearchStep(
                description="Also checked schools the fee filter skipped",
                urls=wider.urls,
                total_found=wider.total,
            )
        )

    def _verify(
        self,
        request: ParentRequest,
        district: str | None,
        counties: tuple[str, ...],
        retrieval: Retrieval,
        dropped: list[str],
        response: AssistantResponse,
    ) -> None:
        logger.info("Checking school profiles")

        def check(school: School) -> SchoolMatch:
            return check_school(school, request, district, counties)

        def priority(match: SchoolMatch) -> tuple:
            rank = rank_key(match)
            return (rank[:2], match.school.slug not in retrieval.fee_filtered, rank[2:])

        shortlist = sorted(map(check, retrieval.schools), key=priority)
        top = [match.school for match in shortlist[:PROFILE_LIMIT]]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            loaded = list(pool.map(self._load_profile, top))
        failed = sum(not school.profile_loaded for school in loaded)
        if failed:
            response.notices.append(
                f"{failed} school profile(s) could not be loaded, so those details are unconfirmed."
            )

        verified = sorted(map(check, loaded), key=rank_key)
        if request.sorting == Sorting.FEES_LOW:
            verified.sort(key=lambda m: (rank_key(m)[:2], m.school.fee_min_ugx or float("inf")))
        expected = {_CHECK_FOR_FILTER.get(label, label) for label in dropped}
        acceptable = [m for m in verified if _unexpected_conflicts(m, expected) == 0]
        hidden = [m for m in verified if m not in acceptable]
        shown = acceptable or verified
        response.matches = shown[:DISPLAY_LIMIT]
        response.total_found = retrieval.total
        response.notices.extend(_location_notice(retrieval.schools, district))
        if hidden and acceptable:
            reasons = "; ".join(_conflict_summary(m, expected) for m in hidden)
            response.notices.append(f"Left out after checking their eSchool profiles: {reasons}.")
        if retrieval.total > len(loaded):
            response.notices.append(
                f"eSchool reported {retrieval.total} schools. The first "
                f"{len(retrieval.schools)} were considered and the top {len(loaded)} checked in "
                "detail."
            )

        clean = not dropped and acceptable and not shown[0].has_conflict
        response.outcome = Outcome.MATCHES if clean else Outcome.NEAR_MATCHES
        confirmed = sum(match.fully_verified for match in response.matches)
        if clean:
            response.message = (
                f"Found {_schools(len(response.matches))} for you; {confirmed} confirmed on "
                "every point. Anything unconfirmed is marked on each school."
            )
        else:
            given_up = ", ".join(dropped) or "some of your criteria"
            response.message = (
                f"No school meets everything you asked for. These are the closest options, "
                f"found by relaxing: {given_up}. Each school shows what it does and does not meet."
            )

    def _load_profile(self, school: School) -> School:
        try:
            return self._client.load_profile(school)
        except (SearchUnavailable, UnexpectedResponse) as exc:
            logger.warning("Profile unavailable for %s: %s", school.slug, exc)
            return school

    @staticmethod
    def _respond(
        query: str,
        outcome: Outcome,
        message: str,
        interpreter: str = "none",
        criteria: ParentRequest | None = None,
    ) -> AssistantResponse:
        return AssistantResponse(
            query=query,
            outcome=outcome,
            message=message,
            interpreter=interpreter,
            criteria=criteria,
        )


_CHECK_FOR_FILTER = {
    "area": "Area",
    "fees": "Fees per term",
    "gender": "Gender",
    "day/boarding": "Day/boarding",
    "school level": "School level",
    "admission status": "Admission status",
    "facilities": "Facilities",
    "curriculum": "Curriculum",
    "religion": "Religion",
}


def _unexpected_conflicts(match: SchoolMatch, expected: set[str]) -> int:
    return sum(
        check.status == "not_met" and check.required and check.criterion not in expected
        for check in match.checks
    )


def _conflict_summary(match: SchoolMatch, expected: set[str]) -> str:
    conflicts = [
        check.evidence.rstrip(".")
        for check in match.checks
        if check.status == "not_met" and check.required and check.criterion not in expected
    ]
    return f"{match.school.name} ({', '.join(conflicts)})"


def _resolve_district(requested: str | None, districts: list[str]) -> str | None:
    if not requested:
        return None
    wanted = requested.upper().removesuffix(" DISTRICT").removesuffix(" CITY").strip()
    return wanted if wanted in districts else None


def _blocking_problem(request: ParentRequest) -> Clarification | None:
    """Only the problems that make a search pointless or wrong stop it. Others become notes."""
    low, high = request.min_fees_ugx, request.max_fees_ugx
    if (low is not None and low < 0) or (high is not None and high <= 0):
        return Clarification(field="fees", question="What fee range per term should I search?")
    if low is not None and high is not None and low > high:
        return Clarification(
            field="fees",
            question=(
                f"Your minimum (UGX {low:,}) is above your maximum (UGX {high:,}). "
                "Which range did you mean?"
            ),
        )
    if request.area and not request.district:
        return Clarification(field="district", question=f"Which district is {request.area} in?")
    has_filter = any(
        (
            request.district,
            high,
            low,
            request.gender,
            request.accommodation,
            request.levels,
            request.admission_status,
            request.curriculums,
            request.facilities,
            request.religions,
            request.school_name,
        )
    )
    if not has_filter:
        return Clarification(
            field="general",
            question=("Could you tell me which district you prefer and your child's class or age?"),
        )
    return None


def _schools(count: int) -> str:
    return f"{count} school" if count == 1 else f"{count} schools"


def _merge_questions(first: Clarification, others: list[Clarification]) -> list[Clarification]:
    # The model's own questions are more specific than the general one, which would repeat them.
    if first.field == "general" and others:
        return others[:MAX_QUESTIONS]
    return [first, *[q for q in others if q.field != first.field]][:MAX_QUESTIONS]


def _not_specified(request: ParentRequest) -> list[str]:
    fields = {
        "location": request.district or request.area,
        "fees": request.min_fees_ugx or request.max_fees_ugx,
        "gender": request.gender,
        "day/boarding": request.accommodation,
        "school level": request.levels,
        "admission status": request.admission_status,
    }
    return [label for label, value in fields.items() if not value]


def _location_notice(schools: list[School], district: str | None) -> list[str]:
    if not district:
        return []
    others = sorted({s.district for s in schools if s.district and s.district.upper() != district})
    if not others:
        return []
    return [
        (
            f"eSchool's {district.title()} filter also returned schools listed in "
            f"{', '.join(others)}. They rank below schools actually in {district.title()}."
        )
    ]


def _describe(filters: Filters) -> dict[str, str]:
    described: dict[str, str] = {}
    if filters.district:
        described["location"] = filters.district
    if filters.counties:
        described["county"] = " or ".join(filters.counties)
    if filters.min_fees is not None:
        described["min_fees"] = f"{filters.min_fees:,}"
    if filters.max_fees is not None:
        described["max_fees"] = f"{filters.max_fees:,}"
    if filters.gender:
        described["gender"] = filters.gender
    if filters.accommodation:
        described["accomadation"] = " or ".join(filters.accommodation)
    if filters.levels:
        described["levels"] = " or ".join(filters.levels)
    if filters.admission_status:
        described["admission_status"] = filters.admission_status
    if filters.curriculums:
        described["curriculums"] = " or ".join(filters.curriculums)
    if filters.facilities:
        described["facilities"] = " or ".join(filters.facilities)
    if filters.religions:
        described["religions"] = " or ".join(filters.religions)
    if filters.school_name:
        described["q"] = filters.school_name
    if filters.sorting:
        described["sorting"] = filters.sorting
    return described
