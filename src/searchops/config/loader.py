"""
YAML configuration loader with environment-specific layering.

Loading order (later sources override earlier):
  1. config/base.yaml
  2. config/{environment}.yaml
  3. Environment variables (handled by Pydantic Settings)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
import structlog

log = structlog.get_logger(__name__)

_CONFIG_ROOT = Path(__file__).parent.parent.parent.parent / "config"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two dicts. override values win. Returns a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_yaml_config(environment: str | None = None) -> dict[str, Any]:
    """Load and merge YAML config files for the given environment.
    
    Args:
        environment: The environment name. Falls back to APP_ENV env var, then 'development'.
    
    Returns:
        Merged configuration dictionary.
    
    Raises:
        FileNotFoundError: If base.yaml does not exist.
    """
    env = environment or os.environ.get("APP_ENV", "development")
    
    base_path = _CONFIG_ROOT / "base.yaml"
    if not base_path.exists():
        raise FileNotFoundError(f"Base config not found: {base_path}")
    
    with base_path.open() as f:
        config = yaml.safe_load(f) or {}
    
    env_path = _CONFIG_ROOT / f"{env}.yaml"
    if env_path.exists():
        with env_path.open() as f:
            env_config = yaml.safe_load(f) or {}
        config = _deep_merge(config, env_config)
        log.debug("Loaded environment config", env=env, path=str(env_path))
    else:
        log.warning("No environment config found", env=env, path=str(env_path))
    
    return config
