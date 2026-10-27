# SEARCHOps Testing Guide

How to verify this project end-to-end, what each gate means, and what is
**not** covered by the automated suite.

Everything below was executed while writing this guide; the numbers are real
output, not estimates.

---

## Quick start — run backend + UI

Two terminals, both from the repo root. Verified live: API boots on `:8000`,
UI dev server on `:3000`.

### Terminal 1 — backend API

```bash
cd /c/Users/HP/Desktop/SEARCHOps
env -u PYTHONPATH .venv/Scripts/python.exe -m uvicorn searchops.api.main:app \
  --reload --host 0.0.0.0 --port 8000
```

Or just `make dev` (identical command; the Makefile sets `PYTHON`).

Wait for `Application startup complete.`, then confirm it is really up:

```bash
curl -s http://127.0.0.1:8000/health | python -m json.tool   # components listed
curl -s http://127.0.0.1:8000/docs                           # Swagger UI -> 200
```

### Terminal 2 — frontend UI

```bash
cd /c/Users/HP/Desktop/SEARCHOps/frontend
npm run dev          # Vite on http://localhost:3000, opens a browser
```

The UI calls the API at `http://localhost:8000` by default. To point it
elsewhere, set `VITE_API_BASE_URL` in `frontend/.env` before `npm run dev`
(Vite only exposes `VITE_`-prefixed vars).

```bash
# first run only, if node_modules is missing
npm ci
```

### Production build (optional)

```bash
cd frontend && npm run build     # -> frontend/dist  (verified: builds in ~38s)
npm run preview                 # serve the built bundle on :3000
```

### Ports in use

| Service | Port | Command |
|---|---|---|
| API (uvicorn) | 8000 | `make dev` / uvicorn above |
| UI dev (Vite) | 3000 | `npm run dev` |
| Redis (optional) | 6379 | `make docker-up` |

### If the UI loads but requests fail

Almost always the API is not running, or CORS. The API enables CORS for the
Vite origin; if you changed the UI port, restart the API so the allow-list is
re-read from settings. Check `curl http://127.0.0.1:8000/health` first — if
that fails, the problem is the backend, not the frontend.

---

## 1. Environment setup (do this once)

The project ships its own virtualenv at `.venv/`. Use **that** interpreter for
everything — a system Python or a Hermes-managed env will fail to import
`pydantic_core` (a native extension compiled for the project's Python).

```bash
cd /c/Users/HP/Desktop/SEARCHOps

# Confirm the interpreter works
.venv/Scripts/python.exe --version          # -> Python 3.12.7
```

### Credentials

`robots.txt`, wall detection, dedup, the frontier, BM25 relevance, claim dedup
and sampled verification all run with **zero API keys**. The premium tiers are
the only key-dependent parts.

| Variable | Needed for | If absent |
|---|---|---|
| `REDIS_URL` | URL seen-set, robots cache, persistent dedup | Degrades to in-memory — pipeline still runs |
| `QDRANT_URL` | Vector retrieval | RAG features skip |
| `NEO4J_URI` / `NEO4J_PASSWORD` | Knowledge graph | KG features skip |
| `OPENROUTER_API_KEY` (or equivalent) | **Any LLM call** | All LLM stages no-op; scraping still testable |
| `BRIGHTDATA_*` | Premium scraping tiers | Tiers are skipped, free tiers still work |
| `FIRECRAWL_API_KEY` | Firecrawl tier | Tier skipped |

`.env` is gitignored. There is no `.env.example` — copy nothing, just create
`.env` with only the keys you have.

```bash
# Verify what you configured (does not print secrets)
.venv/Scripts/python.exe scripts/check_env.py
```

### Services (optional for most tests)

```bash
# Redis is the only service most tests benefit from
docker run -d --name searchops-redis -p 6379:6379 redis:7-alpine
```

The full stack via compose (added this cycle — **config-validated but the
containers have not been run**):

```bash
docker compose config    # validates without starting anything
docker compose up -d     # postgres, redis, qdrant, neo4j
```

---

## 2. The four gates (the contract)

Run these from the repo root. **All four must pass.**

```bash
# Strip PYTHONPATH so the project venv is not shadowed
export PYTHONPATH=""

# 1. Lint
.venv/Scripts/python.exe -m ruff check src/ tests/

# 2. Types
.venv/Scripts/python.exe -m mypy src/

# 3. Tests + coverage (gate is 75%)
.venv/Scripts/python.exe -m pytest -p no:cacheprovider \
  --cov=src/searchops --cov-fail-under=75 -q --timeout=60 \
  -p no:logging --ignore=tests/test_e2e_console.py

# 4. All of the above
make lint && make typecheck && make test
```

### Current baseline (verified 2026-10-11)

| Gate | Result |
|---|---|
| ruff | All checks passed |
| mypy | Success — 219 source files, 0 errors |
| pytest | **1162 passed**, coverage **79.76%** |

> **`PYTHONPATH` matters.** Without stripping it, a Hermes-managed venv
> shadows the project's `site-packages` and imports fail confusingly. Always
> use `env -u PYTHONPATH` or `export PYTHONPATH=""`.

> **`tests/test_e2e_console.py` is excluded above** because it spawns a real
> server and needs Playwright browsers (see §5).

---

## 3. What the new funnel stages actually do

Test them in this order — each stage is independent and testable in isolation.

### Stage 0 — robots.txt compliance

```bash
.venv/Scripts/python.exe -m pytest tests/unit/scraping/test_robots.py -v
.venv/Scripts/python.exe -m pytest tests/unit/scraping/test_pipeline_robots_gate.py -v
```

**Verify by behaviour, not just tests** — a disallowed URL must never reach a
scraper:

```python
import asyncio
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.scraping.robots import RobotsCache

async def fetcher(url):                      # pretend robots.txt forbids /private
    return "User-agent: *\nDisallow: /private\n"

pipeline = ScrapingPipeline(robots_cache=RobotsCache(fetcher=fetcher))
from searchops.core.interfaces.scraper import ScrapeRequest
r = await pipeline.execute(ScrapeRequest(url="https://example.com/private/x"))
print(r.status_code, r.metadata.get("skipped"))   # -> 403 robots
```

**Also verify fail-open** — a robots.txt that cannot be fetched must NOT block
the scrape. That is the whole point of the gate.

### Stage 0b — wall detection (the 200-that-isn't)

```bash
.venv/Scripts/python.exe -m pytest tests/unit/scraping/test_wall_detector.py -v
```

The important test is the negative one: a genuine article that *mentions*
cookies must not be flagged as a consent wall.

```python
from searchops.scraping.wall_detector import detect_wall, WallClass
print(detect_wall("<html><body>We value your privacy. This website uses cookies.</body></html>").wall_class)
# -> consent
```

### Stage 4 — crawl frontier

```bash
.venv/Scripts/python.exe -m pytest tests/unit/scraping/test_frontier.py -v
```

Verify precision mode does **not** crawl:

```python
import asyncio
from searchops.scraping.frontier import CrawlFrontier, CrawlConfig

class Seen:
    def __init__(self): self.s = set()
    async def seen(self, url, *, force=False):
        if url in self.s: return True
        self.s.add(url); return False

async def main():
    f = CrawlFrontier(seen=Seen(), config=CrawlConfig(max_depth=1))
    await f.add_seeds(["https://a.com/1"])
    p = await f.next_url()
    await f.record_links(p.url, '<a href="/2">x</a>')
    print(f.size)          # -> 0  (precision mode does not expand)

    f2 = CrawlFrontier(seen=Seen(), config=CrawlConfig(max_depth=2))
    await f2.add_seeds(["https://a.com/1"])
    p2 = await f2.next_url()
    await f2.record_links(p2.url, '<a href="/2">x</a>')
    print(f2.size)         # -> 1  (corpus mode crawls)
asyncio.run(main())
```

### Stage 5 — BM25 relevance filter

```bash
.venv/Scripts/python.exe -m pytest tests/unit/knowledge/test_relevance.py -v
```

Verify off-topic pages are dropped **before** any LLM call:

```python
from searchops.knowledge.evaluation.relevance import filter_by_relevance
pages = [
    {"url": "on",  "content": "Vector databases store embeddings and support approximate nearest neighbour search."},
    {"url": "off", "content": "The history of the bicycle begins in nineteenth century Europe."},
]
kept, dropped, stats = filter_by_relevance(pages, "vector database nearest neighbour search")
print(stats)   # -> pages_kept 1, pages_dropped 1
```

### Stage 6 — schema extraction

```bash
.venv/Scripts/python.exe -m pytest tests/unit/scraping/test_schema_extraction.py -v
```

Covers prose-wrapped JSON, fenced JSON, truncated JSON, and schema violations
— all of which must return `None` rather than raise.

### Stages 7 & 8 — claim dedup and sampled verification

```bash
.venv/Scripts/python.exe -m pytest tests/unit/orchestration/test_claim_pipeline.py -v
```

The honesty check that matters most:

```python
from searchops.orchestration.nodes.verifier import SamplingPlan
print(SamplingPlan(rate=0.25).describe())
# -> "Sampled verification: roughly 25% of claims were checked, plus every
#     high-stakes claim. The rest remain unverified."
```

A sampled run must **never** claim full verification.

---

## 4. Two modes

```bash
.venv/Scripts/python.exe -m pytest tests/unit/orchestration/test_scrape_node_modes.py -v
.venv/Scripts/python.exe -m pytest tests/unit/core/test_research_mode.py -v
```

| | `precision` | `corpus` |
|---|---|---|
| max_pages | 10 | 50 |
| max_depth | 1 (no crawl) | 3 |
| relevance filter | off | on |
| claim dedup | conservative | aggressive |
| verify sampling | 1.0 (all) | 0.25 |
| report shape | narrative | dataset |

**Regression guard:** any change that makes precision mode follow links is a
bug — `test_depth_one_does_not_crawl` and `test_free_tier_cap_still_applies`
exist to catch it.

---

## 5. Live server test (the real end-to-end)

The frontend E2E test needs a browser. Install once:

```bash
.venv/Scripts/python.exe -m playwright install chromium
```

Then, from a **fresh port** (a stale listener serves the old build):

```bash
# 1. Build the frontend
cd frontend && npm ci && npm run build && cd ..

# 2. Start the API on an unused port
.venv/Scripts/python.exe -m uvicorn searchops.api.main:app --port 8811 &

# 3. Health check — must report real components, not an empty list
curl -s http://127.0.0.1:8811/health       | python -m json.tool
curl -s http://127.0.0.1:8811/health/ready | python -m json.tool
```

The health router is mounted at `/health`, so readiness is **`/health/ready`**
(not `/ready`). `/health` returning `"components": []` is a **regression** — it
means the real component probes were lost. `/health/ready` returns 503 and
names the failing dependency when a service is down; 200 when all configured
components are healthy.

### Run a job

```bash
curl -s -X POST http://127.0.0.1:8811/api/v1/research/ \
  -H 'Content-Type: application/json' \
  -d '{"query":"what is a vector database","depth":"standard","mode":"precision"}' \
  | python -m json.tool
```

An unknown mode must be rejected:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8811/api/v1/research/ \
  -H 'Content-Type: application/json' \
  -d '{"query":"test query","mode":"exhaustive"}'
# -> 422
```

Then watch the funnel counters come back:

```bash
curl -s http://127.0.0.1:8811/api/v1/research/<job_id> | python -m json.tool
```

Look for `frontier_stats`, `relevance_stats`, and `verification_stats`. In
precision mode `frontier_stats` is populated but no links were followed; in
corpus mode `relevance_stats.pages_dropped` should be non-zero on a broad query.

---

## 6. What is NOT covered — test these yourself

These are honest gaps. Do not assume they work because the suite is green.

| Area | Status |
|---|---|
| **Live crawling** | No crawl has hit the real internet. Frontier/dedup/wall tests use fakes. |
| **SearXNG / Playwright search quality** | Routing is proven; live result quality is **not measured**. |
| **`extract_knowledge` entity yield** | The node is wired and executes, but entity counts on real pages are unmeasured. |
| **Premium tiers** (BrightData, Firecrawl) | Code paths exist; not exercised without credentials. Note `brightdata_serp` returns 422 "Unknown zone" — a config issue on this machine. |
| **`docker compose up`** | `docker compose config` passes; containers have not been started. |
| **Perf claims** | "Browser cold-start <50ms", "10–20x latency reduction", "67% token reduction" are **NOT MEASURED** — no benchmark exists. Do not quote them. |
| **`extract_schema` consumption** | `ScrapeRequest.extract_schema` exists and validates, but the LLM extraction node does not read it yet. |
| **BM25 on paraphrase** | Lexical only. "nearest-neighbour lookup" scores lower than "approximate nearest neighbour search". A vector index would fix this; it is deliberately absent. |
| **Redis Bloom** | `BF.*` is unavailable on this server; the seen-set uses exact per-URL `SETNX` instead. |

### A quick manual crawl check

```python
import asyncio
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.core.interfaces.scraper import ScrapeRequest

async def main():
    p = ScrapingPipeline()
    r = await p.execute(ScrapeRequest(url="https://example.com"))
    print(r.status_code, len(r.markdown or ""), r.metadata.get("wall_class"))
    print(p.get_stats()["robots"], p.get_stats()["tier_bandit"])
asyncio.run(main())
```

Expect `200`, non-empty markdown, no `wall_class`. Then try a URL you know
returns a consent wall and confirm `wall_class` is set and the status is 403.

---

## 7. Failure runbook

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: pydantic_core` | Wrong interpreter | Use `.venv/Scripts/python.exe`; strip `PYTHONPATH` |
| `Event loop is closed` | Stale transport pool | Fixed — pool now tracks its loop. If it recurs, restart the process. |
| Tests pass alone, fail in suite | Shared `get_rate_limiter()` singleton | Inject `rate_limiter=DomainRateLimiter()` in new tests |
| `RuntimeError: Event loop is closed` in scraping | Same as above | As above |
| `/health` shows empty components, or redis reported unhealthy with an `AttributeError` | Probe wiring lost, or probe not reaching through the `RedisCache` wrapper | Regression — check `shared/contracts/health_probes.py`. The redis probe resolves `ping()` on the wrapper *then* `.client`. |
| Job fails with `'NoneType' has no attribute 'get'` | Empty state delta | Fixed — `gap_analyst` empty returns are guarded |
| Report is empty | `report_quality` tells you why | Read `report_quality` / `report_user_message` on the job |
| Coverage gate fails at 75% | Real gap | Run `--cov-report=term-missing` and target the listed lines |

---

## 8. Adding a test (the house style)

1. **Write the failing test first.** Confirm it fails for the *right* reason
   (missing feature, not a typo).
2. **Watch it pass** before moving on.
3. **Prefer real objects over mocks.** Fake only at true boundaries (HTTP,
   Redis, browser).
4. **Pin gaps honestly.** If behaviour is wrong but out of scope, write
   `test_<thing>_does_not_<claim>` documenting it — do not assert the desired
   behaviour.
5. **Never weaken a gate** to make a test pass. Fix the code, or fix the test
   if the test was wrong.
6. **Isolate global state.** Anything from a module-level singleton
   (`get_rate_limiter`, `get_transport_pool`, `get_redis_client`) must be
   injected or monkeypatched.

```bash
# Fast inner loop while developing
.venv/Scripts/python.exe -m pytest tests/unit/scraping/test_frontier.py -q -p no:cacheprovider --no-cov
```
