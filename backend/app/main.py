from fastapi import FastAPI
from pydantic import BaseModel

from app.core.config import get_settings


class Health(BaseModel):
    status: str
    version: str


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Videio API",
        version=settings.version,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> Health:
        """Liveness: the process is up and serving requests. Checks no dependencies."""
        return Health(status="ok", version=settings.version)

    return app


app = create_app()
