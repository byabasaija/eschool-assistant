from fastapi.testclient import TestClient

from eschool_assistant import web
from eschool_assistant.models import AssistantResponse, Outcome


class Stub:
    def handle(self, query):
        return AssistantResponse(
            query=query, outcome=Outcome.NO_RESULTS, message="none", interpreter="stub"
        )


def test_page_loads():
    assert "eSchool Search Assistant" in TestClient(web.app).get("/").text


def test_page_lets_a_parent_answer_questions():
    assert "Send answer" in TestClient(web.app).get("/").text


def test_search_endpoint(monkeypatch):
    monkeypatch.setattr(web, "get_assistant", lambda: Stub())
    response = TestClient(web.app).post("/api/search", json={"query": "x"})
    assert response.status_code == 200
    assert response.json()["outcome"] == "no_results"


def test_missing_query_is_a_validation_error():
    assert TestClient(web.app).post("/api/search", json={}).status_code == 422
