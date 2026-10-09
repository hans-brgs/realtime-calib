"""The service's shutdown stops capture, then reaps every transcode (SYN-9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from calibration_service import app as app_module
from calibration_service.app import create_app
from calibration_service.session.manager import SessionManager


def test_shutdown_stops_capture_then_reaps_the_transcodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class _Capture:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("capture stop failed")  # the transcodes are reaped anyway

    monkeypatch.setattr(app_module, "CameraPublishService", _Capture)
    app = create_app(SessionManager(tmp_path, "default"))

    async def aclose() -> None:
        calls.append("aclose")

    monkeypatch.setattr(app.state.preview_jobs, "aclose", aclose)
    with pytest.raises(RuntimeError, match="capture stop failed"), TestClient(app):
        pass
    assert calls == ["start", "stop", "aclose"]
