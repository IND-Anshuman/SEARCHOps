"""API subsystem configuration."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # Production hides the interactive docs unless this is set (API_EXPOSE_DOCS=true).
    expose_docs: bool = False
    allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    # Browser calls authenticate with a Bearer token, not cookies, so
    # credentialed cross-origin requests are not needed.
    cors_allow_credentials: bool = False
    cors_allow_methods: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    cors_allow_headers: list[str] = ["*"]
    request_id_header: str = "X-Request-ID"
    correlation_id_header: str = "X-Correlation-ID"
    request_timeout_seconds: float = 60.0
    max_request_size_bytes: int = 10_485_760
    # Cost control: at most this many unfinished research runs per signed-in
    # user, and a hard ceiling on how long one run may take.
    max_concurrent_jobs_per_user: int = 2
    # Runs a signed-in user may start per UTC day (0 = unlimited). Counted in
    # Redis, so it holds across processes and restarts.
    max_jobs_per_user_per_day: int = 20
    job_max_runtime_seconds: int = 1800
    # A job with no progress for this long (server restart, crashed worker) is
    # reported as failed instead of "running" forever.
    job_stale_seconds: int = 2700

    model_config = SettingsConfigDict(env_prefix="API_", frozen=True)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def validate_allowed_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json

                try:
                    raw: Any = json.loads(v)
                    return [str(x) for x in raw] if isinstance(raw, list) else [str(raw)]
                except Exception:
                    pass
            return [str(x).strip() for x in v.split(",") if str(x).strip()]
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
