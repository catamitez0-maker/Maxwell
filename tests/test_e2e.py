"""
End-to-end integration tests for Maxwell Protocol.

Tests the full pipeline: API → Auth → Filter → Execute → Response
using aiohttp test client against the real MaxwellServer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import pytest
from aiohttp import web

from maxwell.api import MaxwellServer
from maxwell.auth import APIKeyStore, generate_api_key
from maxwell.config import MaxwellConfig
from maxwell.client import MaxwellClient, MaxwellAPIError
from maxwell.models import FunnelStats, Task
from maxwell.oracle import MODELS
from maxwell.proxy import PruningProxy
from maxwell.settlement import SettlementHandler, Web3Relayer


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def key_pair():
    """Generate a fresh API key pair."""
    return generate_api_key()


@pytest.fixture
def key_store(key_pair):
    store = APIKeyStore()
    store.add(key_pair[0], key_pair[1])
    return store


@pytest.fixture
def proxy():
    """Create a PruningProxy for testing."""
    stats = FunnelStats()
    model = MODELS["llama-7b"]
    p = PruningProxy(
        stats,
        worker_count=1,
        model=model,
        max_seq_length=8192,
        role="standalone",
    )
    return p


@pytest.fixture
async def e2e_app(proxy, key_store):
    """Create a full Maxwell app with auth + settlement for E2E testing."""
    settlement_handler = SettlementHandler(Web3Relayer())

    app = web.Application()

    server = MaxwellServer(
        proxy,
        api_key_store=key_store,
        settlement_handler=settlement_handler,
    )

    from maxwell.auth import auth_middleware
    app = web.Application(middlewares=[auth_middleware])
    app["api_key_store"] = key_store

    app.router.add_post("/v1/proxy", server.handle_proxy)
    app.router.add_get("/healthz", server.handle_health)
    app.router.add_get("/v1/stats", server.handle_stats)
    settlement_handler.register_routes(app)

    return app


def _sign(key_id: str, secret: str, body: str) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode(), (ts + body).encode(), hashlib.sha256,
    ).hexdigest()
    return {
        "X-Maxwell-Key": key_id,
        "X-Maxwell-Signature": sig,
        "X-Maxwell-Timestamp": ts,
        "Content-Type": "application/json",
    }


# ── Full Pipeline Tests ──────────────────────────────────────────────


class TestFullPipeline:
    """Test the complete API → Filter → Execute → Response pipeline."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, aiohttp_client, e2e_app):
        client = await aiohttp_client(e2e_app)
        resp = await client.get("/healthz")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "uptime" in data
        assert "total_requests" in data

    @pytest.mark.asyncio
    async def test_stats_with_auth(self, aiohttp_client, e2e_app, key_pair):
        client = await aiohttp_client(e2e_app)
        headers = _sign(key_pair[0], key_pair[1], "")
        resp = await client.get("/v1/stats", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert "total_requests" in data
        assert "layers" in data
        assert "oracle" in data

    @pytest.mark.asyncio
    async def test_stats_without_auth_rejected(self, aiohttp_client, e2e_app):
        client = await aiohttp_client(e2e_app)
        resp = await client.get("/v1/stats")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_settlement_with_auth(self, aiohttp_client, e2e_app, key_pair):
        client = await aiohttp_client(e2e_app)
        body = json.dumps({
            "task_id": 1,
            "provider_address": "0xabc",
            "consumer_address": "0xdef",
            "flops_actual": 5000,
            "signature_hex": "0x1234",
            "mrenclave": "hash",
            "certificate_chain": ["cert1"],
            "price_per_petaflop": 0.03,
        })
        headers = _sign(key_pair[0], key_pair[1], body)
        resp = await client.post("/settle", data=body, headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_settlement_without_auth_rejected(self, aiohttp_client, e2e_app):
        client = await aiohttp_client(e2e_app)
        resp = await client.post("/settle", json={"task_id": 1})
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_balance_check(self, aiohttp_client, e2e_app):
        client = await aiohttp_client(e2e_app)
        # balance endpoint is public in current config
        resp = await client.get("/balances/0xtest")
        assert resp.status == 200
        data = await resp.json()
        assert data["balance"] == 1000.0


# ── SDK Client Tests ─────────────────────────────────────────────────


class TestSDKClient:
    """Test the MaxwellClient SDK against a real server."""

    @pytest.mark.asyncio
    async def test_client_health(self, aiohttp_client, e2e_app):
        http_client = await aiohttp_client(e2e_app)
        base_url = str(http_client.make_url(""))

        async with MaxwellClient(base_url) as sdk:
            health = await sdk.health()
            assert health["status"] == "ok"

    @pytest.mark.asyncio
    async def test_client_stats_with_auth(self, aiohttp_client, e2e_app, key_pair):
        http_client = await aiohttp_client(e2e_app)
        base_url = str(http_client.make_url(""))

        async with MaxwellClient(base_url, key_id=key_pair[0], secret=key_pair[1]) as sdk:
            stats = await sdk.stats()
            assert "total_requests" in stats

    @pytest.mark.asyncio
    async def test_client_stats_without_auth_raises(self, aiohttp_client, e2e_app):
        http_client = await aiohttp_client(e2e_app)
        base_url = str(http_client.make_url(""))

        async with MaxwellClient(base_url) as sdk:
            with pytest.raises(MaxwellAPIError) as exc_info:
                await sdk.stats()
            assert exc_info.value.status == 401

    @pytest.mark.asyncio
    async def test_client_balance(self, aiohttp_client, e2e_app):
        http_client = await aiohttp_client(e2e_app)
        base_url = str(http_client.make_url(""))

        async with MaxwellClient(base_url) as sdk:
            result = await sdk.balance("0xtest")
            assert result["balance"] == 1000.0


# ── CLI Init Tests ───────────────────────────────────────────────────


class TestCLIInit:
    """Test the `maxwell init` scaffolding command."""

    def test_init_creates_files(self, tmp_path):
        from maxwell.cli import init
        from typer.testing import CliRunner
        from maxwell.cli import app as maxwell_app

        runner = CliRunner()
        result = runner.invoke(maxwell_app, ["init", str(tmp_path)])

        assert result.exit_code == 0
        assert (tmp_path / "maxwell.toml").exists()
        assert (tmp_path / "rules.json").exists()
        assert (tmp_path / "api_keys.json").exists()
        assert (tmp_path / "logs").is_dir()

    def test_init_creates_valid_toml(self, tmp_path):
        import tomllib
        from typer.testing import CliRunner
        from maxwell.cli import app as maxwell_app

        runner = CliRunner()
        runner.invoke(maxwell_app, ["init", str(tmp_path)])

        with open(tmp_path / "maxwell.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert cfg["server"]["port"] == 8080
        assert cfg["model"]["name"] == "llama-7b"

    def test_init_creates_valid_rules(self, tmp_path):
        from typer.testing import CliRunner
        from maxwell.cli import app as maxwell_app

        runner = CliRunner()
        runner.invoke(maxwell_app, ["init", str(tmp_path)])

        with open(tmp_path / "rules.json") as f:
            rules = json.load(f)
        assert "blacklist" in rules
        assert "patterns" in rules

    def test_init_creates_api_key(self, tmp_path):
        from typer.testing import CliRunner
        from maxwell.cli import app as maxwell_app

        runner = CliRunner()
        runner.invoke(maxwell_app, ["init", str(tmp_path)])

        with open(tmp_path / "api_keys.json") as f:
            keys = json.load(f)
        assert "keys" in keys
        assert len(keys["keys"]) == 1
        key_id = list(keys["keys"].keys())[0]
        assert key_id.startswith("mxk_")

    def test_init_idempotent(self, tmp_path):
        """Running init twice shouldn't overwrite existing files."""
        from typer.testing import CliRunner
        from maxwell.cli import app as maxwell_app

        runner = CliRunner()
        runner.invoke(maxwell_app, ["init", str(tmp_path)])

        # Get first key
        with open(tmp_path / "api_keys.json") as f:
            first_keys = json.load(f)

        runner.invoke(maxwell_app, ["init", str(tmp_path)])

        # Config files shouldn't be overwritten
        assert (tmp_path / "maxwell.toml").exists()
        # But a second API key should be added
        with open(tmp_path / "api_keys.json") as f:
            second_keys = json.load(f)
        assert len(second_keys["keys"]) == 2


class TestCLIKeygen:
    def test_keygen_outputs_key(self):
        from typer.testing import CliRunner
        from maxwell.cli import app as maxwell_app

        runner = CliRunner()
        result = runner.invoke(maxwell_app, ["keygen"])
        assert result.exit_code == 0
        assert "mxk_" in result.output
        assert "Key ID:" in result.output
        assert "Secret:" in result.output
