"""
Unit tests for EntityCanonicalizer alias resolution.
"""

from __future__ import annotations

import pytest
from searchops.knowledge.canonicalizer import EntityCanonicalizer, slugify


@pytest.mark.unit
def test_slugify_basic():
    assert slugify("OpenAI Inc.") == "openai"
    assert slugify("Quantum Computing!") == "quantum_computing"
    assert slugify("  Google LLC  ") == "google"


@pytest.mark.unit
def test_canonicalizer_alias_resolution():
    canonicalizer = EntityCanonicalizer(similarity_threshold=0.85)
    id1 = canonicalizer.canonicalize("OpenAI", "Organization")
    id2 = canonicalizer.canonicalize("Open AI", "Organization")
    id3 = canonicalizer.canonicalize("OpenAI Inc.", "Organization")

    assert id1 == "organization:openai"
    assert id2 == "organization:openai"
    assert id3 == "organization:openai"


@pytest.mark.unit
def test_canonicalizer_distinct_entities():
    canonicalizer = EntityCanonicalizer()
    id1 = canonicalizer.canonicalize("OpenAI", "Organization")
    id2 = canonicalizer.canonicalize("Google", "Organization")

    assert id1 == "organization:openai"
    assert id2 == "organization:google"
