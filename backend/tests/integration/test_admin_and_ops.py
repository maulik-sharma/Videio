from httpx import AsyncClient
from sqlalchemy import text

from app import cli
from app.db.engines import Database
from tests.integration.conftest import login, register, wait_for_replica


async def test_system_panel_shows_replica_lag_to_admins(client: AsyncClient) -> None:
    account = await register(client)
    assert await cli.promote_admin(account.email)
    await login(client, account)

    response = await client.get("/api/admin/system")
    assert response.status_code == 200
    replication = response.json()["replication"]
    assert replication["read_db_in_recovery"] is True
    [replica] = replication["replicas"]
    assert replica["name"] == "core-replica"
    assert replica["state"] == "streaming"
    assert replica["lag_bytes"] >= 0


async def test_system_panel_is_admin_only(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/system")).status_code == 401
    await login(client, await register(client))
    response = await client.get("/api/admin/system")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_promote_admin_cli_reports_unknown_email() -> None:
    assert await cli.promote_admin("nobody@example.com") is False


async def test_disabled_accounts_cannot_log_in_or_use_a_session(
    client: AsyncClient, db: Database
) -> None:
    account = await register(client)
    await login(client, account)
    async with db.write_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET disabled_at = now() WHERE id = :id"), {"id": account.id}
        )
    await wait_for_replica(db)

    me = await client.get("/api/me")
    assert me.status_code == 403
    assert me.json()["error"]["code"] == "ACCOUNT_DISABLED"

    response = await client.post(
        "/api/auth/login", json={"email": account.email, "password": account.password}
    )
    assert response.status_code == 403
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_readyz_checks_every_dependency(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["checks"] == {"db_primary": "ok", "db_replica": "ok", "redis": "ok"}
