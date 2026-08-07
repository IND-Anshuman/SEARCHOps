#!/usr/bin/env python3
"""
Environment validation script.

Checks that all required environment variables are set before starting the platform.
Run this before starting the server to catch misconfiguration early.

Usage:
    uv run python scripts/check_env.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Auto-load .env file if present
env_path = Path(".env")
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.split("#")[0].strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass
class EnvCheck:
    """A single environment variable check."""

    name: str
    required: bool = True
    validator: Callable[[str], bool] | None = None
    description: str = ""


CHECKS: list[EnvCheck] = [
    EnvCheck("APP_ENV", description="Application environment (development|staging|production|testing)"),
    EnvCheck("APP_SECRET_KEY", description="JWT signing secret (min 32 chars)", validator=lambda v: len(v) >= 32 and "CHANGE_ME" not in v),
    EnvCheck("POSTGRES_PASSWORD", description="PostgreSQL password", validator=lambda v: "CHANGE_ME" not in v),
    EnvCheck("OPENAI_API_KEY", required=False, description="OpenAI API key"),
    EnvCheck("NVIDIA_API_KEY", required=False, description="NVIDIA NIM API key"),
    EnvCheck("ZHIPU_API_KEY", required=False, description="Zhipu GLM API key"),
    EnvCheck("ANTHROPIC_API_KEY", required=False, description="Anthropic API key"),
    EnvCheck("FIRECRAWL_API_KEY", required=False, description="Firecrawl API key"),
    EnvCheck("TAVILY_API_KEY", required=False, description="Tavily Search API key"),
    EnvCheck("NEO4J_PASSWORD", description="Neo4j password", validator=lambda v: "CHANGE_ME" not in v),
    EnvCheck("REDIS_PASSWORD", required=False, description="Redis password"),
    EnvCheck("QDRANT_API_KEY", required=False, description="Qdrant API key"),
    EnvCheck("LANGFUSE_PUBLIC_KEY", required=False, description="Langfuse public key"),
    EnvCheck("LANGFUSE_SECRET_KEY", required=False, description="Langfuse secret key"),
]

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def check_env() -> int:
    """Run all environment checks. Returns exit code (0=pass, 1=fail)."""
    print(f"\n{BOLD}SEARCHOps Environment Check{RESET}\n{'=' * 50}")

    failures = 0
    warnings = 0

    for check in CHECKS:
        value = os.environ.get(check.name)

        if value is None or "CHANGE_ME" in str(value):
            if check.required:
                print(f"  {RED}[FAIL]{RESET}   {check.name} — {check.description}")
                failures += 1
            else:
                print(f"  {YELLOW}[OPTIONAL]{RESET}  {check.name} — {check.description}")
                warnings += 1
            continue

        if check.validator and not check.validator(value):
            print(f"  {RED}[INVALID]{RESET}   {check.name} — {check.description}")
            failures += 1
            continue

        masked = value[:4] + "..." + value[-2:] if len(value) > 8 else "***"
        print(f"  {GREEN}[OK]{RESET}       {check.name} = {masked}")

    print(f"\n{'=' * 50}")
    print(f"Results: {GREEN}{len(CHECKS) - failures - warnings} passed{RESET}, "
          f"{YELLOW}{warnings} optional{RESET}, "
          f"{RED}{failures} failed{RESET}")

    if failures > 0:
        print(f"\n{RED}Environment check FAILED. Copy .env.example to .env and fill in required values.{RESET}\n")
        return 1

    print(f"\n{GREEN}Environment check PASSED.{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(check_env())
