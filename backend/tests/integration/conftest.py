"""Integration fixtures: the real app against real Postgres (primary + replica) and Redis.

Start the services first:
    docker compose -f compose.yaml -f compose.ci.yaml up -d --build --wait
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text

from app.db.engines import Database
from app.main import create_app

CLIENT_IP = "203.0.113.10"
PASSWORD = "correct horse battery"


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
def db(app: FastAPI) -> Database:
    database: Database = app.state.db
    return database


@pytest.fixture
def redis(app: FastAPI) -> Redis:
    client: Redis = app.state.redis
    return client


async def wait_for_replica(db: Database, timeout_s: float = 10) -> None:
    """Block until the replica has replayed everything the primary has written."""
    async with db.write_engine.connect() as conn:
        target = await conn.scalar(text("SELECT pg_current_wal_lsn()"))
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        async with db.read_engine.connect() as conn:
            behind = await conn.scalar(
                text("SELECT pg_wal_lsn_diff(:target, pg_last_wal_replay_lsn()) > 0"),
                {"target": target},
            )
        if not behind:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("replica did not catch up")
        await asyncio.sleep(0.05)


@pytest.fixture(autouse=True)
async def clean_state(db: Database, redis: Redis) -> None:
    async with db.write_engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await redis.flushdb()
    await wait_for_replica(db)


def make_client(app: FastAPI, *, csrf: bool = True) -> AsyncClient:
    transport = ASGITransport(app=app, client=(CLIENT_IP, 40000))
    headers = {"X-Requested-With": "videio"} if csrf else {}
    # https: the session cookies are Secure, so they are only sent over HTTPS.
    return AsyncClient(transport=transport, base_url="https://testserver", headers=headers)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with make_client(app) as client:
        yield client


@dataclass
class Account:
    id: uuid.UUID
    email: str
    password: str


async def register(client: AsyncClient, email: str = "ann@example.com") -> Account:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Ann"},
    )
    assert response.status_code == 201, response.text
    return Account(id=uuid.UUID(response.json()["id"]), email=email, password=PASSWORD)


async def login(client: AsyncClient, account: Account) -> None:
    response = await client.post(
        "/api/auth/login", json={"email": account.email, "password": account.password}
    )
    assert response.status_code == 200, response.text
