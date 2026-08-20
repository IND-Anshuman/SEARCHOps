"""
Unit tests for domain, exception handlers, and shared domain models to ensure >80% coverage.
"""

from __future__ import annotations

import pytest
from searchops.shared.domain.aggregate import AggregateRoot
from searchops.shared.domain.entity import BaseEntity
from searchops.shared.domain.value_object import BaseValueObject


class DummyEntity(BaseEntity):
    pass


class DummyAggregate(AggregateRoot):
    pass


class DummyValueObject(BaseValueObject):
    val: str


@pytest.mark.unit
def test_shared_domain_abstractions():
    e1 = DummyEntity(id="e1")
    e2 = DummyEntity(id="e1")
    assert e1 == e2

    agg = DummyAggregate(id="agg1")
    assert agg.id == "agg1"

    vo1 = DummyValueObject(val="a")
    vo2 = DummyValueObject(val="a")
    assert vo1 == vo2
