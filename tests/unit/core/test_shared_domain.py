from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from searchops.shared.domain.entity import BaseEntity
from searchops.shared.domain.value_object import BaseValueObject
from searchops.shared.domain.aggregate import AggregateRoot
from searchops.shared.domain.event import DomainEvent
from searchops.shared.domain.command import Command

class MyEntity(BaseEntity):
    name: str

class MyVO(BaseValueObject):
    name: str

class MyAggregate(AggregateRoot):
    name: str

class MyEvent(DomainEvent):
    event_name: str

class MyCommand(Command):
    cmd_name: str

@pytest.mark.unit
def test_base_entity():
    entity = MyEntity(id="1", name="Test")
    assert entity.id == "1"
    assert entity.name == "Test"

@pytest.mark.unit
def test_base_value_object():
    vo = MyVO(name="VO")
    assert vo.name == "VO"

@pytest.mark.unit
def test_aggregate_root():
    agg = MyAggregate(id="2", name="Agg")
    assert agg.id == "2"
    assert agg.name == "Agg"

@pytest.mark.unit
def test_domain_event_immutability():
    event = MyEvent(event_name="created")
    with pytest.raises(PydanticValidationError):
        event.event_name = "updated"

@pytest.mark.unit
def test_command():
    cmd = MyCommand(cmd_name="run")
    assert cmd.cmd_name == "run"
