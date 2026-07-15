"""Background H.264 preview transcodes (ADR-0027): job states + CFR contract."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from calibration_service.recording import (
    PreviewJobs,
    PreviewState,
    VideoRecorder,
    preview_path,
)
from calibration_service.recording import preview as preview_module


def _record(path: Path, frames: int, fps: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with VideoRecorder(path, 64, 48, fps=fps) as recorder:
        for _ in range(frames):
            recorder.write(np.zeros((48, 64, 3), dtype=np.uint8))


async def _drain(jobs: PreviewJobs) -> None:
    while any(not task.done() for task in jobs._tasks.values()):
        await asyncio.gather(*jobs._tasks.values(), return_exceptions=True)


def test_status_missing_without_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        jobs = PreviewJobs()
        assert (await jobs.status(tmp_path / "none.mkv")).state is PreviewState.MISSING

    asyncio.run(scenario())


def test_failed_then_explicit_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed transcode NEVER re-enqueues silently: status stays FAILED with the
    # error; retry() is the explicit path (webapp Retry button). Hermetic: fake
    # commands replace ffmpeg.
    source = tmp_path / "cam_0.mkv"
    _record(source, 2)

    async def scenario() -> None:
        jobs = PreviewJobs()
        monkeypatch.setattr(preview_module, "transcode_args", lambda s, d, fps: ["/bin/false"])
        jobs.ensure(source)
        await _drain(jobs)
        status = await jobs.status(source)
        assert status.state is PreviewState.FAILED
        assert status.error

        # status() must not have re-enqueued anything while failed.
        assert all(task.done() for task in jobs._tasks.values())

        monkeypatch.setattr(
            preview_module, "transcode_args", lambda s, d, fps: ["/bin/cp", str(s), str(d)]
        )
        jobs.retry(source)
        await _drain(jobs)
        assert (await jobs.status(source)).state is PreviewState.DONE
        assert preview_path(source).is_file()

    asyncio.run(scenario())


def test_invalidate_drops_job_and_stale_mp4(tmp_path: Path) -> None:
    source = tmp_path / "cam_0.mkv"
    _record(source, 1)
    stale = preview_path(source)
    stale.write_bytes(b"stale")

    async def scenario() -> None:
        jobs = PreviewJobs()
        jobs.invalidate(source)
        assert not stale.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("fps", [30, 15])
def test_real_transcode_preserves_frames_and_cfr_at_source_fps(
    tmp_path: Path, fps: int
) -> None:
    # The ADR-0027/0037 contract: frame count preserved 1:1 and CFR at the
    # RECORDING's declared fps (served in the status), so the webapp's
    # index = round(currentTime * status.fps) is exact AND playback speed is true
    # whatever the configured capture rate.
    source = tmp_path / "cam_0.mkv"
    _record(source, 5, fps=fps)

    async def scenario() -> None:
        jobs = PreviewJobs()
        assert (await jobs.status(source)).state is PreviewState.RUNNING  # auto-enqueued
        await _drain(jobs)
        status = await jobs.status(source)
        assert status.state is PreviewState.DONE
        assert status.frames == 5
        assert status.fps == pytest.approx(fps)

    asyncio.run(scenario())

    capture = cv2.VideoCapture(str(preview_path(source)))
    try:
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 5
        assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(fps)
    finally:
        capture.release()
