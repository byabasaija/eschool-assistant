"""HTTP access to eSchool's public pages: the GET search form and school profiles.

Only public URLs are used, never `admin/main/ajax.php`, which the site's own JavaScript calls
but which is not a public contract.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ..models import School
from .parser import SearchPage, UnexpectedResponse, parse_profile, parse_search_page

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://eschool.ug"
USER_AGENT = "eschool-assistant/0.1 (Halvek take-home prototype)"
# The site regularly takes 10-15 seconds per page, so a short read timeout would cause outages.
TIMEOUT = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=60.0)
RETRY_STATUSES = {429, 500, 502, 503, 504}
Params = list[tuple[str, str]]


class SearchUnavailable(RuntimeError):
    """eSchool could not be reached, or kept failing after retries."""


class ESchoolClient:
    def __init__(
        self,
        base_url: str | None = None,
        http: httpx.Client | None = None,
        cache_dir: Path | None = None,
        cache_ttl: int = 3600,
        retries: int = 2,
        backoff: float = 1.0,
    ):
        self.base_url = (base_url or os.getenv("ESCHOOL_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._http = http or httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl
        self._retries = retries
        self._backoff = backoff

    def search_url(self, params: Params) -> str:
        return f"{self.base_url}/find-schools/search?{urlencode(params)}"

    def search(self, params: Params) -> SearchPage:
        return parse_search_page(self._get(self.search_url(params)))

    def load_profile(self, school: School) -> School:
        url = f"{self.base_url}/find-schools/?{urlencode({'slug': school.slug})}"
        return parse_profile(self._get(url), school)

    def _get(self, url: str) -> str:
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        last_error = "no response"
        for attempt in range(self._retries + 1):
            if attempt:
                time.sleep(self._backoff * 2 ** (attempt - 1))
            try:
                response = self._http.get(url)
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
                logger.warning("eSchool request failed (%s): %s", last_error, url)
                continue
            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                raise SearchUnavailable(f"eSchool answered HTTP {response.status_code}")
            if "text/html" not in response.headers.get("content-type", ""):
                raise UnexpectedResponse("eSchool did not return an HTML page")
            self._write_cache(url, response.text)
            return response.text
        raise SearchUnavailable(f"eSchool could not be reached ({last_error})")

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self._cache_ttl:
            return None
        return path.read_text(encoding="utf-8")

    def _write_cache(self, url: str, body: str) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
