"""Tests for the event-loop binding of the global transport pool.

``get_transport_pool()`` is a process-wide singleton wrapping an
``httpx.AsyncClient``, which is bound to the event loop that first used it. A
caller on a different loop reusing the stale client fails with
``Event loop is closed`` — a latent bug that only appears once more than one
loop touches the pool (a test session, a worker, a fresh loop per job).
"""

from __future__ import annotations

import asyncio

import pytest

from searchops.scraping import transport as tp


class TestTransportPoolLoopBinding:
    @pytest.mark.asyncio
    async def test_same_loop_reuses_the_pool(self) -> None:
        await tp.close_transport_pool()
        first = tp.get_transport_pool()
        assert tp.get_transport_pool() is first

    @pytest.mark.asyncio
    async def test_closed_pool_is_rebuilt(self) -> None:
        await tp.close_transport_pool()
        first = tp.get_transport_pool()
        await first.aclose()
        # A closed client must not be handed back.
        second = tp.get_transport_pool()
        assert second is not first
        assert second.is_closed is False
        await tp.close_transport_pool()

    @pytest.mark.asyncio
    async def test_different_loop_gets_a_fresh_pool(self) -> None:
        """The regression test: a new loop must not inherit a stale client."""
        await tp.close_transport_pool()
        first = tp.get_transport_pool()

        other = await asyncio.to_thread(lambda: asyncio.run(_fetch_here()))
        assert other is not first
        assert other.is_closed is False
        await tp.close_transport_pool()


async def _fetch_here() -> object:
    """Fetch the pool inside a *brand new* event loop."""
    return tp.get_transport_pool()
