from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import BrokerDep, DbDep, RedisDep, SettingsDep

router = APIRouter(tags=["ops"])


class Health(BaseModel):
    status: str
    version: str


@router.get("/healthz")
async def healthz(settings: SettingsDep) -> Health:
    """Liveness: the process is up and serving requests. Checks no dependencies."""
    return Health(status="ok", version=settings.version)


@router.get("/readyz", responses={503: {"description": "A dependency is unreachable"}})
async def readyz(
    db: DbDep, redis: RedisDep, broker: BrokerDep, settings: SettingsDep
) -> JSONResponse:
    """Readiness: databases, Redis and RabbitMQ all answer."""
    checks: dict[str, str] = {}
    for name, engine in (("db_primary", db.write_engine), ("db_replica", db.read_engine)):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks[name] = "ok"
        except Exception as exc:
            checks[name] = f"error: {type(exc).__name__}"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    try:
        if broker.write_channel is None or broker.write_channel.is_closed:
            raise ConnectionError("RabbitMQ broker is not connected")
        checks["rabbitmq"] = "ok"
    except Exception as exc:
        checks["rabbitmq"] = f"error: {type(exc).__name__}"

    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "unavailable", "version": settings.version, "checks": checks},
        status_code=200 if ok else 503,
    )
