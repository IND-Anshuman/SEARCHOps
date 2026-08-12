"""
Centralized PromptRegistry managing versioned PromptDefinitions.
"""

from __future__ import annotations

import structlog

from searchops.llm_platform.prompts.models import PromptDefinition

log = structlog.get_logger(__name__)


class PromptRegistry:
    """Central Prompt Registry for registering, retrieving, and versioning prompts."""

    def __init__(self) -> None:
        self._prompts: dict[str, PromptDefinition] = {}

    def register(self, definition: PromptDefinition) -> None:
        """Register a new PromptDefinition."""
        self._prompts[definition.key] = definition
        # Also set default alias if unversioned name requested
        self._prompts[definition.name] = definition
        log.info("Registered prompt definition", key=definition.key)

    def get(self, name: str, version: str | None = None) -> PromptDefinition | None:
        """Retrieve a PromptDefinition by name and optional version tag."""
        key = f"{name}:{version}" if version else name
        return self._prompts.get(key)

    def list_prompts(self) -> list[str]:
        """List all registered prompt keys."""
        return sorted(list(self._prompts.keys()))


# Global singleton instance
_registry = PromptRegistry()


def get_prompt_registry() -> PromptRegistry:
    """Return global PromptRegistry singleton."""
    return _registry
