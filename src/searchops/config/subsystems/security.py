"""Security subsystem configuration."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Published in this source file, so it is known to everyone: the auth
# middleware refuses to accept it as a credential.
INSECURE_DEFAULT_SECRET_KEY = "e8c9f7a6b5c4d3e2f1a09876543210fe8c9f7a6b5c4d3e2f1a09876543210fe8"  # noqa: S105


class SecuritySettings(BaseSettings):
    """Security configuration settings."""

    secret_key: SecretStr = Field(
        default=SecretStr(INSECURE_DEFAULT_SECRET_KEY),
        alias="APP_SECRET_KEY",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    bcrypt_rounds: int = 12
    rate_limit_requests_per_minute: int = 60
    rate_limit_burst: int = 10
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1"]
    csrf_enabled: bool = True
    prompt_injection_detection_enabled: bool = True
    output_validation_enabled: bool = True
    # Empty: the API builds a policy for this app and its Clerk instance
    # (middleware/security_headers.py). Set CONTENT_SECURITY_POLICY to override
    # it, e.g. to add an analytics host.
    content_security_policy: str = ""
    max_upload_size_bytes: int = 10_485_760
    oauth2_enabled: bool = False
    oauth2_provider: str | None = None
    oauth2_client_id: str | None = None
    oauth2_client_secret: SecretStr | None = None
    # Dedicated API key for X-API-Key authentication. Distinct from
    # secret_key (which also signs JWTs) so an API credential can be rotated
    # without invalidating every issued token. When set, it takes precedence
    # over secret_key for request auth.
    api_key: SecretStr | None = Field(default=None, alias="SEARCHOPS_API_KEY")
    # Clerk user sign-in for the browser workbench. The publishable key is
    # public by design (it also goes in the frontend as
    # VITE_CLERK_PUBLISHABLE_KEY); the backend derives the token issuer and
    # JWKS URL from it, so Clerk's secret key is not needed here.
    clerk_publishable_key: str | None = Field(default=None, alias="CLERK_PUBLISHABLE_KEY")
    clerk_jwks_url: str | None = Field(default=None, alias="CLERK_JWKS_URL")
    # Origins a session token may have been minted for (the JWT ``azp``
    # claim). Empty means: the API's allowed CORS origins.
    clerk_authorized_parties: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="CLERK_AUTHORIZED_PARTIES"
    )
    # Auth must never be inferred off from the environment. Disabling it
    # requires this explicit, named opt-in so an accidental APP_ENV=development
    # cannot silently ship an unauthenticated deployment.
    auth_disabled_explicitly: bool = Field(default=False)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True, populate_by_name=True)

    @field_validator("allowed_hosts", "clerk_authorized_parties", mode="before")
    @classmethod
    def validate_allowed_hosts(cls, v: Any) -> list[str]:
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

    @property
    def clerk_enabled(self) -> bool:
        return bool((self.clerk_publishable_key or "").strip())

    @field_validator("bcrypt_rounds")
    @classmethod
    def validate_bcrypt_rounds(cls, v: int) -> int:
        if not (10 <= v <= 14):
            raise ValueError("bcrypt_rounds must be between 10 and 14")
        return v
