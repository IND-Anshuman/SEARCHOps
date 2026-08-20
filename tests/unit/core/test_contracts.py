from __future__ import annotations

import pytest

from searchops.shared.contracts.pagination import PaginationParams, PaginatedResponse
from searchops.shared.contracts.health import HealthResponse, ComponentHealth, HealthStatusValue
from searchops.shared.contracts.errors import ErrorResponse, ErrorDetail, FieldError
from searchops.shared.contracts.base import BaseRequest, BaseResponse


@pytest.mark.unit
def test_pagination_params():
    p1 = PaginationParams(page=1, size=20)
    assert p1.offset == 0
    assert p1.limit == 20

    p2 = PaginationParams(page=3, size=10)
    assert p2.offset == 20
    assert p2.limit == 10


@pytest.mark.unit
def test_paginated_response():
    params = PaginationParams(page=1, size=10)
    resp = PaginatedResponse.create(
        items=["a", "b"],
        total=25,
        params=params,
    )
    assert resp.items == ["a", "b"]
    assert resp.total == 25
    assert resp.page == 1
    assert resp.size == 10
    assert resp.pages == 3
    assert resp.has_next is True
    assert resp.has_previous is False


@pytest.mark.unit
def test_health_response():
    resp = HealthResponse.healthy(
        service="searchops",
        version="0.1.0",
        components=[],
        uptime=12.5,
    )
    assert resp.status == HealthStatusValue.HEALTHY
    assert resp.service == "searchops"
    assert resp.uptime_seconds == 12.5


@pytest.mark.unit
def test_error_response():
    detail = ErrorDetail(
        type="https://searchops.io/errors/validation_error",
        title="Validation Error",
        status=422,
        detail="Invalid request body",
        instance="/api/v1/resource",
        error_code="VALIDATION_ERROR",
        field_errors=[FieldError(field="user.email", message="Invalid email", code="value_error")],
    )
    resp = ErrorResponse(error=detail)
    assert resp.error.title == "Validation Error"
    assert resp.error.field_errors[0].field == "user.email"


@pytest.mark.unit
def test_base_request_and_response():
    req = BaseRequest()
    assert req.request_id is not None

    res = BaseResponse(request_id=req.request_id)
    assert res.request_id == req.request_id
    assert res.timestamp is not None
