"""
Unit tests for configuration subsystem settings models.
"""

from __future__ import annotations

import pytest
from searchops.config.settings import get_settings


@pytest.mark.unit
def test_all_config_subsystems():
    settings = get_settings()

    assert settings.agent is not None
    assert settings.api is not None
    assert settings.cache is not None
    assert settings.database is not None
    assert settings.knowledge_graph is not None
    assert settings.llm is not None
    assert settings.memory is not None
    assert settings.observability is not None
    assert settings.scraping is not None
    assert settings.search is not None
    assert settings.security is not None
