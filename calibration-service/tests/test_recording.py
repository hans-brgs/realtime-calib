"""Recording tests: round-trip + session path."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from calibration_service.recording import RecordingError, VideoRecorder, intrinsic_capture_path


def test_intrinsic_capture_path(tmp_path: Path) -> None:
    path = intrinsic_capture_path(tmp_path, "demo", "cam_0")
    assert path == tmp_path / "demo" / "intrinsic" / "cam_0" / "capture.mkv"


def test_record_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cap.mkv"
    w, h, n = 320, 240, 12
    with VideoRecorder(path, w, h, fps=30) as rec:
        for i in range(n):
            frame = np.zeros((h, w, 3), np.uint8)
            frame[:, i * 10 : i * 10 + 8] = (0, 0, 255)  # a moving red bar
            rec.write(frame)
        assert rec.frames == n
    assert path.is_file()

    cap = cv2.VideoCapture(str(path))
    read = 0
    dims = None
    while True:
        ok, decoded = cap.read()
        if not ok or decoded is None:
            break
        read += 1
        dims = decoded.shape
    cap.release()
    assert read == n
    assert dims == (h, w, 3)


def test_a_frame_of_another_size_is_refused_not_silently_dropped(tmp_path: Path) -> None:
    # cv2.VideoWriter silently drops it: the count advanced over a frame the file
    # never got (a manifest announcing N frames over an empty video).
    with VideoRecorder(tmp_path / "cap.mkv", 320, 240, fps=30) as rec:
        with pytest.raises(RecordingError, match="320x240"):
            rec.write(np.zeros((480, 640, 3), np.uint8))
        assert rec.frames == 0
