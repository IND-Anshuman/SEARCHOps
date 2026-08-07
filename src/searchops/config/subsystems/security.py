"""Security subsystem configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, SecretStr


class SecuritySettings(BaseSettings):
    """Security configuration settings."""

    secret_key: SecretStr = Field(default=SecretStr("e8c9f7a6b5c4d3e2f1a09876543210fe8c9f7a6b5c4d3e2f1a09876543210fe8"), alias="APP_SECRET_KEY")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    bcrypt_rounds: int = 12
    rate_limit_requests_per_minute: int = 60
    rate_limit_burst: int = 10
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    csrf_enabled: bool = True
    prompt_injection_detection_enabled: bool = True
    output_validation_enabled: bool = True
    content_security_policy: str = "default-src 'self'"
    max_upload_size_bytes: int = 10_485_760
    oauth2_enabled: bool = False
    oauth2_provider: str | None = None
    oauth2_client_id: str | None = None
    oauth2_client_secret: SecretStr | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True, populate_by_name=True)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def validate_allowed_hosts(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("bcrypt_rounds")
    @classmethod
    def validate_bcrypt_rounds(cls, v: int) -> int:
        if not (10 <= v <= 14):
            raise ValueError("bcrypt_rounds must be between 10 and 14")
        return v
