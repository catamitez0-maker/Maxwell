import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import MagicMock
from maxwell.api import MaxwellServer

@pytest.fixture
def mock_proxy():
    proxy = MagicMock()
    proxy.stats = MagicMock()
    proxy.stats.uptime = 123.456
    proxy.stats.total_requests = 100
    proxy.stats.pruning_rate = 15.678
    proxy.stats.is_circuit_open = False
    return proxy

@pytest.mark.asyncio
async def test_handle_health(mock_proxy):
    server = MaxwellServer(proxy=mock_proxy)
    app = web.Application()
    app.router.add_get("/healthz", server.handle_health)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        data = await resp.json()

        assert data["status"] == "ok"
        assert data["uptime"] == 123.5
        assert data["total_requests"] == 100
        assert data["pruning_rate"] == 15.68
        assert data["circuit_breaker"] == "CLOSED"

@pytest.mark.asyncio
async def test_handle_health_circuit_open(mock_proxy):
    mock_proxy.stats.is_circuit_open = True
    server = MaxwellServer(proxy=mock_proxy)
    app = web.Application()
    app.router.add_get("/healthz", server.handle_health)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        data = await resp.json()
        assert data["circuit_breaker"] == "OPEN"
