from httpx import AsyncClient

from tests.integration.conftest import PASSWORD, register


async def test_login_is_limited_per_email(client: AsyncClient) -> None:
    await register(client)
    attempt = {"email": "ann@example.com", "password": "wrong password"}
    for _ in range(5):
        assert (await client.post("/api/auth/login", json=attempt)).status_code == 401

    blocked = await client.post("/api/auth/login", json=attempt)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(blocked.headers["retry-after"]) <= 60

    # Even the right password waits: the bucket counts attempts, not failures.
    right = await client.post(
        "/api/auth/login", json={"email": "ann@example.com", "password": PASSWORD}
    )
    assert right.status_code == 429


async def test_login_limit_is_per_email_and_ignores_case(client: AsyncClient) -> None:
    for i in range(5):
        email = "Ann@Example.com" if i % 2 else "ann@example.com"
        body = {"email": email, "password": "wrong password"}
        assert (await client.post("/api/auth/login", json=body)).status_code == 401
    body = {"email": "ANN@EXAMPLE.COM", "password": "wrong password"}
    assert (await client.post("/api/auth/login", json=body)).status_code == 429

    # A different account is unaffected.
    other = {"email": "bob@example.com", "password": "wrong password"}
    assert (await client.post("/api/auth/login", json=other)).status_code == 401


async def test_register_is_limited_per_ip(client: AsyncClient) -> None:
    for i in range(5):
        await register(client, f"user{i}@example.com")
    response = await client.post(
        "/api/auth/register",
        json={"email": "user5@example.com", "password": PASSWORD, "display_name": "Six"},
    )
    assert response.status_code == 429
    assert "retry-after" in response.headers
