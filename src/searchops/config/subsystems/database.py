"""Database subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, SecretStr


class DatabaseSettings(BaseSettings):
    """Database configuration settings."""

    host: str = "localhost"
    port: int = 5432
    name: str = Field(default="searchops", alias="POSTGRES_DB")
    user: str = Field(default="searchops", alias="POSTGRES_USER")
    password: SecretStr = Field(alias="POSTGRES_PASSWORD")
    pool_size: int = 20
    max_overflow: int = 10
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    echo: bool = False
    echo_pool: bool = False
    ssl_mode: str = "prefer"

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", frozen=True, populate_by_name=True)

    @field_validator("pool_size")
    @classmethod
    def validate_pool_size(cls, v: int) -> int:
        if not (1 <= v <= 100):
            raise ValueError("pool_size must be between 1 and 100")
        return v

    @field_validator("max_overflow")
    @classmethod
    def validate_max_overflow(cls, v: int) -> int:
        if not (0 <= v <= 50):
            raise ValueError("max_overflow must be between 0 and 50")
        return v

    @field_validator("ssl_mode")
    @classmethod
    def validate_ssl_mode(cls, v: str) -> str:
        if v not in {"disable", "prefer", "require", "verify-full"}:
            raise ValueError("ssl_mode must be one of: disable, prefer, require, verify-full")
        return v

    @property
    def async_url(self) -> str:
        """Get the asynchronous database URL."""
        return f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_url(self) -> str:
        """Get the synchronous database URL."""
        return f"postgresql+psycopg://{self.user}:{self.password.get_secret_value()}@{self.host}:{self.port}/{self.name}"
