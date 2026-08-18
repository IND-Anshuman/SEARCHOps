"""
Pytest configuration and shared fixtures for the SEARCHOps test suite.

All test infrastructure is defined here. Tests should import fixtures
from conftest.py rather than importing from application internals directly.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from searchops.config.settings import Settings
from searchops.core.context.execution import ExecutionContext
from searchops.core.context.request import RequestContext
from searchops.feature_flags.manager import FeatureFlagManager
from searchops.feature_flags.providers import InMemoryFeatureFlagProvider
from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.platform.registry.tool_registry import ToolRegistry
from searchops.typing.aliases import CorrelationId, RequestId


import os

# Set environment variables immediately at import time for test collection
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32-chars-minimum!")
os.environ.setdefault("POSTGRES_PASSWORD", "test-password")
os.environ.setdefault("REDIS_PASSWORD", "test-redis-password")
os.environ.setdefault("NEO4J_PASSWORD", "test-neo4j-password")
os.environ.setdefault("NEO4J_URI", "mock")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key")
# Disable OTel SDK entirely in tests — avoids background thread I/O errors
# during process teardown when the OTLP exporter tries to flush to a
# localhost:4317 collector that doesn't exist in CI / local dev.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")
os.environ.setdefault("OTEL_LOGS_EXPORTER", "none")


# ── Environment setup ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def set_test_env(monkeypatch_session: pytest.MonkeyPatch) -> None:
    """Set environment variables for testing before any test runs."""
    monkeypatch_session.setenv("APP_ENV", "testing")
    monkeypatch_session.setenv("APP_SECRET_KEY", "test-secret-key-32-chars-minimum!")
    monkeypatch_session.setenv("POSTGRES_PASSWORD", "test-password")
    monkeypatch_session.setenv("REDIS_PASSWORD", "test-redis-password")
    monkeypatch_session.setenv("NEO4J_PASSWORD", "test-neo4j-password")
    monkeypatch_session.setenv("NEO4J_URI", "mock")
    monkeypatch_session.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch_session.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")


@pytest.fixture(scope="session")
def monkeypatch_session() -> Generator[pytest.MonkeyPatch, None, None]:
    """Session-scoped monkeypatch fixture."""
    with pytest.MonkeyPatch.context() as mp:
        yield mp


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.fixture
def test_settings() -> Settings:
    """Return a test Settings instance with safe defaults."""
    return Settings(
        env="testing",
        log_level="WARNING",
        log_format="console",
    )


# ── Context fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def execution_context() -> ExecutionContext:
    """Return a fresh ExecutionContext for testing."""
    return ExecutionContext.create(
        correlation_id=CorrelationId("test-correlation-id"),
        max_tokens=10_000,
        max_cost_usd=1.0,
        timeout_seconds=30.0,
    )


@pytest.fixture
def request_context() -> RequestContext:
    """Return a test RequestContext."""
    return RequestContext(
        request_id=RequestId("test-request-id"),
        correlation_id=CorrelationId("test-correlation-id"),
        path="/test",
        method="GET",
        client_ip="127.0.0.1",
    )


# ── Feature flags ─────────────────────────────────────────────────────────────

@pytest.fixture
def flag_provider() -> InMemoryFeatureFlagProvider:
    """Return a mutable in-memory feature flag provider."""
    return InMemoryFeatureFlagProvider({
        "firecrawl_enabled": True,
        "playwright_enabled": True,
        "knowledge_graph_enabled": True,
    })


@pytest.fixture
def feature_flags(flag_provider: InMemoryFeatureFlagProvider) -> FeatureFlagManager:
    """Return a FeatureFlagManager backed by in-memory provider."""
    return FeatureFlagManager(providers=[flag_provider])


# ── Platform registries ───────────────────────────────────────────────────────

@pytest.fixture
def agent_registry() -> AgentRegistry:
    """Return a fresh AgentRegistry."""
    return AgentRegistry()


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """Return a fresh ToolRegistry."""
    return ToolRegistry()


@pytest.fixture
def capability_registry() -> CapabilityRegistry:
    """Return a fresh CapabilityRegistry."""
    return CapabilityRegistry()
