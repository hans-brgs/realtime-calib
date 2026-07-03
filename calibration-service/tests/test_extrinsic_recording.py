"""ExtrinsicRecorder: N videos + timestamp sidecars + manifest (spec calibration-recording)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from calibration_service.recording import CameraSpec, ExtrinsicRecorder, read_timestamps


def _image(width: int = 64, height: int = 48) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_records_one_video_and_sidecar_per_camera(tmp_path: Path) -> None:
    recorder = ExtrinsicRecorder(
        tmp_path, [CameraSpec("cam_0", 64, 48, 30), CameraSpec("cam_1", 64, 48, 30)]
    )
    recorder.write("cam_0", _image(), 10.000000)
    recorder.write("cam_1", _image(), 10.005)
    recorder.write("cam_0", _image(), 10.033)
    counts = recorder.close()

    assert counts == {"cam_0": 2, "cam_1": 1}
    # Sidecars line up with the written frames and read back as floats.
    assert read_timestamps(tmp_path / "cam_0.timestamps") == [10.0, 10.033]
    assert read_timestamps(tmp_path / "cam_1.timestamps") == [10.005]
    # Videos are decodable with the declared frame counts.
    for name, expected in counts.items():
        capture = cv2.VideoCapture(str(tmp_path / f"{name}.mkv"))
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == expected
        capture.release()


def test_manifest_lists_every_camera_artifact(tmp_path: Path) -> None:
    recorder = ExtrinsicRecorder(tmp_path, [CameraSpec("cam_0", 64, 48, 15)])
    recorder.write("cam_0", _image(), 1.0)
    recorder.close()

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["cameras"] == [
        {
            "name": "cam_0",
            "video": "cam_0.mkv",
            "timestamps": "cam_0.timestamps",
            "width": 64,
            "height": 48,
            "fps": 15,
            "frames": 1,
        }
    ]


def test_unknown_camera_writes_are_ignored(tmp_path: Path) -> None:
    recorder = ExtrinsicRecorder(tmp_path, [CameraSpec("cam_0", 64, 48, 30)])
    recorder.write("cam_9", _image(), 1.0)  # not part of the sweep -> no-op
    assert recorder.close() == {"cam_0": 0}
