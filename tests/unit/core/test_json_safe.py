"""Serialisation of job state written to Redis.

The production failure these guard against: ``orjson`` raises
``TypeError: Type is not JSON serializable: EvidenceGap`` when a Pydantic
model reaches RedisCache.set or EventBus.publish. The exception is swallowed
by the existing try/except, the write is skipped, and the job reports
COMPLETED while its state was never persisted — so the client polls and finds
no report. That is the bug behind "ran a query, got no report".
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

import orjson
import pytest

from searchops.orchestration.evidence import EvidenceGap
from searchops.shared.json_safe import json_safe


def test_pydantic_models_are_the_failure_case_orjson_cannot_handle():
    """Baseline: proves the bug was real, not hypothetical."""
    gap = EvidenceGap(description="no evidence yet", target_query="q", priority=1)

    with pytest.raises(TypeError):
        orjson.dumps([gap])


def test_json_safe_normalises_pydantic_models():
    gap = EvidenceGap(description="no evidence yet", target_query="q", priority=1)

    out = json_safe([gap])

    assert out == [{"description": "no evidence yet", "target_query": "q", "priority": 1}]
    assert orjson.dumps(out)  # must not raise


def test_json_safe_handles_the_exact_job_payload_shape():
    """The shape research_service writes: a dict containing model lists."""
    gap = EvidenceGap(description="g", target_query="q")
    payload = {
        "job_id": "abc",
        "status": "COMPLETED",
        "evidence_gaps": [gap],
        "evidence_claims": [],
        "entities": {},
    }

    raw = orjson.dumps(json_safe(payload))

    assert b"evidence_gaps" in raw
    assert b"g" in raw
    # Round-trips back to something the client can read.
    assert orjson.loads(raw)["evidence_gaps"][0]["description"] == "g"


def test_json_safe_handles_datetime_uuid_decimal_enum():
    class Level(Enum):
        LOW = "low"

    when = datetime(2026, 10, 13, 11, 1, tzinfo=UTC)
    uid = uuid4()
    payload = {"when": when, "id": uid, "amount": Decimal("0.0012"), "level": Level.LOW}

    out = orjson.loads(orjson.dumps(json_safe(payload)))

    assert out["when"] == "2026-10-13T11:01:00+00:00"
    assert out["id"] == str(uid)
    assert out["amount"] == "0.0012"
    assert out["level"] == "low"


def test_json_safe_is_recursive_and_preserves_primitives():
    value = {
        "nested": {"list": [1, "two", 3.0, None, True, {"deep": EvidenceGap(description="d")}]},
        "tuple": (1, 2),
    }

    out = orjson.loads(orjson.dumps(json_safe(value)))

    assert out["nested"]["list"][:5] == [1, "two", 3.0, None, True]
    assert out["nested"]["list"][5] == {"deep": {"description": "d", "target_query": "", "priority": 1}}
    assert out["tuple"] == [1, 2]


def test_json_safe_passes_unknown_objects_through_rather_than_coercing():
    """Unknown types must not be silently mangled — let orjson reject them."""

    class Opaque:
        pass

    assert isinstance(json_safe(Opaque()), Opaque)
