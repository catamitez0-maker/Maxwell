"""
Tests for maxwell.api — HTTP/WebSocket API endpoints.
"""

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

from maxwell.api import MaxwellServer
from maxwell.models import FunnelStats, Task
from maxwell.proxy import PruningProxy


@pytest.fixture
def proxy() -> PruningProxy:
    stats = FunnelStats()
    return PruningProxy(stats, worker_count=1)


@pytest.fixture
async def client(proxy: PruningProxy):
    """Create an aiohttp test client for the Maxwell API."""
    app = web.Application()
    app.router.add_post("/v1/proxy", MaxwellServer(proxy).handle_proxy)
    app.router.add_get("/healthz", MaxwellServer(proxy).handle_health)
    app.router.add_get("/v1/stats", MaxwellServer(proxy).handle_stats)
    app.router.add_get("/dashboard", MaxwellServer(proxy).handle_dashboard)

    async with TestClient(TestServer(app)) as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, client: TestClient) -> None:
        resp = await client.get("/healthz")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "uptime" in data
        assert "total_requests" in data
        assert "circuit_breaker" in data

    @pytest.mark.asyncio
    async def test_health_circuit_breaker_state(self, client: TestClient, proxy: PruningProxy) -> None:
        resp = await client.get("/healthz")
        data = await resp.json()
        assert data["circuit_breaker"] == "CLOSED"

        proxy.stats.is_circuit_open = True
        resp = await client.get("/healthz")
        data = await resp.json()
        assert data["circuit_breaker"] == "OPEN"


class TestStatsEndpoint:
    @pytest.mark.asyncio
    async def test_stats_returns_correct_structure(self, client: TestClient) -> None:
        resp = await client.get("/v1/stats")
        assert resp.status == 200
        data = await resp.json()
        assert "total_requests" in data
        assert "qps" in data
        assert "pruning_rate" in data
        assert "layers" in data
        assert "oracle" in data
        assert "p2p" in data

    @pytest.mark.asyncio
    async def test_stats_layers_all_present(self, client: TestClient) -> None:
        resp = await client.get("/v1/stats")
        data = await resp.json()
        layers = data["layers"]
        assert "L1_bloom_blocked" in layers
        assert "L2_regex_blocked" in layers
        assert "L3_entropy_blocked" in layers
        assert "L4_oracle_blocked" in layers
        assert "L5_repetition_blocked" in layers


class TestProxyEndpoint:
    @pytest.mark.asyncio
    async def test_post_missing_payload_returns_400(self, client: TestClient) -> None:
        resp = await client.post("/v1/proxy", json={})
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_post_invalid_json_returns_400(self, client: TestClient) -> None:
        resp = await client.post(
            "/v1/proxy",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_post_valid_payload_returns_stream(self, client: TestClient) -> None:
        resp = await client.post(
            "/v1/proxy",
            json={"payload": "Tell me about quantum computing and its applications"},
        )
        assert resp.status == 200
        body = await resp.text()
        # Should contain some response content and end with DONE
        assert "<DONE>" in body

    @pytest.mark.asyncio
    async def test_post_blocked_payload_returns_error(self, client: TestClient, proxy: PruningProxy) -> None:
        proxy.bloom.add("blocked_payload_test")
        resp = await client.post(
            "/v1/proxy",
            json={"payload": "blocked_payload_test"},
        )
        assert resp.status == 200
        body = await resp.text()
        assert "Blocked" in body or "error" in body


class TestDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_returns_html(self, client: TestClient) -> None:
        resp = await client.get("/dashboard")
        assert resp.status == 200
        body = await resp.text()
        assert "Maxwell" in body
        assert "<!DOCTYPE html>" in body
