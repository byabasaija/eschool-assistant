"""Small web app: `uvicorn eschool_assistant.web:app`, then open http://127.0.0.1:8000."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .assistant import Assistant
from .factory import build_assistant
from .models import AssistantResponse

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("eschool_assistant").setLevel(logging.INFO)

app = FastAPI(title="eSchool Search Assistant")
_PAGE = Path(__file__).with_name("static") / "index.html"


class SearchRequest(BaseModel):
    query: str


@lru_cache(maxsize=1)
def get_assistant() -> Assistant:
    return build_assistant()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE.read_text(encoding="utf-8")


@app.post("/api/search")
async def search(body: SearchRequest) -> AssistantResponse:
    return await run_in_threadpool(get_assistant().handle, body.query)
