"""Gunicorn configuration for the token server (bind, workers, logging)."""

from __future__ import annotations

import os

bind = f"0.0.0.0:{os.environ.get('TOKEN_SERVER_PORT', '8080')}"
workers = 2
accesslog = "-"
errorlog = "-"
