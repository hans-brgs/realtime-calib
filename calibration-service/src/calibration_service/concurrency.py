"""Waits that survive cancellation, for the invariants of a teardown (ADR-0050).

Cancelling an asyncio task that awaits another future normally cancels that
future too, and a second cancellation interrupts any "wait for it anyway"
written in an ``except CancelledError``. These helpers wait through ``asyncio.wait``,
which never cancels what it waits for: the awaited work always reaches its end,
and the caller's own cancellation is re-raised afterwards, never swallowed.

Two idioms they replace are forbidden in a teardown:

- ``with suppress(CancelledError): await task`` swallows the CALLER's cancellation
  along with the task's;
- ``if current_task().cancelling(): raise`` mid-teardown abandons every step
  after the first.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any


async def run_to_completion[T](future: asyncio.Future[T]) -> T:
    """Wait for ``future`` without ever cancelling it; re-raise a cancellation after."""
    cancelled: asyncio.CancelledError | None = None
    while not future.done():
        try:
            await asyncio.wait({future})  # never cancels the future
        except asyncio.CancelledError as exc:
            cancelled = exc
    if cancelled is not None:
        # The work's own failure rides along (retrieved, so never lost to an
        # "exception was never retrieved" at garbage collection).
        error = None if future.cancelled() else future.exception()
        raise cancelled from error
    return future.result()


async def finish[T](awaitable: Awaitable[T]) -> T:
    """Run ``awaitable`` to its end even if the caller is cancelled meanwhile."""
    return await run_to_completion(asyncio.ensure_future(awaitable))


async def settle(futures: Iterable[asyncio.Future[Any]]) -> None:
    """Wait until every future is done (results ignored); re-raise a cancellation after.

    The futures are neither cancelled nor inspected: a caller stopping tasks
    cancels them first, then settles them, then reads what each one ended with.
    """
    pending = {future for future in futures if not future.done()}
    cancelled: asyncio.CancelledError | None = None
    while pending:
        try:
            _, pending = await asyncio.wait(pending)
        except asyncio.CancelledError as exc:
            cancelled = exc
    if cancelled is not None:
        raise cancelled


async def run_steps(
    steps: Iterable[Callable[[], Awaitable[object]]], logger: logging.Logger, what: str
) -> None:
    """Run cleanup steps in order, each to its end; log failures; re-raise a cancellation last.

    A cancellation arriving during a step neither interrupts it nor skips the
    steps after it, and an exception in one step does not prevent the next.
    """
    cancelled: asyncio.CancelledError | None = None
    for step in steps:
        name = getattr(step, "__name__", "a step")
        future: asyncio.Future[object] | None = None
        try:
            future = asyncio.ensure_future(step())  # a step may also raise right here
            await run_to_completion(future)
        except asyncio.CancelledError as exc:
            cancelled = exc
            # Cancelled meanwhile: the step still ran to its end, and its own
            # failure is logged here like any other.
            error = None if future is None or future.cancelled() else future.exception()
            if error is not None:
                logger.error("%s: %s failed", what, name, exc_info=error)
        except Exception:
            logger.exception("%s: %s failed", what, name)
    if cancelled is not None:
        raise cancelled
