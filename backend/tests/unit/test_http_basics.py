"""Middleware and error shape. No database: these requests never reach a DB dependency."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        yield client


async def test_mutating_api_request_without_csrf_header_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_HEADER_REQUIRED"


async def test_every_response_gets_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert len(response.headers["x-request-id"]) == 32


async def test_a_sane_incoming_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "edge-abc.123"})
    assert response.headers["x-request-id"] == "edge-abc.123"


async def test_a_malformed_incoming_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "bad id <script>"})
    assert response.headers["x-request-id"] != "bad id <script>"


async def test_unknown_route_uses_the_error_shape(client: AsyncClient) -> None:
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}
