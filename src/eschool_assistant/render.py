"""Plain-text and Markdown rendering of an `AssistantResponse`."""

from __future__ import annotations

from .models import AssistantResponse, MatchStatus

_SYMBOLS = {
    MatchStatus.MET: "✓",
    MatchStatus.PARTIAL: "~",
    MatchStatus.UNKNOWN: "?",
    MatchStatus.NOT_MET: "✗",
}


def render_markdown(response: AssistantResponse) -> str:
    lines = [f"**Request:** {response.query}", ""]
    lines.append(f"**Outcome:** `{response.outcome.value}`  ")
    lines.append(f"**Interpreted by:** {response.interpreter}")
    lines += ["", response.message, ""]

    if response.applied_filters:
        lines.append("**Search criteria sent to eSchool**")
        lines += [f"- `{k}` = {v}" for k, v in response.applied_filters.items()]
        lines.append("")
    if response.not_specified:
        lines += [f"**Not specified by the parent:** {', '.join(response.not_specified)}", ""]
    if response.criteria and response.criteria.assumptions:
        lines.append("**Assumptions**")
        lines += [f"- {assumption}" for assumption in response.criteria.assumptions]
        lines.append("")
    if response.notices:
        lines.append("**Notes**")
        lines += [f"- {notice}" for notice in response.notices]
        lines.append("")
    questions = [
        q.question for q in response.clarifying_questions if q.question != response.message
    ]
    if questions:
        lines.append("**Questions for the parent**")
        lines += [f"- {question}" for question in questions]
        lines.append("")
    if response.steps:
        lines.append("**Search steps**")
        lines += ["| Step | Schools found |", "| --- | --- |"]
        lines += [f"| {step.description} | {step.total_found} |" for step in response.steps]
        lines.append("")

    for number, match in enumerate(response.matches, 1):
        school = match.school
        heading = f"{number}. [{school.name}]({school.url})"
        facts = [school.level, school.county, school.district, school.admissions_badge]
        lines.append(f"**{heading}**: {', '.join(fact for fact in facts if fact)}")
        lines.extend(
            f"   - {_SYMBOLS[check.status]} {check.criterion}"
            f"{'' if check.required else ' (preference)'}: {check.evidence}"
            for check in match.checks
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
