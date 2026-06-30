"""Process-wide logging configuration.

Use ``logging`` everywhere (never ``print``), with a module-level logger
``logger = logging.getLogger(__name__)`` per module.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger to emit to stdout with a single handler.

    Idempotent: clears existing handlers so repeated calls (tests, reloads)
    do not duplicate log lines.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
