"""API subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class APISettings(BaseSettings):
    """API configuration settings."""

    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    reload: bool = False
    root_path: str = ""
    docs_url: str | None = "/docs"
    redoc_url: str | None = "/redoc"
    openapi_url: str | None = "/openapi.json"
    allowed_origins: list[str] = ["http://localhost:3000"]
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    cors_allow_headers: list[str] = ["*"]
    request_id_header: str = "X-Request-ID"
    correlation_id_header: str = "X-Correlation-ID"
    request_timeout_seconds: float = 60.0
    max_request_size_bytes: int = 10_485_760

    model_config = SettingsConfigDict(env_prefix="API_", frozen=True)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def validate_allowed_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("workers")
    @classmethod
    def validate_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError("workers must be at least 1")
        return v
