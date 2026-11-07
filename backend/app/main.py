from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api import admin, auth, health, internal, users, videos
from app.core import logging as app_logging
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.middleware import CsrfHeaderMiddleware, RequestIdMiddleware
from app.db.engines import Database
from app.services.storage import S3Storage
from app.worker.broker import broker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.db = Database.from_settings(settings)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.storage = S3Storage.from_settings(settings)
    app.state.broker = broker
    try:
        await broker.startup()
        yield
    finally:
        await broker.shutdown()
        await app.state.redis.aclose()
        await app.state.db.dispose()


def create_app() -> FastAPI:
    # Structured JSON logging must be installed before the first log message,
    # including those emitted by uvicorn during startup.
    app_logging.configure()
    settings = get_settings()
    app = FastAPI(
        title="Videio API",
        version=settings.version,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    # Added last runs first: the request id wraps everything, including CSRF rejections.
    app.add_middleware(CsrfHeaderMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(videos.router)
    app.include_router(admin.router)
    app.include_router(internal.router)
    return app


app = create_app()
