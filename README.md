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

# 6. Run tests
uv run pytest tests/unit -m unit

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

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Project Bootstrap, Config, DI, Logging, Observability | ✅ Complete |
| 2 | Database Layer, Repositories, Memory Systems | 🔄 Planned |
| 3 | A2A Framework | 🔄 Planned |
| 4 | MCP Framework | 🔄 Planned |
| 5 | Firecrawl Service | 🔄 Planned |
| 6 | LangGraph Orchestration | 🔄 Planned |
| 7 | Research Agents | 🔄 Planned |
| 8 | Knowledge Graph | 🔄 Planned |
| 9 | Verification System | 🔄 Planned |
| 10 | Reporting Engine | 🔄 Planned |
| 11 | Monitoring System | 🔄 Planned |
| 12 | Security Hardening | 🔄 Planned |
| 13 | Deployment | 🔄 Planned |
| 14 | Optimization | 🔄 Planned |
| 15 | Documentation | 🔄 Planned |

## License

Proprietary — All rights reserved.
Analyze the runtime latency and memory overhead of DeepSeek-V3 vs Llama-3-405B in speculative decoding pipelines.