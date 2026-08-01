"""Master settings class."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

from searchops.config.subsystems.api import APISettings
from searchops.config.subsystems.database import DatabaseSettings
from searchops.config.subsystems.cache import CacheSettings
from searchops.config.subsystems.llm import LLMSettings
from searchops.config.subsystems.observability import ObservabilitySettings
from searchops.config.subsystems.security import SecuritySettings
from searchops.config.subsystems.scraping import ScrapingSettings
from searchops.config.subsystems.agent import AgentSettings
from searchops.config.subsystems.knowledge_graph import KnowledgeGraphSettings
from searchops.config.subsystems.search import SearchSettings
from searchops.config.subsystems.memory import MemorySettings


class Settings(BaseSettings):
    """Master application configuration."""

    env: Literal["development", "staging", "production", "testing"] = Field(default="development", alias="APP_ENV")
    app_name: str = "searchops"
    app_version: str = "0.1.0"
    debug: bool = Field(default=False, alias="APP_DEBUG")
    log_level: str = "INFO"
    log_format: str = "json"

    api: APISettings = Field(default_factory=APISettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    scraping: ScrapingSettings = Field(default_factory=ScrapingSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    knowledge_graph: KnowledgeGraphSettings = Field(default_factory=KnowledgeGraphSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        populate_by_name=True,
        extra="ignore",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be in [DEBUG, INFO, WARNING, ERROR, CRITICAL]")
        return v

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        if v not in {"json", "console"}:
            raise ValueError("log_format must be in [json, console]")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get the cached settings instance."""
    return Settings()
