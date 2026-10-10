"""One open camera and the only thread allowed to touch it (ADR-0050).

A V4L2 handle released while another thread is inside ``grab()`` wedges the
device node until the service restarts (seen on the rig). Every call to a
camera therefore runs on its own single-thread executor: the queue is FIFO on
one thread, so a ``release`` posted after a ``grab`` runs after it, whatever
asyncio cancellation did to the coroutine waiting for that grab.

``grab``/``retrieve``/``read`` stay cancellable on the asyncio side (the thread
finishes the call regardless); ``open`` and ``release`` run to completion
(``run_to_completion``), so a cancelled open still leaves a camera to release,
and a second cancellation never skips a release still in the queue.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from calibration_service.capture.camera import CameraCapture, Clock
from calibration_service.concurrency import run_to_completion
from calibration_service.models.frame import Frame


class CameraDevice:
    """A camera owned by one thread: open, read, grab, retrieve, release (idempotent)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"camera-{name}")
        # Written ON the device thread (by _open_on_thread), so an open whose
        # coroutine was cancelled still leaves the handle for release to close.
        self._capture: CameraCapture | None = None
        self._release: asyncio.Future[None] | None = None

    async def open(self, opener: Callable[[], CameraCapture]) -> None:
        """Run ``opener`` (e.g. ``open_camera``) on the device thread, to its end."""
        opened = self._thread.submit(self._open_on_thread, opener)
        await run_to_completion(asyncio.wrap_future(opened))

    def _open_on_thread(self, opener: Callable[[], CameraCapture]) -> None:
        self._capture = opener()

    async def read(self) -> Frame | None:
        """Grab and decode one frame (the first-frame wait)."""
        return await self._call(self._handle().read)

    async def choose_clock(self) -> Clock:
        """Pick the timestamp base once, before the loop (ADR-0049)."""
        return await self._call(self._handle().choose_clock)

    @property
    def clock(self) -> Clock:
        """The timestamp base chosen at open ("host" until then)."""
        return self._capture.clock if self._capture is not None else "host"

    async def grab(self) -> float | None:
        """Buffer the next frame without decoding it; its timestamp, or None."""
        return await self._call(self._handle().grab)

    async def retrieve(self) -> Frame | None:
        """Decode the last grabbed frame."""
        return await self._call(self._handle().retrieve)

    async def release(self) -> None:
        """Close the camera after any call still on its thread, then stop the thread.

        Idempotent: every caller waits for the same release, which runs once.
        """
        if self._release is None:
            self._release = asyncio.wrap_future(self._thread.submit(self._release_on_thread))
            # The thread is idle once its last item ran: shutting it down without
            # waiting never blocks the loop. Never cancel_futures: the release
            # itself is in that queue.
            self._release.add_done_callback(lambda _: self._thread.shutdown(wait=False))
        await run_to_completion(self._release)

    @property
    def released(self) -> bool:
        return self._release is not None and self._release.done()

    def _release_on_thread(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()

    def _handle(self) -> CameraCapture:
        if self._capture is None or self._release is not None:
            raise RuntimeError(f"camera {self.name} is not open")
        return self._capture

    async def _call[T](self, call: Callable[[], T]) -> T:
        return await asyncio.wrap_future(self._thread.submit(call))
