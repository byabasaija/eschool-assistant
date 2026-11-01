"""Contract checks against the real services: `uv run pytest -m live`.

These catch site changes that fixtures cannot. They are slow and need network access.
"""

import os

import pytest
from dotenv import load_dotenv

from eschool_assistant.eschool.client import ESchoolClient

pytestmark = pytest.mark.live


def test_search_contract():
    page = ESchoolClient().search([("location", "KAMPALA"), ("gender", "girls")])
    assert page.total > 0
    assert page.schools
    assert "KAMPALA" in page.districts
    assert page.counties


def test_profile_contract():
    client = ESchoolClient()
    school = client.search([("q", "Nsambya")]).schools[0]
    assert client.load_profile(school).profile_loaded


load_dotenv()


@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="needs GEMINI_API_KEY")
def test_gemini_interprets_the_brief_example():
    from eschool_assistant.interpreter.gemini import GeminiInterpreter
    from eschool_assistant.models import Gender, Importance

    request = GeminiInterpreter().interpret(
        "I'm looking for a girls' boarding school in Kampala, preferably around Central "
        "Division, charging no more than UGX 1 million per term and currently admitting "
        "Senior One students."
    )
    assert request.district == "Kampala"
    assert request.area_importance == Importance.PREFERRED
    assert request.max_fees_ugx == 1_000_000
    assert request.gender == Gender.GIRLS
