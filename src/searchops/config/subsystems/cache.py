"""Cache subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, SecretStr


class CacheSettings(BaseSettings):
    """Cache configuration settings."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: SecretStr | None = None
    max_connections: int = 100
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
    retry_on_timeout: bool = True
    health_check_interval: int = 30
    encoding: str = "utf-8"
    decode_responses: bool = True
    ssl: bool = False
    ssl_cert_reqs: str | None = None

    model_config = SettingsConfigDict(env_prefix="REDIS_", frozen=True)

    @field_validator("db")
    @classmethod
    def validate_db(cls, v: int) -> int:
        if not (0 <= v <= 15):
            raise ValueError("db must be between 0 and 15")
        return v

    @field_validator("max_connections")
    @classmethod
    def validate_max_connections(cls, v: int) -> int:
        if not (1 <= v <= 1000):
            raise ValueError("max_connections must be between 1 and 1000")
        return v

    @property
    def url(self) -> str:
        """Get the Redis URL without password."""
        scheme = "rediss" if self.ssl else "redis"
        return f"{scheme}://{self.host}:{self.port}/{self.db}"

    @property
    def url_with_password(self) -> str:
        """Get the Redis URL with password if available."""
        scheme = "rediss" if self.ssl else "redis"
        if self.password:
            return f"{scheme}://:{self.password.get_secret_value()}@{self.host}:{self.port}/{self.db}"
        return f"{scheme}://{self.host}:{self.port}/{self.db}"
