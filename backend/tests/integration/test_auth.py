from email.message import Message

from fastapi import FastAPI
from httpx import AsyncClient, Response

from tests.integration.conftest import PASSWORD, login, make_client, register


def set_cookies(response: Response) -> dict[str, str]:
    """Raw Set-Cookie headers by cookie name, to check attributes."""
    cookies: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        cookies[header.split("=", 1)[0]] = header
    return cookies


def cookie_attrs(header: str) -> Message:
    msg = Message()
    for part in header.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        msg[key.lower()] = value
    return msg


async def test_register_returns_the_account_without_logging_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": "ann@example.com", "password": PASSWORD, "display_name": " Ann "},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ann@example.com"
    assert body["display_name"] == "Ann"
    assert body["role"] == "user"
    assert "password_hash" not in body
    assert "set-cookie" not in response.headers


async def test_email_is_unique_regardless_of_case(client: AsyncClient) -> None:
    await register(client, "ann@example.com")
    response = await client.post(
        "/api/auth/register",
        json={"email": "ANN@example.com", "password": PASSWORD, "display_name": "Other"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


async def test_validation_errors_list_each_field(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": "nope", "password": "short", "display_name": ""}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert set(error["fields"]) == {"email", "password", "display_name"}


async def test_login_sets_hardened_session_cookies(client: AsyncClient) -> None:
    account = await register(client)
    response = await client.post(
        "/api/auth/login", json={"email": "ANN@Example.com", "password": account.password}
    )
    assert response.status_code == 200
    cookies = set_cookies(response)

    access = cookie_attrs(cookies["videio_at"])
    assert access["path"] == "/"
    assert access["max-age"] == "900"
    refresh = cookie_attrs(cookies["videio_rt"])
    assert refresh["path"] == "/api/auth"
    assert refresh["max-age"] == str(7 * 24 * 3600)
    for attrs in (access, refresh):
        assert "httponly" in attrs
        assert "secure" in attrs
        assert attrs["samesite"].lower() == "lax"

    me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["id"] == str(account.id)


async def test_wrong_password_and_unknown_email_look_the_same(client: AsyncClient) -> None:
    await register(client)
    wrong = await client.post(
        "/api/auth/login", json={"email": "ann@example.com", "password": "wrong password"}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_me_requires_a_session(client: AsyncClient) -> None:
    response = await client.get("/api/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_refresh_rotates_the_refresh_token(client: AsyncClient) -> None:
    await login(client, await register(client))
    old_refresh = client.cookies.get("videio_rt")

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 200
    new_refresh = client.cookies.get("videio_rt")
    assert new_refresh and new_refresh != old_refresh
    assert "videio_at" in set_cookies(response)

    # The rotated token keeps working.
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_reusing_a_rotated_refresh_token_revokes_every_session(
    app: FastAPI, client: AsyncClient
) -> None:
    account = await register(client)
    await login(client, account)
    stolen = client.cookies.get("videio_rt")
    assert (await client.post("/api/auth/refresh")).status_code == 200  # legit rotation

    # A second, independent session for the same user (another device).
    async with make_client(app) as other_device:
        await login(other_device, account)

        # The attacker replays the stolen (already rotated) token.
        async with make_client(app) as attacker:
            replay = await attacker.post(
                "/api/auth/refresh", headers={"Cookie": f"videio_rt={stolen}"}
            )
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

        # Every session of that user is now gone, including the legitimate ones.
        for session in (client, other_device):
            response = await session.post("/api/auth/refresh")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_failed_refresh_clears_the_cookies(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/refresh", headers={"Cookie": "videio_rt=not-a-real-token"}
    )
    assert response.status_code == 401
    cookies = set_cookies(response)
    assert cookie_attrs(cookies["videio_at"])["max-age"] == "0"
    assert cookie_attrs(cookies["videio_rt"])["max-age"] == "0"


async def test_logout_revokes_the_refresh_token(app: FastAPI, client: AsyncClient) -> None:
    await login(client, await register(client))
    token = client.cookies.get("videio_rt")

    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert cookie_attrs(set_cookies(response)["videio_rt"])["max-age"] == "0"

    async with make_client(app) as replay:
        response = await replay.post("/api/auth/refresh", headers={"Cookie": f"videio_rt={token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_logout_without_a_session_is_harmless(client: AsyncClient) -> None:
    assert (await client.post("/api/auth/logout")).status_code == 204


async def test_auth_endpoints_require_the_csrf_header(app: FastAPI) -> None:
    async with make_client(app, csrf=False) as client:
        for path in ("/api/auth/register", "/api/auth/login", "/api/auth/refresh"):
            response = await client.post(path, json={})
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "CSRF_HEADER_REQUIRED"
