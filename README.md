# eSchool Search Assistant

Parents describe the school they want in ordinary language. The assistant turns that into eSchool
search filters, runs them on [eschool.ug](https://eschool.ug/find-schools/search), checks each
candidate against its public profile, and explains what is confirmed, unconfirmed or not met.

- **[Design explanation](docs/DESIGN.md):** how it works and why
- **[Demonstration](docs/DEMO.md):** example requests with criteria and results
- **[eSchool integration notes](docs/ESCHOOL_INTEGRATION.md):** filter behaviour, findings, limitations

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer.

```bash
uv sync
cp .env.example .env    # then add your GEMINI_API_KEY
```

Without a key the app still runs, using a basic keyword fallback, and says so in every response.
Natural-language handling at the intended quality needs Gemini. A key is free from
[Google AI Studio](https://aistudio.google.com/apikey), with no card required.

| Variable | Purpose | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini API access | none |
| `ESCHOOL_MODEL` | Gemini model | `gemini-3.5-flash-lite` |
| `ESCHOOL_BASE_URL` | eSchool site | `https://eschool.ug` |
| `ESCHOOL_CACHE_DIR` | Page cache (1 hour) | `.cache/eschool` |

## Running

```bash
uv run uvicorn eschool_assistant.web:app
```

Then open http://127.0.0.1:8000 and describe the school you want, for example "A girls' boarding
school in Kampala up to UGX 1 million per term". The JSON API is `POST /api/search` with
`{"query": "..."}`.

If the assistant asks a question, answer it in the box under the question. The answer is added to
your request and the search runs again. The server keeps no conversation between searches.

eSchool can take 10 to 15 seconds per page, so a first search may take a minute or more. Repeat
searches use the cache.

**Regenerate the demonstration**

```bash
uv run python scripts/run_demo.py
```

## Tests

```bash
uv run pytest            # offline: real saved eSchool pages and a fake Gemini transport
uv run pytest -m live    # contract checks against eschool.ug, and Gemini if a key is set
uv run ruff check src tests scripts
```

## Project layout

```
src/eschool_assistant/
  interpreter/      AI boundary: Gemini, plus the keyword fallback
  eschool/          HTTP client and HTML parser
  models.py         Data contracts
  query.py          Criteria to eSchool parameters, and relaxation order
  matching.py       Evidence checks against each school's profile
  assistant.py      Orchestration
  render.py, web.py, static/
tests/              Unit, orchestration and live contract tests
```
