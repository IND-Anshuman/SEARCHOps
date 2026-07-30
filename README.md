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

# 5. Run bootstrap check
uv run python scripts/check_env.py

# 6. Run tests
uv run pytest tests/unit -m unit
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
