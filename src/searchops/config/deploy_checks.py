"""
Deployment readiness checks, run at start-up.

In ``production`` and ``staging`` a misconfiguration that would leave the API
open or leak secrets stops the process from starting, with a message naming the
setting to fix. In other environments the same findings are logged as warnings.

Nothing here talks to the network; it only inspects configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from searchops.middleware.auth import configured_key

if TYPE_CHECKING:
    from searchops.config.settings import Settings

log = structlog.get_logger(__name__)

_STRICT_ENVS = frozenset({"production", "staging"})
_DEV_PASSWORDS = frozenset({"searchops_dev_pass", "changeme", "password", "postgres", "neo4j"})


@dataclass
class DeployReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_deployment(settings: Settings) -> DeployReport:
    """Inspect *settings* and return what must and should be fixed."""
    report = DeployReport()
    sec = settings.security
    strict = settings.env in _STRICT_ENVS

    if sec.auth_disabled_explicitly:
        # A named opt-in (e.g. a public demo), so it warns rather than refusing to start.
        report.warnings.append("AUTH_DISABLED_EXPLICITLY is true: the API is unauthenticated.")
    elif not sec.clerk_enabled and not configured_key(sec):
        report.errors.append(
            "No authentication is configured. Set CLERK_PUBLISHABLE_KEY (people) and/or "
            "SEARCHOPS_API_KEY (services); the built-in default secret is public and is refused."
        )

    origins = settings.api.allowed_origins
    if "*" in origins:
        report.errors.append("API_ALLOWED_ORIGINS contains '*': list the exact origins of the web app.")
    if strict and any(o.startswith("http://localhost") or o.startswith("http://127.") for o in origins):
        report.warnings.append("API_ALLOWED_ORIGINS still lists localhost; remove it for a public deployment.")

    if sec.clerk_enabled and (sec.clerk_publishable_key or "").startswith("pk_test_") and settings.env == "production":
        report.warnings.append(
            "CLERK_PUBLISHABLE_KEY is a development (pk_test_) key; create a Clerk production instance for production."
        )

    hosts = {h for h in sec.allowed_hosts if h not in ("localhost", "127.0.0.1")}
    if strict and not hosts:
        report.warnings.append(
            "ALLOWED_HOSTS lists only localhost, so requests for your public hostname will be rejected. "
            "Set ALLOWED_HOSTS to the host name(s) users reach the API on."
        )

    if settings.env == "production" and settings.api.expose_docs and settings.api.docs_url:
        report.warnings.append("API docs are exposed in production (API_EXPOSE_DOCS=true).")

    for name, secret in (
        ("POSTGRES_PASSWORD", settings.database.password),
        ("NEO4J_PASSWORD", settings.knowledge_graph.neo4j_password),
    ):
        value = secret.get_secret_value() if secret is not None else ""
        if strict and value in _DEV_PASSWORDS:
            report.warnings.append(f"{name} is a well-known development password.")

    return report


def enforce_deployment(settings: Settings) -> DeployReport:
    """Raise in strict environments on any error; log everything else."""
    report = check_deployment(settings)
    for w in report.warnings:
        log.warning("deploy.check.warning", detail=w, env=settings.env)
    if report.errors:
        message = "Refusing to start: " + " | ".join(report.errors)
        if settings.env in _STRICT_ENVS:
            raise RuntimeError(message)
        for e in report.errors:
            log.warning("deploy.check.error_ignored_outside_production", detail=e, env=settings.env)
    return report
