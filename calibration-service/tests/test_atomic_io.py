"""Crash-safe file and folder replacement (ADR-0011: the session folder is the truth)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from calibration_service.atomic_io import atomic_write_text, replace_directory


def test_write_replaces_content_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "state" / "result.json"
    atomic_write_text(target, "old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert [p.name for p in target.parent.iterdir()] == ["result.json"]


def test_failed_write_keeps_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "result.json"
    atomic_write_text(target, "previous")

    def _crash(*_args: object) -> None:
        raise OSError("simulated crash before the rename")

    monkeypatch.setattr("calibration_service.atomic_io.os.replace", _crash)
    with pytest.raises(OSError):
        atomic_write_text(target, "half-written")
    assert target.read_text() == "previous"
    assert [p.name for p in tmp_path.iterdir()] == ["result.json"]  # temp cleaned up


def test_written_file_follows_the_umask_like_write_text(tmp_path: Path) -> None:
    # mkstemp would create 0o600 files: unreadable by the host user of the
    # mounted sessions folder when the container writes them.
    previous = os.umask(0o022)
    try:
        atomic_write_text(tmp_path / "a.toml", "x")
        (tmp_path / "b.toml").write_text("x")
    finally:
        os.umask(previous)
    assert (tmp_path / "a.toml").stat().st_mode & 0o777 == (
        tmp_path / "b.toml"
    ).stat().st_mode & 0o777


def test_replace_directory_holds_exactly_the_new_set(tmp_path: Path) -> None:
    folder = tmp_path / "export"
    replace_directory(folder, {"camera_array.toml": "toml", "camera_array_unity.json": "{}"})
    replace_directory(folder, {"camera_array_unity.json": '{"v": 2}'})
    assert sorted(p.name for p in folder.iterdir()) == ["camera_array_unity.json"]
    assert (folder / "camera_array_unity.json").read_text() == '{"v": 2}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export"]  # no staging left


def test_failed_directory_replacement_keeps_the_previous_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "export"
    replace_directory(folder, {"camera_array.toml": "v1"})

    def _crash(*_args: object) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr("calibration_service.atomic_io.os.replace", _crash)
    with pytest.raises(OSError):
        replace_directory(folder, {"camera_array.toml": "v2"})
    assert (folder / "camera_array.toml").read_text() == "v1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export"]


def test_failed_swap_restores_the_previous_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first rename (old set aside) succeeds, the second (new set in) fails:
    # the old set must be renamed back, not left hidden in a ".old" folder.
    folder = tmp_path / "export"
    replace_directory(folder, {"camera_array.toml": "v1"})
    real_replace = os.replace
    calls: list[object] = []

    def _second_fails(src: object, dst: object) -> None:
        calls.append(src)
        if len(calls) == 2:
            raise OSError("simulated crash between the renames")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr("calibration_service.atomic_io.os.replace", _second_fails)
    with pytest.raises(OSError):
        replace_directory(folder, {"camera_array.toml": "v2"})
    assert (folder / "camera_array.toml").read_text() == "v1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export"]
