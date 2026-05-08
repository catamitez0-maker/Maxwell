"""
Tests for maxwell.auth — API HMAC-SHA256 authentication.
"""

import hashlib
import hmac
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from maxwell.auth import (
    APIKeyStore,
    auth_middleware,
    generate_api_key,
    verify_hmac,
    HEADER_KEY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
)


# ── Unit Tests ───────────────────────────────────────────────────────


class TestGenerateAPIKey:
    def test_returns_tuple(self) -> None:
        key_id, secret = generate_api_key()
        assert key_id.startswith("mxk_")
        assert len(secret) == 64  # 32 bytes hex

    def test_unique_keys(self) -> None:
        keys = {generate_api_key() for _ in range(10)}
        assert len(keys) == 10


class TestVerifyHMAC:
    def test_valid_signature(self) -> None:
        secret = "mysecret"
        ts = "1234567890"
        body = '{"payload": "test"}'
        sig = hmac.new(
            secret.encode(), (ts + body).encode(), hashlib.sha256,
        ).hexdigest()
        assert verify_hmac(secret, ts, body, sig) is True

    def test_wrong_secret_fails(self) -> None:
        secret = "mysecret"
        ts = "1234567890"
        body = '{"payload": "test"}'
        sig = hmac.new(
            secret.encode(), (ts + body).encode(), hashlib.sha256,
        ).hexdigest()
        assert verify_hmac("wrongsecret", ts, body, sig) is False

    def test_tampered_body_fails(self) -> None:
        secret = "mysecret"
        ts = "1234567890"
        body = '{"payload": "test"}'
        sig = hmac.new(
            secret.encode(), (ts + body).encode(), hashlib.sha256,
        ).hexdigest()
        assert verify_hmac(secret, ts, '{"payload": "hack"}', sig) is False

    def test_wrong_timestamp_fails(self) -> None:
        secret = "mysecret"
        ts = "1234567890"
        body = '{"payload": "test"}'
        sig = hmac.new(
            secret.encode(), (ts + body).encode(), hashlib.sha256,
        ).hexdigest()
        assert verify_hmac(secret, "9999999999", body, sig) is False


class TestAPIKeyStore:
    def test_add_and_get(self) -> None:
        store = APIKeyStore()
        store.add("key1", "secret1")
        assert store.get_secret("key1") == "secret1"
        assert store.enabled is True

    def test_unknown_key_returns_none(self) -> None:
        store = APIKeyStore()
        assert store.get_secret("nonexistent") is None

    def test_disabled_when_empty(self) -> None:
        store = APIKeyStore()
        assert store.enabled is False

    def test_load_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAXWELL_API_KEYS", "k1:s1,k2:s2")
        store = APIKeyStore()
        count = store.load_from_env()
        assert count == 2
        assert store.get_secret("k1") == "s1"
        assert store.get_secret("k2") == "s2"

    def test_load_from_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAXWELL_API_KEYS", raising=False)
        store = APIKeyStore()
        count = store.load_from_env()
        assert count == 0

    def test_load_from_file(self, tmp_path: pytest.TempPathFactory) -> None:
        import json
        keys_file = tmp_path / "keys.json"
        keys_file.write_text(json.dumps({"keys": {"mxk_abc": "sec123"}}))
        store = APIKeyStore()
        count = store.load_from_file(str(keys_file))
        assert count == 1
        assert store.get_secret("mxk_abc") == "sec123"

    def test_load_from_missing_file(self) -> None:
        store = APIKeyStore()
        count = store.load_from_file("/nonexistent/path.json")
        assert count == 0


# ── Integration Tests (middleware) ────────────────────────────────────


def _make_signed_headers(key_id: str, secret: str, body: str) -> dict[str, str]:
    """Helper to create properly signed request headers."""
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode(), (ts + body).encode(), hashlib.sha256,
    ).hexdigest()
    return {
        HEADER_KEY: key_id,
        HEADER_SIGNATURE: sig,
        HEADER_TIMESTAMP: ts,
    }


@pytest.fixture
def auth_app() -> web.Application:
    """Create an aiohttp app with auth middleware and a test endpoint."""
    store = APIKeyStore()
    store.add("test_key", "test_secret")

    async def protected_handler(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "key": request.get("authenticated_key", "")})

    async def health_handler(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application(middlewares=[auth_middleware])
    app["api_key_store"] = store
    app.router.add_post("/v1/proxy", protected_handler)
    app.router.add_get("/healthz", health_handler)
    return app


@pytest.mark.asyncio
async def test_no_auth_headers_returns_401(aiohttp_client, auth_app: web.Application) -> None:
    client = await aiohttp_client(auth_app)
    resp = await client.post("/v1/proxy", json={"payload": "test"})
    assert resp.status == 401


@pytest.mark.asyncio
async def test_invalid_key_returns_401(aiohttp_client, auth_app: web.Application) -> None:
    client = await aiohttp_client(auth_app)
    headers = _make_signed_headers("bad_key", "bad_secret", '{"payload": "test"}')
    resp = await client.post("/v1/proxy", json={"payload": "test"}, headers=headers)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_valid_auth_passes(aiohttp_client, auth_app: web.Application) -> None:
    body = '{"payload": "test"}'
    client = await aiohttp_client(auth_app)
    headers = _make_signed_headers("test_key", "test_secret", body)
    resp = await client.post("/v1/proxy", data=body, headers={**headers, "Content-Type": "application/json"})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["key"] == "test_key"


@pytest.mark.asyncio
async def test_tampered_body_returns_401(aiohttp_client, auth_app: web.Application) -> None:
    client = await aiohttp_client(auth_app)
    headers = _make_signed_headers("test_key", "test_secret", '{"payload": "original"}')
    resp = await client.post("/v1/proxy", json={"payload": "tampered"}, headers=headers)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_expired_timestamp_returns_401(aiohttp_client, auth_app: web.Application) -> None:
    body = '{"payload": "test"}'
    old_ts = str(int(time.time()) - 600)  # 10 minutes ago
    sig = hmac.new(
        b"test_secret", (old_ts + body).encode(), hashlib.sha256,
    ).hexdigest()
    headers = {
        HEADER_KEY: "test_key",
        HEADER_SIGNATURE: sig,
        HEADER_TIMESTAMP: old_ts,
    }
    client = await aiohttp_client(auth_app)
    resp = await client.post("/v1/proxy", data=body, headers={**headers, "Content-Type": "application/json"})
    assert resp.status == 401


@pytest.mark.asyncio
async def test_public_path_bypasses_auth(aiohttp_client, auth_app: web.Application) -> None:
    client = await aiohttp_client(auth_app)
    resp = await client.get("/healthz")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_no_keys_configured_allows_all(aiohttp_client) -> None:
    """When no keys are configured, auth is disabled (open mode)."""
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[auth_middleware])
    # No api_key_store set
    app.router.add_post("/v1/proxy", handler)

    client = await aiohttp_client(app)
    resp = await client.post("/v1/proxy", json={"payload": "test"})
    assert resp.status == 200
