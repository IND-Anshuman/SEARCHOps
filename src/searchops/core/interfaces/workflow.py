"""
Workflow Runtime Interface: Decouples Application Services from Orchestration Backends (LangGraph, Temporal, Ray).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IWorkflowRuntime(ABC):
    """Abstract Workflow Runtime Port enabling runtime substitution without touching domain code."""

    @abstractmethod
    async def execute_workflow(
        self,
        workflow_id: str,
        initial_state: dict[str, Any],
        tenant_id: str = "default_tenant",
    ) -> dict[str, Any]:
        """Execute a research workflow with explicit state and tenant context."""
        ...

    @abstractmethod
    async def pause_workflow(self, workflow_id: str) -> bool:
        """Pause a running workflow execution."""
        ...

    @abstractmethod
    async def resume_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Resume a paused workflow execution."""
        ...
