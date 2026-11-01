import httpx
import pytest

from eschool_assistant.eschool.client import ESchoolClient, SearchUnavailable
from eschool_assistant.eschool.parser import UnexpectedResponse


def make_client(handler, **kwargs) -> ESchoolClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return ESchoolClient(base_url="https://test", http=http, backoff=0, **kwargs)


def test_retries_then_succeeds(fixture_html):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(
            200, text=fixture_html("search_empty"), headers={"content-type": "text/html"}
        )

    assert make_client(handler).search([]).total == 0
    assert len(calls) == 3


def test_timeouts_become_search_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SearchUnavailable):
        make_client(handler, retries=1).search([])


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403)

    with pytest.raises(SearchUnavailable):
        make_client(handler).search([])
    assert len(calls) == 1


def test_non_html_response_is_unexpected():
    def handler(request):
        return httpx.Response(200, json={"error": "nope"})

    with pytest.raises(UnexpectedResponse):
        make_client(handler).search([])


def test_cache_avoids_second_request(fixture_html, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, text=fixture_html("search_empty"), headers={"content-type": "text/html"}
        )

    client = make_client(handler, cache_dir=tmp_path)
    client.search([("location", "KAMPALA")])
    client.search([("location", "KAMPALA")])
    assert len(calls) == 1


def test_failures_are_not_cached(fixture_html, tmp_path):
    responses = [httpx.Response(404)]

    def handler(request):
        if responses:
            return responses.pop()
        return httpx.Response(
            200, text=fixture_html("search_empty"), headers={"content-type": "text/html"}
        )

    client = make_client(handler, cache_dir=tmp_path)
    with pytest.raises(SearchUnavailable):
        client.search([])
    assert client.search([]).total == 0


def test_search_url_encodes_array_parameters():
    client = make_client(lambda request: httpx.Response(200))
    url = client.search_url([("levels[]", "Nursery & Primary")])
    assert url == "https://test/find-schools/search?levels%5B%5D=Nursery+%26+Primary"
