"""Flask app issuing subscribe-only LiveKit access tokens.

The webapp fetches a token here, then joins the LiveKit room to subscribe to the
camera tracks published by ``calibration-service`` (ADR-0004). Tokens are
subscribe-only (``can_publish=False``): only the service publishes video.
"""

from __future__ import annotations

import logging
import uuid

from flask import Flask, Response, jsonify, request
from livekit import api

from livekit_token_server.config import Settings, load_settings

logger = logging.getLogger(__name__)

SERVICE_NAME = "livekit-token-server"


def create_app(settings: Settings | None = None) -> Flask:
    """Build the Flask app (factory so tests can inject settings)."""
    resolved = settings if settings is not None else load_settings()
    app = Flask(__name__)

    @app.get("/health")
    def health() -> Response:
        return jsonify(status="ok", service=SERVICE_NAME)

    @app.get("/token")
    def token() -> Response:
        identity = request.args.get("identity") or f"viewer-{uuid.uuid4().hex[:8]}"
        room = request.args.get("room") or resolved.room_name

        grants = api.VideoGrants(
            room_join=True,
            room=room,
            can_subscribe=True,
            can_publish=False,
        )
        jwt = (
            api.AccessToken(resolved.livekit_api_key, resolved.livekit_api_secret)
            .with_identity(identity)
            .with_name(identity)
            .with_grants(grants)
            .to_jwt()
        )
        return jsonify(token=jwt, room=room, identity=identity)

    @app.after_request
    def allow_cors(response: Response) -> Response:
        # LAN tool issuing subscribe-only tokens; permissive CORS keeps the vite
        # dev server (different origin than the token server) friction-free.
        # Behind Caddy (default profile) requests are same-origin anyway.
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    settings = load_settings()
    app = create_app(settings)
    logger.info("Starting %s on 0.0.0.0:%d", SERVICE_NAME, settings.port)
    app.run(host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
