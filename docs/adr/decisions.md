# ADR-001: Clean Architecture with Hexagonal Ports & Adapters

**Date**: 2026-07-29  
**Status**: Accepted  
**Deciders**: Platform Architecture Team

---

## Context

We are building an autonomous AI research platform that will eventually support:
- 35+ specialized AI agents
- 4 independent memory systems
- LangGraph orchestration with 9 graphs
- 3 external scraping backends
- Real-time streaming and SSE
- Thousands of concurrent users

The architecture must support horizontal scaling, independent deployability of agents, and the ability to swap any infrastructure component (LLM provider, vector store, graph database) without touching business logic.

## Decision

We adopt **Clean Architecture** with **Hexagonal (Ports and Adapters)** pattern with strict layer enforcement:

```
domain/ → application/ → infrastructure/ (via ports in core/interfaces/)
                       → orchestration/   (calls application/ only)
api/    → application/ (never domain/ or infrastructure/ directly)
```

### Layer responsibilities:

| Layer | Allowed imports | Forbidden imports |
|-------|----------------|-------------------|
| `domain/` | `core/`, `shared/`, `typing/` | `application/`, `infrastructure/`, `orchestration/`, `agents/` |
| `application/` | `domain/`, `core/`, `shared/` | `infrastructure/`, `orchestration/` |
| `infrastructure/` | `domain/`, `core/`, `shared/` | `application/`, `orchestration/` |
| `orchestration/` | `application/`, `core/`, `shared/` | `domain/`, `infrastructure/` |
| `agents/` | `application/`, `core/`, `shared/` | `infrastructure/` (directly) |
| `api/` | `application/`, `core/`, `shared/` | `domain/`, `infrastructure/` |

## Consequences

**Positive:**
- Business logic is fully testable without any infrastructure
- LangGraph (orchestration engine) can be replaced with Temporal or Prefect without touching business logic
- Each layer can be independently scaled and deployed
- New agents can be added without modifying existing code (Open/Closed Principle)

**Negative:**
- More files and indirection than a monolithic approach
- Requires discipline from all contributors to not violate layer boundaries

---

# ADR-002: Pydantic v2 as the Universal Type System

**Date**: 2026-07-29  
**Status**: Accepted

## Context

The platform has many data transformation points: API requests → commands → domain entities → events → repository models → API responses. We need a consistent approach to validation and serialization.

## Decision

Pydantic v2 is used as the universal type system:
- All DTOs, schemas, contracts, and value objects use Pydantic BaseModel
- Domain events and commands use Pydantic (frozen=True)
- All API request/response bodies use Pydantic
- All configuration uses pydantic-settings

## Consequences

- Consistent validation across all layers
- Auto-generated JSON Schema for OpenAPI docs
- 10-50x faster than Pydantic v1 due to Rust core
- First-class FastAPI integration

---

# ADR-003: LangGraph as Pure Orchestration Engine

**Date**: 2026-07-29  
**Status**: Accepted

## Context

LangGraph is powerful but can easily become a business logic dumping ground if nodes do too much.

## Decision

LangGraph is used **exclusively** as an orchestration engine:
- Graph nodes contain **zero** business logic
- Nodes call `application/` use cases and handlers
- All state management is via typed TypedDict state classes in `orchestration/states/`
- All business decisions are in `domain/` and `application/`

```python
# BAD: Business logic in a node
async def search_node(state: ResearchState) -> ResearchState:
    results = await httpx.get(f"https://api.tavily.com/search?q={state.query}")
    # ... processing, deduplication, ranking ...
    return state

# GOOD: Node delegates to application layer
async def search_node(state: ResearchState) -> ResearchState:
    use_case = container.search_use_case
    results = await use_case.execute(SearchCommand(query=state.query))
    return state.model_copy(update={"search_results": results})
```

## Consequences

- If LangGraph is deprecated, only `orchestration/` needs to be rewritten
- Business logic can be tested without instantiating any graph
- State types are self-documenting

---

# ADR-004: Event-Driven Architecture with Redis Streams

**Date**: 2026-07-29  
**Status**: Accepted

## Context

Agents need to communicate asynchronously. Direct method calls create tight coupling. We need an event bus.

## Decision

Redis Streams is the primary event bus for Phase 1-10. The abstraction (`IEventBus` in `core/interfaces/event_bus.py`) allows migration to Kafka for Phase 14+ (high-throughput optimization) without changing any business code.

All domain events flow through the bus:
- Publishers: `platform/events/publishers.py`
- Subscribers: `platform/events/subscribers.py`
- Topics: `platform/events/topics.py` (EventTopic enum)

## Consequences

- Agents are decoupled — adding a new agent does not require modifying existing agents
- Events are persisted in Redis Streams and can be replayed for debugging
- Migration path to Kafka is clear (implement `KafkaEventBus(IEventBus)`)

---

# ADR-005: Feature Flags for Runtime Capability Control

**Date**: 2026-07-29  
**Status**: Accepted

## Context

Enterprise deployments need to disable specific features without redeployment:
- Disable Firecrawl if the API key is exhausted
- Disable a specific agent that is malfunctioning
- A/B test different LLM providers

## Decision

All optional capabilities are gated by `FeatureFlagManager`:
- Env-based flags in production (zero external dependency)
- Remote provider can be added (LaunchDarkly, Split.io) without changing flag checks
- Default values are defined in `feature_flags/manager.py`

## Consequences

- Zero-downtime feature toggling
- Gradual rollouts possible
- Clear separation between "can we?" (feature flags) and "should we?" (business rules)
