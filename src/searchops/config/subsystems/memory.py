"""Memory subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class MemorySettings(BaseSettings):
    """Memory configuration settings."""

    execution_backend: str = "redis"
    execution_default_ttl: int = 86400
    execution_max_size_bytes: int = 104_857_600
    workflow_backend: str = "postgres"
    workflow_retention_days: int = 90
    vector_backend: str = "qdrant"
    vector_collection_name: str = "searchops_vectors"
    vector_distance_metric: str = "cosine"
    qdrant_host: str = Field(default="localhost", alias="QDRANT_HOST")
    qdrant_port: int = Field(default=6333, alias="QDRANT_PORT")
    qdrant_grpc_port: int = Field(default=6334, alias="QDRANT_GRPC_PORT")
    qdrant_api_key: SecretStr | None = Field(default=None, alias="QDRANT_API_KEY")
    qdrant_prefer_grpc: bool = True
    qdrant_timeout: int = 30

    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)
