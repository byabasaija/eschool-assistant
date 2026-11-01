from __future__ import annotations

from pathlib import Path

import pytest

from eschool_assistant.models import School

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html():
    def read(name: str) -> str:
        return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")

    return read


@pytest.fixture
def make_school():
    def build(**overrides) -> School:
        values = {
            "name": "Test School",
            "slug": "test-school",
            "url": "https://eschool.ug/find-schools?slug=test-school",
            "level": "Secondary",
            "district": "Kampala",
            "admissions_badge": "Admissions Closed",
            "county": "Kampala Central Division",
            "gender": "girls",
            "section": "Boarding",
            "curriculum": "UNEB",
            "religion": "Catholic",
            "fee_min_ugx": 800_000,
            "fee_max_ugx": 900_000,
            "fee_text": "UGX 800,000 - 900,000",
            "facilities": ["Library"],
            "profile_loaded": True,
        }
        return School(**(values | overrides))

    return build
