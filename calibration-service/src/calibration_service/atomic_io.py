"""Crash-safe replacement of the service's state files (ADR-0011).

The session folder is the source of truth, so a state file must never be seen
half-written: a reader (or the next start after a crash) gets the previous
version or the new one, nothing in between.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text`` atomically.

    The temp file sits next to the target (same filesystem, so ``os.replace`` is
    an atomic rename) under a unique name (two concurrent writers never share
    one), and is fsync'd before the rename so a power cut cannot leave an empty
    file under the final name. Created through ``os.open`` with mode 0o666 so the
    umask applies exactly as for ``Path.write_text`` — ``mkstemp``'s 0o600 would
    hide the files from the host user of the mounted sessions folder. Stdlib
    only: the ``atomicwrites`` package that used to cover this is archived.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def replace_directory(directory: Path, files: Mapping[str, str]) -> None:
    """Make ``directory`` hold exactly ``files`` (name -> text), swapped in whole.

    The set is written to a sibling staging folder, then renamed in place of the
    old one: a reader sees the previous complete set, the new one, or (for the
    instant between the two renames) no folder — never a mix, and nothing of an
    earlier write survives. The export archive zips this folder; stale artefacts
    from a previous selection used to ship next to the new ones. Both renames
    stay within the parent, so each one is atomic; if the second one fails, the
    previous set is renamed back.
    """
    staging = directory.with_name(f".{directory.name}.{uuid.uuid4().hex[:12]}.new")
    staging.mkdir(parents=True)  # mode 0o777 under the umask, like the folder it replaces
    try:
        for name, text in files.items():
            (staging / name).write_text(text, encoding="utf-8")
        if directory.exists():
            retired = directory.with_name(f".{directory.name}.{uuid.uuid4().hex[:12]}.old")
            os.replace(directory, retired)
            try:
                os.replace(staging, directory)
            except BaseException:
                os.replace(retired, directory)  # put the previous set back
                raise
            shutil.rmtree(retired, ignore_errors=True)
        else:
            os.replace(staging, directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
