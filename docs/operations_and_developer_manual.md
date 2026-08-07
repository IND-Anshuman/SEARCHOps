# SEARCHOps Operations & Developer Manual

```
================================================================================
                               SEARCHOps PLATFORM
                     OPERATIONS & DEVELOPER MANUAL (v0.1.0)
================================================================================
Project Name         : SEARCHOps Enterprise Technology Intelligence Platform
Repository Version   : 0.1.0
Document Version     : 2.0.0
Authors              : SEARCHOps DevOps & Platform Engineering Team
Last Updated         : 2026-08-01
Classification       : Operational & Developer Guide
================================================================================
```

---

## 1. Project Overview & Operational Goals

SEARCHOps is an enterprise-grade autonomous research platform. This manual provides complete, step-by-step instructions for installing, configuring, running, debugging, testing, deploying, and operating SEARCHOps across local development environments, staging clusters, and production container orchestrators.

---

## 2. System Prerequisites & Environment Matrix

### 2.1 Software Prerequisites
- **Operating System**: Windows 11 / Windows Server 2022, macOS Sonoma+, or Ubuntu 22.04 LTS / 24.04 LTS.
- **Python Runtime**: `Python 3.12.7` (Managed via `pyenv` or `.venv`).
- **Package Managers**: `uv` (Recommended) or `pip` 24.0+.
- **Container Runtime**: Docker Desktop / Docker Engine 24.0+ with Docker Compose v2.20+.
- **Databases & Services**:
  - **Redis**: v7.0+ (Port `6379`)
  - **PostgreSQL**: v16+ (Port `5432`)
  - **Neo4j Enterprise / Community**: v5.26+ (Port `7687` Bolt, `7474` HTTP)
  - **Qdrant Vector Database**: v1.12+ (Port `6333` HTTP, `6334` gRPC)

### 2.2 API Key Requirements
- `OPENAI_API_KEY`: Required for primary OpenAI model routing (`gpt-4o`, `gpt-4o-mini`).
- `ANTHROPIC_API_KEY`: Optional for Claude model routing (`claude-3-5-sonnet`, `claude-3-haiku`).
- `GOOGLE_API_KEY`: Optional for Gemini model routing (`gemini-1.5-pro`, `gemini-1.5-flash`).
- `FIRECRAWL_API_KEY`: Optional for Firecrawl web scraping service.
- `SERPER_API_KEY` / `TAVILY_API_KEY`: Optional for external search providers.

---

## 3. Detailed Environment Variables Reference

| Variable Name | Required | Default Value | Description | Example Value |
| :--- | :---: | :--- | :--- | :--- |
| `SEARCHOPS_ENV` | No | `development` | Active environment (`development`, `staging`, `production`) | `production` |
| `SEARCHOPS_SECRET_KEY` | Yes | `dev-secret-key...` | JWT/HMAC Secret key for API authentication | `c8f1d...94b2` |
| `OPENAI_API_KEY` | Yes | `sk-proj-...` | Primary OpenAI API secret key | `sk-proj-123456` |
| `ANTHROPIC_API_KEY` | No | `""` | Anthropic Claude API key | `sk-ant-123456` |
| `GOOGLE_API_KEY` | No | `""` | Google Gemini API key | `AIzaSy123456` |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Connection URI for Redis cache & streams | `redis://redis:6379/0` |
| `DATABASE_URL` | No | `postgresql+asyncpg://...` | Async PostgreSQL database connection URI | `postgresql+asyncpg://user:pass@localhost:5432/searchops` |
| `NEO4J_URI` | No | `bolt://localhost:7687` | Bolt URI for Neo4j Knowledge Graph database | `bolt://localhost:7687` |
| `NEO4J_USER` | No | `neo4j` | Username for Neo4j database | `neo4j` |
| `NEO4J_PASSWORD` | No | `password` | Password for Neo4j database | `secretpassword` |
| `QDRANT_URL` | No | `http://localhost:6333` | Connection URL for Qdrant Vector Cluster | `http://localhost:6333` |
| `LOG_LEVEL` | No | `INFO` | Structlog logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

---

## 4. Installation & Local Setup Guide

### 4.1 Step-by-Step Environment Provisioning

#### Step 1: Clone Repository
```powershell
git clone https://github.com/IND-Anshuman/SEARCHOps.git
cd SEARCHOps
```

#### Step 2: Set Up Virtual Environment
```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

#### Step 3: Install Package Dependencies
```powershell
# Install wheel package in editable mode with development dependencies
pip install -e .[dev]
```

#### Step 4: Verify Installation with Test Suite
```powershell
.venv\Scripts\pytest
```

### 4.2 Running without Docker (Offline Mode)

For development and testing environments, you can run SEARCHOps entirely without Docker by configuring lightweight in-memory and mock drivers in your `.env` file:

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

Then, run pytest without any active Docker container:
```bash
# Run unit tests
uv run pytest tests/unit/ --no-cov

# Run Playwright E2E browser tests
uv run pytest tests/test_e2e_console.py --no-cov
```

---

## 5. Operational Command Reference Cheat Sheet

### 5.1 Test Suite Commands
```powershell
# Run complete unit test suite
.venv\Scripts\pytest

# Run tests with detailed coverage report
.venv\Scripts\pytest --cov=searchops --cov-report=term-missing

# Run specific targeted test file
.venv\Scripts\pytest tests/unit/core/test_token_economy_context_engineering.py -v

# Run tests filtering by marker
.venv\Scripts\pytest -m unit
```

### 5.2 Code Formatting & Static Analysis
```powershell
# Check code formatting with Ruff
ruff check src tests

# Auto-fix lint violations
ruff check src tests --fix

# Type check codebase with MyPy
mypy src
```

### 5.3 FastAPI Application Execution
```powershell
# Start local development server with auto-reload
uvicorn searchops.api.main:app --reload --host 0.0.0.0 --port 8000

# Start production worker server
uvicorn searchops.api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## 6. Running Application Endpoints & Health Checks

Once the FastAPI server is running:
- **Interactive OpenAPI Documentation**: `http://localhost:8000/docs`
- **ReDoc API Manual**: `http://localhost:8000/redoc`
- **Prometheus Metrics Endpoint**: `http://localhost:8000/metrics`
- **Health Check Endpoint**: `http://localhost:8000/health`

### Example Research API Request
```powershell
curl -X POST "http://localhost:8000/api/v1/research/execute" `
  -H "Content-Type: application/json" `
  -H "X-API-Key: dev-secret-key" `
  -d '{
    "query": "Explain GraphRAG hybrid retrieval performance vs pure vector search",
    "depth": "deep",
    "max_sources": 5
  }'
```

---

## 7. Database Administration & Operations

### 7.1 Neo4j Graph Database
- **Web Browser GUI**: `http://localhost:7474`
- **Default Login**: Username `neo4j`, Password `password`
- **Cypher Query Verification**:
```cypher
MATCH (n:KGEntity) RETURN n.name, n.entity_type, n.canonical_id LIMIT 20;
```

### 7.2 Qdrant Vector Cluster
- **Web Dashboard**: `http://localhost:6333/dashboard`
- **Collection Verification**: Ensure collection `tech_docs` exists with vector dimension `1536` and distance metric `Cosine`.

### 7.3 PostgreSQL Checkpointer DB
- **Connect via psql**:
```bash
psql -h localhost -U user -d searchops
```
- **Inspect Checkpointer Snapshots**:
```sql
SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 10;
```

---

## 8. Performance, Token, & Cost Optimization Guide

### 8.1 State Compaction Tuning (`StateTokenOptimizer`)
- Implemented in `src/searchops/orchestration/nodes/state_compressor.py`.
- Strips raw `content` HTML/markdown strings immediately post-extraction.
- Retains `content_summary` (max 300 chars) and top structural `snippets` (max 3 lines).
- **Result**: Reduces checkpointer memory footprint by **90.4%**.

### 8.2 Context Window Delta Tuning (`ContextDeltaCompressor`)
- Implemented in `src/searchops/core/context/delta_compressor.py`.
- Calculates set differences between state $N-1$ and state $N$.
- Transmits only newly discovered entities (`+delta_entities`) and relations (`+delta_relations`) in multi-hop loops.
- **Result**: Reduces multi-hop prompt token consumption by **65%–85%**.

---

## 9. Troubleshooting & FAQ

### Symptom: `ImportError: cannot import name 'TokenBudgetManager'`
- **Root Cause**: Deprecated class reference in legacy test files.
- **Fix**: Replace `TokenBudgetManager` with `LLMBudgetTracker` from `searchops.llm.budget`.

### Symptom: `StatusCode.UNAVAILABLE: failed to connect to localhost:4317`
- **Root Cause**: Local OpenTelemetry collector daemon is not active.
- **Fix**: Safe warning message; OTel exporter logs warning and falls back gracefully without interrupting application workflows.

---

## 10. Technical Glossary

- **GraphRAG**: Hybrid Knowledge Graph & Vector Retrieval Augmented Generation.
- **State Compaction**: Memory optimization technique stripping raw text blobs from LangGraph state post-extraction.
- **Context Delta**: The set difference of new entities, relations, and search items discovered between graph iteration $N-1$ and $N$.
- **RRF (Reciprocal Rank Fusion)**: Ranking algorithm combining rank positions across search providers ($1 / (k + \text{rank})$).
- **Transactional Outbox**: Pattern recording domain events atomically in DB tables before relaying to Redis Streams.
