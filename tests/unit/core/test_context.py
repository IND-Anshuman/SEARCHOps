from __future__ import annotations

import time
import pytest

from searchops.core.context.execution import BudgetTracker, ExecutionContext
from searchops.core.context.research import ResearchContext, ResearchDepth
from searchops.core.context.agent import AgentContext
from searchops.core.context.request import RequestContext
from searchops.typing.aliases import AgentId, CorrelationId, RequestId, TaskId, UserId, ExecutionId


@pytest.mark.unit
def test_execution_context_create():
    ctx1 = ExecutionContext.create()
    ctx2 = ExecutionContext.create()
    assert ctx1.execution_id != ctx2.execution_id
    assert ctx1.correlation_id != ctx2.correlation_id


@pytest.mark.unit
def test_execution_context_expiration():
    ctx = ExecutionContext.create(timeout_seconds=-1.0)
    assert ctx.is_expired is True


@pytest.mark.unit
def test_execution_context_remaining_seconds():
    ctx = ExecutionContext.create(timeout_seconds=60.0)
    assert ctx.remaining_seconds > 0.0
    assert ctx.is_expired is False


@pytest.mark.unit
def test_budget_tracker():
    tracker = BudgetTracker(max_tokens=1000, max_cost_usd=1.0)
    assert tracker.is_tokens_exceeded is False
    assert tracker.is_cost_exceeded is False
    assert tracker.is_exceeded is False
    assert tracker.remaining_tokens == 1000
    assert tracker.remaining_cost_usd == 1.0

    tracker.record_usage(tokens=500, cost_usd=0.5)
    assert tracker.consumed_tokens == 500
    assert tracker.consumed_cost_usd == 0.5
    assert tracker.remaining_tokens == 500
    assert tracker.remaining_cost_usd == 0.5
    assert tracker.is_exceeded is False

    tracker.record_usage(tokens=500, cost_usd=0.6)
    assert tracker.is_tokens_exceeded is True
    assert tracker.is_cost_exceeded is True
    assert tracker.is_exceeded is True
    assert tracker.remaining_tokens == 0
    assert tracker.remaining_cost_usd == 0.0


@pytest.mark.unit
def test_execution_context_log_dict():
    ctx = ExecutionContext.create()
    d = ctx.to_log_dict()
    assert "execution_id" in d
    assert "correlation_id" in d
    assert "consumed_tokens" in d
    assert "consumed_cost_usd" in d


@pytest.mark.unit
def test_research_context():
    exec_ctx = ExecutionContext.create()
    ctx = ResearchContext(
        execution_context=exec_ctx,
        research_id="res-123",
        query="quantum computing",
        depth=ResearchDepth.DEEP,
        domains_blocked={"malicious.com"},
    )
    assert ctx.is_visited("https://example.com") is False
    ctx.mark_visited("https://example.com")
    assert ctx.is_visited("https://example.com") is True
    assert ctx.is_domain_blocked("malicious.com") is True
    assert ctx.is_domain_blocked("example.com") is False


@pytest.mark.unit
def test_agent_context():
    exec_ctx = ExecutionContext.create()
    ctx = AgentContext(
        execution_context=exec_ctx,
        agent_id=AgentId("agent-1"),
        task_id=TaskId("task-1"),
        capability="search",
        recursion_depth=1,
        max_recursion_depth=5,
    )
    assert ctx.is_recursion_limit_reached is False
    next_ctx = ctx.increment_depth()
    assert next_ctx.recursion_depth == 2
    assert ctx.recursion_depth == 1  # immutable copy


@pytest.mark.unit
def test_request_context():
    ctx = RequestContext(
        request_id=RequestId("req-1"),
        correlation_id=CorrelationId("corr-1"),
        path="/api/v1/health",
        method="GET",
        client_ip="127.0.0.1",
    )
    d = ctx.to_log_dict()
    assert d["request_id"] == "req-1"
    assert d["correlation_id"] == "corr-1"
    assert d["path"] == "/api/v1/health"
