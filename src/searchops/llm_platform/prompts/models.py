"""
Prompt Platform Domain Models: PromptDefinition, PromptVersion, PromptBudget.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class PromptVersion(BaseModel):
    """Semantic version identifier for prompts (e.g. planner:v1.0)."""
    name: str
    version: str = "v1.0"

    def __str__(self) -> str:
        return f"{self.name}:{self.version}"


class PromptDefinition(BaseModel):
    """Metadata definition for a versioned prompt in the PromptRegistry."""
    name: str
    version: str = "v1.0"
    owner: str = "platform-team"
    description: str = ""
    system_template: str = ""
    user_template: str = ""
    max_tokens: int = 3_000
    temperature: float = 0.0
    cacheable: bool = True
    expected_output_schema: type[BaseModel] | None = None
    cost_budget_usd: float = 0.01

    @property
    def key(self) -> str:
        return f"{self.name}:{self.version}"


class PromptBudget(BaseModel):
    """Granular token budget breakdown per compiled prompt invocation."""
    system_tokens: int = 0
    user_tokens: int = 0
    context_tokens: int = 0
    history_tokens: int = 0
    tool_tokens: int = 0
    completion_tokens: int = 1_024
    max_total_tokens: int = 4_096

    @property
    def total_input_tokens(self) -> int:
        return self.system_tokens + self.user_tokens + self.context_tokens + self.history_tokens + self.tool_tokens


class PromptCompileResult(BaseModel):
    """Result of compiling a PromptDefinition with variable arguments."""
    prompt_key: str
    system_prompt: str
    user_prompt: str
    sha256_hash: str
    budget: PromptBudget
