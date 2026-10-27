# SEARCHOps — Enterprise Autonomous Technology Intelligence Platform

> A production-grade autonomous AI research platform that continuously monitors technological
> developments, coordinates specialized AI agents, builds a living knowledge graph, and
> generates highly accurate research intelligence reports.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          SEARCHOps Platform                              │
│                                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Planner  │  │  Search  │  │Firecrawl │  │Knowledge │  │  Report  │  │
│  │  Agent   │  │  Agent   │  │  Agent   │  │  Graph   │  │Generator │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │              │              │              │              │        │
│  ─────┼──────────────┼──────────────┼──────────────┼──────────────┼────  │
│              A2A Protocol + LangGraph Orchestration                       │
│  ─────┼──────────────┼──────────────┼──────────────┼──────────────┼────  │
│       │              │              │              │              │        │
│  ┌────▼─────────────────────────────────────────────────────────▼─────┐  │
│  │                      Memory Systems                                 │  │
│  │  Redis (exec) │ PostgreSQL (workflow) │ Qdrant (vector) │ Neo4j    │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone and enter
git clone <repo>
cd searchops

# 2. Install uv (if not present)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Create virtual environment and install
uv venv
uv sync

# 4. Copy env template
cp .env.example .env
# Edit .env with your credentials

# 5. Run tests
uv run pytest tests/unit -m unit
```

## Running without Docker (Offline Mode)

You can run the entire test suite and development servers completely database-free/without Docker running by configuring lightweight local/mock fallbacks in your `.env` file:

1. **Local In-Memory Qdrant**: Set Qdrant host to use memory mode:
   ```env
   QDRANT_HOST=:memory:
   ```
2. **Mock Neo4j Graph Database**: Set Neo4j URI to bypass network handshakes:
   ```env
   NEO4J_URI=mock
   ```
3. **Disable Telemetry Trace Exports**: Silence OTel connection refused warnings:
   ```env
   OTEL_TRACES_EXPORTER=none
   ```

Then, execute testing without any active Docker containers:
```bash
# Run unit tests
uv run pytest tests/unit/ --no-cov

# Run Playwright E2E browser tests
uv run pytest tests/test_e2e_console.py --no-cov
```

## Development

```bash
# Install pre-commit hooks
pre-commit install

# Run linting
make lint

# Run type checking
make typecheck

# Run all unit tests
make test-unit

# Run integration tests (requires infrastructure)
make test-integration

# Format code
make fmt

# run server
uv run uvicorn searchops.api.main:app --host 127.0.0.1 --port 8000
```

## Phase Completion Status

All 15 phases are implemented. Status reflects the code in the tree, not the
original roadmap.

| Phase | Description | Status | Evidence |
|-------|-------------|--------|----------|
| 1 | Project Bootstrap, Config, DI, Logging, Observability | ✅ Complete | `bootstrap/`, `config/`, `core/logging/`, `core/observability/` |
| 2 | Database Layer, Repositories, Memory Systems | ✅ Complete | `infrastructure/database/`, `infrastructure/cache/` |
| 3 | A2A Framework | ✅ Complete | `a2a/` (protocol, router, transports) |
| 4 | MCP Framework | ✅ Complete | `mcp/` |
| 5 | Firecrawl Service | ✅ Complete | `scraping/firecrawl.py` (1 of 8 tiers) |
| 6 | LangGraph Orchestration | ✅ Complete | `orchestration/graphs/deep_research.py` |
| 7 | Research Agents | ✅ Complete | `orchestration/nodes/` |
| 8 | Knowledge Graph | ✅ Complete | `knowledge/`, `infrastructure/graph/` |
| 9 | Verification System | ✅ Complete | `orchestration/nodes/verifier.py`, `critic.py` |
| 10 | Reporting Engine | ✅ Complete | `orchestration/nodes/report_writer.py` |
| 11 | Monitoring System | ✅ Complete | `core/observability/metrics.py`, `tracer.py` |
| 12 | Security Hardening | ✅ Complete | `middleware/auth.py`, `rate_limiter.py` |
| 13 | Deployment | ✅ Complete | `Makefile`, `scripts/check_env.py` |
| 14 | Optimization | ✅ Complete | tiered scraping, caching, pruning, dedup |
| 15 | Documentation | ✅ Complete | `docs/` (4 documents) |

### Current quality gates

| Gate | Command | Status |
|------|---------|--------|
| Tests | `make test-unit` | ✅ 1544 passed |
| Coverage | (via pytest addopts) | ✅ 81.4% (gate: 75%) |
| Lint | `make lint` | ✅ all checks passed |
| Format | `make fmt-check` | ✅ 355 files |
| Types | `make typecheck` | ✅ mypy strict, 0 errors / 243 files |

`make ci` runs all of the above; `.github/workflows/ci.yml` runs them on every push, plus the
frontend build, an `npm audit` of production dependencies and a Docker build.
Deploying: see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

> Coverage is enforced by `--cov-fail-under=75` in `pyproject.toml`. Reaching it
> required fixing real bugs (dead Markdown renderer, broken page reuse, an
> `AttributeError` in pool shutdown) rather than lowering the gate.

## Authentication

- **People** sign in with [Clerk](https://clerk.com). Set
  `CLERK_PUBLISHABLE_KEY` in `.env` and the same value as
  `VITE_CLERK_PUBLISHABLE_KEY` in `frontend/.env.local`. The API verifies each
  Clerk session token against the instance's public JWKS (issuer, expiry,
  signature and `azp` origin); the Clerk secret key is not needed.
- **Scripts and services** send `X-API-Key: $SEARCHOPS_API_KEY` (optional).
- **Ownership:** a report belongs to the Clerk user who started it; other users
  get a 404 for it. API-key callers can read every report.
- The WebSocket carries the Clerk token as the subprotocol `bearer.<token>`
  (browsers cannot set socket headers); the server never echoes it back.

## How evidence and verification work

1. **Passages, not page prefixes.** Every fetched page is chunked into
   paragraph-aligned passages (`orchestration/passages.py`) and indexed with
   BM25. The extractor reads each page's passages most relevant to the
   question, wherever they sit on the page.
2. **Grounded citations.** Each claim's citation URL is the page it came from
   (set in code, never by the model), and its quote must be found verbatim in
   that page. A claim whose quote cannot be found is dropped.
3. **Cross-source verification.** The verifier retrieves passages from *other*
   pages for every claim; the model labels each passage supports /
   contradicts / irrelevant, and the verdict (supported / contested / refuted)
   is computed from those labels in code. Supporting and contradicting source
   URLs are stored on each claim.
4. **Resolvable citations.** The report writer numbers sources once for the
   whole report; every `[n]` resolves to the Sources list, and unresolvable
   markers are removed. Refuted claims never reach the prose; contested
   claims must be written as uncertain.
5. **Reflection that adds evidence.** Critic gaps become new queries; each
   round searches only queries not yet run and fetches only unread URLs.
6. **Measured quality.** The evaluation node reports grounding rate, citation
   coverage, multi-source support, claim status mix and hallucination rate
   against the passage corpus. Per-node latency and token cost are measured
   from the LLM router.

Finished reports are archived to `$SEARCHOPS_REPORT_DIR` (default
`data/reports/`) and survive Redis expiry and restarts.
`tests/unit/orchestration/test_graph_end_to_end.py` runs the real graph with
stubbed I/O and pins all of the above.

## Known limitations

- Retrieval is lexical (BM25). Qdrant and Neo4j adapters exist but are not
  used by the research pipeline; dense/graph retrieval is not wired in.
- Default models in `.env.example` are cheap tiers; report quality scales with
  the model you configure.

Scraping-layer limitations are documented in `docs/scraping_audit_report.md`:

- `MinHashDeduplicator` is implemented but **not wired into the pipeline**.
- No Bloom-filter URL seen-set.
- Browser warm-vs-cold latency claims are unmeasured.
- A misconfigured search credential degrades silently instead of surfacing.

## License

Proprietary — All rights reserved.