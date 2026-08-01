"""Agent subsystem configuration — tightened for free-tier usage."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Agent configuration — conservative limits to protect free-tier quotas."""

    # Token / cost guards: cut to 1/10th of previous defaults
    max_token_budget: int = 10_000    # was 100_000
    max_cost_usd: float = 0.10        # was 10.0  — stops runaway spend

    # Recursion / parallelism
    max_recursion_depth: int = 8      # was 25
    max_parallel_agents: int = 2      # was 10 — fewer concurrent LLM calls

    # Timeouts
    execution_timeout_seconds: int = 180   # was 600
    heartbeat_interval_seconds: int = 30
    human_approval_timeout_seconds: int = 3600

    # Checkpointing
    checkpointing_enabled: bool = True

    model_config = SettingsConfigDict(env_prefix="AGENT_", frozen=True)

    @field_validator("max_cost_usd")
    @classmethod
    def validate_max_cost_usd(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_cost_usd must be greater than 0")
        return v

    @field_validator("max_recursion_depth")
    @classmethod
    def validate_max_recursion_depth(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("max_recursion_depth must be between 1 and 100")
        return v

    @field_validator("max_parallel_agents")
    @classmethod
    def validate_max_parallel_agents(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("max_parallel_agents must be between 1 and 100")
        return v
