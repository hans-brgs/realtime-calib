"""Waits that survive cancellation (ADR-0050): nothing they wait for is cut short."""

from __future__ import annotations

import asyncio
import logging

import pytest

from calibration_service.concurrency import finish, run_steps, run_to_completion, settle


def test_run_to_completion_survives_two_cancellations() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        work: asyncio.Future[str] = loop.create_future()
        waiter = asyncio.create_task(run_to_completion(work))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)
        waiter.cancel()  # the second one is what "shield, then await" lost
        await asyncio.sleep(0.01)
        assert not waiter.done() and not work.cancelled()
        work.set_result("done")
        with pytest.raises(asyncio.CancelledError):
            await waiter  # re-raised once the work ended, never swallowed
        assert work.result() == "done"

    asyncio.run(scenario())


def test_finish_runs_a_coroutine_to_its_end_despite_cancellation() -> None:
    async def scenario() -> None:
        ended = asyncio.Event()

        async def slow() -> None:
            await asyncio.sleep(0.05)
            ended.set()

        waiter = asyncio.create_task(finish(slow()))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert ended.is_set()

    asyncio.run(scenario())


def test_settle_waits_for_every_task_and_reraises_the_cancellation() -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        tasks = [asyncio.create_task(release.wait()) for _ in range(3)]
        waiter = asyncio.create_task(settle(tasks))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0.01)
        assert not waiter.done()  # still waiting for the three tasks
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert all(task.done() and not task.cancelled() for task in tasks)

    asyncio.run(scenario())


def test_run_steps_neither_skips_a_step_nor_swallows_the_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        ran: list[str] = []
        gate = asyncio.Event()

        async def first() -> None:
            await gate.wait()
            ran.append("first")

        async def second() -> None:
            ran.append("second")
            raise RuntimeError("boom")

        async def third() -> None:
            ran.append("third")

        runner = asyncio.create_task(
            run_steps((first, second, third), logging.getLogger("test"), "closing")
        )
        await asyncio.sleep(0)
        runner.cancel()  # lands during the first step
        await asyncio.sleep(0.01)
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await runner
        assert ran == ["first", "second", "third"]

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert "closing: second failed" in caplog.text


def test_a_step_failing_after_a_cancellation_is_still_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()

        async def failing() -> None:
            await gate.wait()
            raise RuntimeError("late failure")

        runner = asyncio.create_task(run_steps((failing,), logging.getLogger("test"), "closing"))
        await asyncio.sleep(0)
        runner.cancel()
        await asyncio.sleep(0.01)
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await runner

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert "closing: failing failed" in caplog.text
    assert "late failure" in caplog.text


def test_a_step_raising_before_it_awaits_does_not_skip_the_next(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ran: list[str] = []

    def broken() -> asyncio.Future[None]:
        raise RuntimeError("raised synchronously")  # not even an awaitable

    async def after() -> None:
        ran.append("after")

    with caplog.at_level(logging.ERROR):
        asyncio.run(run_steps((broken, after), logging.getLogger("test"), "closing"))
    assert ran == ["after"]
    assert "closing: broken failed" in caplog.text
