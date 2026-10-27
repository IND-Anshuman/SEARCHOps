"""Observability subsystem configuration."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ObservabilitySettings(BaseSettings):
    """Observability configuration settings."""

    # Off unless asked for: with no collector at OTEL_EXPORTER_OTLP_ENDPOINT the
    # exporter retries and logs connection errors for the life of the process.
    # Set OTEL_ENABLED=true once a collector (or a hosted OTLP endpoint) exists.
    enabled: bool = Field(default=False, alias="OTEL_ENABLED")
    service_name: str = "searchops"
    service_version: str = "0.1.0"
    otlp_endpoint: str = Field(default="http://localhost:4317", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otlp_protocol: str = "grpc"
    traces_enabled: bool = Field(default=True, alias="OTEL_TRACES_ENABLED")
    metrics_enabled: bool = True
    logs_enabled: bool = True
    sample_rate: float = 1.0
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = True
    langfuse_debug: bool = False
    prometheus_enabled: bool = True
    propagation: str = "tracecontext,baggage"

    model_config = SettingsConfigDict(frozen=True, populate_by_name=True)

    @field_validator("otlp_protocol")
    @classmethod
    def validate_otlp_protocol(cls, v: str) -> str:
        if v not in {"grpc", "http/protobuf"}:
            raise ValueError("otlp_protocol must be 'grpc' or 'http/protobuf'")
        return v

    @field_validator("sample_rate")
    @classmethod
    def validate_sample_rate(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("sample_rate must be between 0.0 and 1.0")
        return v
