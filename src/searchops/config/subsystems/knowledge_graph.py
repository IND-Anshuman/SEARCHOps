"""Knowledge graph subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, SecretStr


class KnowledgeGraphSettings(BaseSettings):
    """Knowledge Graph configuration settings."""

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: SecretStr = Field(alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="searchops", alias="NEO4J_DATABASE")
    neo4j_max_connection_pool_size: int = 50
    embedding_dimension: int = 3072
    similarity_threshold: float = 0.85
    community_resolution: float = 1.0
    max_hops: int = 3
    confidence_threshold: float = 0.7
    deduplication_threshold: float = 0.95

    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)

    @field_validator("similarity_threshold", "confidence_threshold", "deduplication_threshold")
    @classmethod
    def validate_thresholds(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("Thresholds must be between 0.0 and 1.0")
        return v

    @field_validator("max_hops")
    @classmethod
    def validate_max_hops(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("max_hops must be between 1 and 10")
        return v
