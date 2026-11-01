"""Wires the assistant from the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .assistant import Assistant
from .eschool.client import ESchoolClient
from .interpreter.keyword import KeywordInterpreter


def build_assistant(offline_interpreter: bool = False) -> Assistant:
    load_dotenv()
    client = ESchoolClient(cache_dir=Path(os.getenv("ESCHOOL_CACHE_DIR", ".cache/eschool")))
    keyword = KeywordInterpreter()
    if offline_interpreter or not os.getenv("GEMINI_API_KEY"):
        return Assistant(keyword, client)
    from .interpreter.gemini import GeminiInterpreter

    return Assistant(GeminiInterpreter(), client, fallback=keyword)
