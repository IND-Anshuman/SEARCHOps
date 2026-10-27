"""
Unit tests for NetworkInterceptor: the page network-traffic capture helper
used by the Playwright scrape tier.

Covers request/response recording, GraphQL detection, and the JSON-body
capture path. The response handler is async in Playwright, so the handlers are
registered as coroutine functions and driven with fake response objects.
"""

from __future__ import annotations

import asyncio

import pytest

from searchops.scraping.pipeline import NetworkInterceptor


class FakeHeaders(dict):
    """dict subclass so `.get()` behaves like Playwright's header mapping."""


def _req(url="https://x.test/api", method="GET", post_data=None, headers=None):
    return type(
        "R",
        (),
        {
            "url": url,
            "method": method,
            "post_data": post_data,
            "headers": headers or FakeHeaders(),
        },
    )()


def _resp(url="https://x.test/api", status=200, headers=None, body=None):
    """Fake Playwright Response.

    `.json()` and `.text()` are METHODS on a real Response, so the fake
    provides callables that return coroutines.
    """

    async def json_body():
        if body is None:
            raise ValueError("no body")
        return body

    async def text_body():
        return "" if body is None else str(body)

    return type(
        "P",
        (),
        {
            "url": url,
            "status": status,
            "headers": headers or FakeHeaders(),
            # staticmethod so the fake takes no implicit self, matching the
            # real Playwright Response.json() / .text() signatures.
            "json": staticmethod(json_body),
            "text": staticmethod(text_body),
        },
    )()


# ── Request capture ───────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_records_every_request():
    i = NetworkInterceptor()
    await i._on_request(_req(url="https://a.test/1"))
    await i._on_request(_req(url="https://a.test/2", method="POST"))

    assert len(i.requests) == 2
    assert i.requests[0]["url"] == "https://a.test/1"
    assert i.requests[1]["method"] == "POST"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_records_timestamp():
    i = NetworkInterceptor()
    await i._on_request(_req())

    assert i.requests[0]["timestamp"] > 0


# ── GraphQL detection ─────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_detects_graphql_query_from_dict_post_data():
    i = NetworkInterceptor()
    await i._on_request(
        _req(
            post_data={"query": "query { user { id } }", "variables": {"a": 1}},
            headers=FakeHeaders({"content-type": "application/json"}),
        )
    )

    assert len(i.graphql_responses) == 1
    entry = i.graphql_responses[0]
    assert entry["type"] == "graphql_query"
    assert "user" in entry["query"]
    assert entry["variables"] == {"a": 1}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ignores_json_post_without_query_key():
    i = NetworkInterceptor()
    await i._on_request(_req(post_data={"name": "x"}, headers=FakeHeaders({"content-type": "application/json"})))

    assert i.graphql_responses == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_json_content_type_is_not_scanned_for_graphql():
    i = NetworkInterceptor()
    await i._on_request(_req(post_data={"query": "query { x }"}, headers=FakeHeaders({"content-type": "text/plain"})))

    assert i.graphql_responses == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_string_post_data_does_not_crash():
    """A raw JSON string body must not raise or be mis-detected."""
    i = NetworkInterceptor()
    await i._on_request(
        _req(post_data='{"query": "query { x }"}', headers=FakeHeaders({"content-type": "application/json"}))
    )

    assert i.graphql_responses == [], "String bodies are not parsed as GraphQL"
    assert len(i.requests) == 1


# ── Response capture ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_records_every_response():
    i = NetworkInterceptor()
    await i._on_response(_resp(url="https://a.test/1", status=200))
    await i._on_response(_resp(url="https://a.test/2", status=404))

    assert len(i.responses) == 2
    assert i.responses[0]["status"] == 200
    assert i.responses[1]["status"] == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_captures_json_response_body():
    """A 200 JSON response must have its body captured.

    Regression: _on_response detected application/json and then did nothing
    (`pass`), so the structured-data path always returned empty.
    """
    i = NetworkInterceptor()
    await i._on_response(
        _resp(
            url="https://a.test/api",
            status=200,
            headers=FakeHeaders({"content-type": "application/json"}),
            body={"items": [1, 2, 3]},
        )
    )

    assert len(i.json_responses) == 1
    captured = i.json_responses[0]
    assert captured["url"] == "https://a.test/api"
    assert captured["status"] == 200
    assert captured["body"] == {"items": [1, 2, 3]}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_json_response_is_not_body_captured():
    i = NetworkInterceptor()
    await i._on_response(_resp(status=200, headers=FakeHeaders({"content-type": "text/html"}), body="<html/>"))

    assert len(i.responses) == 1
    assert i.json_responses == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_200_json_response_is_not_captured():
    i = NetworkInterceptor()
    await i._on_response(_resp(status=500, headers=FakeHeaders({"content-type": "application/json"}), body={"e": 1}))

    assert i.json_responses == [], "Only successful responses are captured"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unparseable_json_body_does_not_crash():
    i = NetworkInterceptor()
    await i._on_response(_resp(status=200, headers=FakeHeaders({"content-type": "application/json"}), body=None))

    assert len(i.responses) == 1
    assert i.json_responses == [], "A body that fails to parse is skipped, not fatal"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_json_body_capture_is_bounded():
    """A page with many JSON endpoints must not grow the capture unbounded."""
    i = NetworkInterceptor()

    for n in range(50):
        await i._on_response(
            _resp(
                url=f"https://a.test/api/{n}",
                status=200,
                headers=FakeHeaders({"content-type": "application/json"}),
                body={"n": n},
            )
        )

    assert len(i.json_responses) <= 20, "Capture is capped so a chatty page cannot balloon memory"


# ── get_structured_data ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_data_reports_counts_and_json_bodies():
    i = NetworkInterceptor()
    await i._on_request(_req(url="https://a.test/1"))
    await i._on_request(
        _req(post_data={"query": "query { a }"}, headers=FakeHeaders({"content-type": "application/json"}))
    )
    await i._on_response(
        _resp(status=200, headers=FakeHeaders({"content-type": "application/json"}), body={"ok": True})
    )

    data = i.get_structured_data()

    assert data["total_requests"] == 2
    assert len(data["graphql_queries"]) == 1
    assert len(data["json_responses"]) == 1
    assert data["json_responses"][0]["body"] == {"ok": True}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_data_on_empty_interceptor():
    data = NetworkInterceptor().get_structured_data()

    assert data["total_requests"] == 0
    assert data["graphql_queries"] == []
    assert data["json_responses"] == []


# ── Handler registration ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_setup_handlers_registers_both_events():
    i = NetworkInterceptor()
    registered: dict[str, object] = {}

    class FakePage:
        def on(self, event, handler):
            registered[event] = handler

    i.setup_handlers(FakePage())

    assert set(registered) == {"request", "response"}
    # Each listener schedules a coroutine, so it must return a Future/Task
    # rather than an un-awaited coroutine (which Playwright would drop).
    handler = registered["response"]
    assert callable(handler)

    async def drive():
        class FH(dict):
            pass

        async def jb():
            return {"ok": True}

        resp = type(
            "P",
            (),
            {
                "url": "u",
                "status": 200,
                "headers": FH({"content-type": "application/json"}),
                "json": staticmethod(jb),
            },
        )()
        handler(resp)  # type: ignore[operator]
        await asyncio.sleep(0)  # let the scheduled task run

    asyncio.run(drive())
    assert len(i.json_responses) == 1, "The registered listener must actually capture the body"
