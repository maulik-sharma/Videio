"""Pure-ASGI middleware: request IDs and the CSRF header check."""

import json
import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware:
    """Reuse a sane incoming X-Request-Id (e.g. from the edge) or mint one; echo it back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_id)


CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "videio"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CsrfHeaderMiddleware:
    """Reject mutating /api requests that lack `X-Requested-With: videio`.

    A cross-site form or <img> cannot set custom headers, and a cross-site fetch
    that sets one triggers a CORS preflight we never approve. Together with
    SameSite=Lax cookies this blocks CSRF without per-session tokens.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in _SAFE_METHODS
            and scope["path"].startswith("/api/")
            and dict(scope["headers"]).get(CSRF_HEADER.encode()) != CSRF_VALUE.encode()
        ):
            body = json.dumps(
                error_body(
                    "CSRF_HEADER_REQUIRED",
                    "Mutating requests must send the header X-Requested-With: videio.",
                )
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
