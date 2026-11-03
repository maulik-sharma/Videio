"""One error shape for every API response: {"error": {"code", "message", ...}}."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Raise from routes and services; rendered by the handler below."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers
        self.extra = extra or {}


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}


def _status_code_name(status: int) -> str:
    try:
        return HTTPStatus(status).name
    except ValueError:
        return "ERROR"


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        error_body(exc.code, exc.message, **exc.extra), exc.status, headers=exc.headers
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        error_body(_status_code_name(exc.status_code), str(exc.detail)),
        exc.status_code,
        headers=exc.headers,
    )


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Map each failing field to its first message so forms can show inline errors.
    fields: dict[str, str] = {}
    for err in exc.errors():
        loc = [str(part) for part in err["loc"] if part not in ("body", "query", "path")]
        name = ".".join(loc) or "request"
        fields.setdefault(name, err["msg"])
    return JSONResponse(
        error_body("VALIDATION_ERROR", "Some fields are invalid.", fields=fields), 422
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(error_body("INTERNAL_ERROR", "Something went wrong on our side."), 500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
