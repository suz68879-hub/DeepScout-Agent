import httpx
from fastapi import FastAPI

import api.auth as auth_api


async def test_register_me_and_logout_cookie_flow(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    monkeypatch.setattr(auth_api, "storage", storage)
    auth_api.reset_rate_limits()
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        registered = await client.post(
            "/api/auth/register", json={"username": "Alice_01", "password": "password-123"},
        )
        assert registered.status_code == 201
        assert registered.json() == {"id": registered.json()["id"], "username": "alice_01"}
        cookie = registered.headers["set-cookie"]
        assert "interview_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie

        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "alice_01"

        logout = await client.post("/api/auth/logout")
        assert logout.status_code == 204
        assert (await client.get("/api/auth/me")).status_code == 401
    await storage.close()


async def test_duplicate_username_and_generic_login_error(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    monkeypatch.setattr(auth_api, "storage", storage)
    auth_api.reset_rate_limits()
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = {"username": "alice", "password": "password-123"}
        assert (await client.post("/api/auth/register", json=body)).status_code == 201
        assert (await client.post("/api/auth/register", json=body)).status_code == 409
        response = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid username or password"}
        malformed = await client.post(
            "/api/auth/login", json={"username": "用户", "password": "wrong-password"},
        )
        assert malformed.status_code == 401
        assert malformed.json() == {"detail": "invalid username or password"}
    await storage.close()


async def test_login_rate_limit_is_process_local(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    monkeypatch.setattr(auth_api, "storage", storage)
    auth_api.reset_rate_limits()
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            response = await client.post(
                "/api/auth/login", json={"username": "missing", "password": "wrong-password"},
            )
            assert response.status_code == 401
        response = await client.post(
            "/api/auth/login", json={"username": "missing", "password": "wrong-password"},
        )
        assert response.status_code == 429
    await storage.close()
