"""
Tests for maxwell.settlement — Web3 relayer and settlement logic.
"""

import pytest
from unittest.mock import MagicMock

from maxwell.crypto import TEESimulator, TEEQuote
from maxwell.settlement import Web3Relayer, SettleRequest, SettlementHandler


class TestSettleRequest:
    def test_from_dict_valid(self) -> None:
        data = {
            "task_id": 1,
            "provider_address": "0xabc",
            "consumer_address": "0xdef",
            "flops_actual": 5000,
            "signature_hex": "0x1234",
            "mrenclave": "hash",
            "certificate_chain": ["cert1"],
            "price_per_petaflop": 0.03,
        }
        req = SettleRequest.from_dict(data)
        assert req.task_id == 1
        assert req.is_state_channel is True  # default

    def test_from_dict_missing_field(self) -> None:
        with pytest.raises(ValueError, match="Missing required fields"):
            SettleRequest.from_dict({"task_id": 1})


class TestWeb3RelayerInit:
    def test_mock_mode_default(self) -> None:
        """Web3Relayer should initialize in mock mode without env vars."""
        relayer = Web3Relayer()
        assert relayer.mock_mode is True

    def test_mock_mode_has_mock_ledgers(self) -> None:
        relayer = Web3Relayer()
        assert isinstance(relayer.mock_settled_tasks, dict)
        assert isinstance(relayer.mock_balances, dict)

    def test_mock_get_balance_default(self) -> None:
        relayer = Web3Relayer()
        assert relayer.get_balance("0xabc") == 1000.0

    def test_mock_submit_transaction(self) -> None:
        relayer = Web3Relayer()
        result = relayer.submit_transaction(MagicMock())
        assert result == "0xmocktxhash"


class TestSettleMethod:
    def test_state_channel_settle(self) -> None:
        relayer = Web3Relayer()
        req = SettleRequest(
            task_id=1,
            provider_address="0xabc",
            consumer_address="0xdef",
            flops_actual=5000,
            signature_hex="0x1234",
            mrenclave="hash",
            certificate_chain=["cert1"],
            price_per_petaflop=0.03,
            is_state_channel=True,
        )
        result = relayer.settle(req)
        assert result["status"] == "success"
        assert result["task_id"] == 1

    def test_state_channel_nonce_enforcement(self) -> None:
        relayer = Web3Relayer()
        req1 = SettleRequest(
            task_id=42, provider_address="0x", consumer_address="0x",
            flops_actual=1000, signature_hex="0x", mrenclave="h",
            certificate_chain=[], price_per_petaflop=0.01,
        )
        relayer.settle(req1)
        # Second settle with same or lower nonce should fail
        req2 = SettleRequest(
            task_id=42, provider_address="0x", consumer_address="0x",
            flops_actual=500, signature_hex="0x", mrenclave="h",
            certificate_chain=[], price_per_petaflop=0.01,
        )
        with pytest.raises(ValueError, match="monotonically increasing"):
            relayer.settle(req2)


class TestTEEVerification:
    def test_valid_tee_signature_passes(self) -> None:
        tee = TEESimulator()
        quote = tee.sign_execution(task_id=100, flops_actual=5000)
        assert TEESimulator.verify_execution(quote, task_id=100) is True

    def test_wrong_task_id_fails(self) -> None:
        tee = TEESimulator()
        quote = tee.sign_execution(task_id=100, flops_actual=5000)
        assert TEESimulator.verify_execution(quote, task_id=999) is False

    def test_tampered_flops_fails(self) -> None:
        tee = TEESimulator()
        quote = tee.sign_execution(task_id=100, flops_actual=5000)
        tampered = quote._replace(flops_actual=9999)
        assert TEESimulator.verify_execution(tampered, task_id=100) is False

    def test_tampered_mrenclave_fails(self) -> None:
        tee = TEESimulator()
        quote = tee.sign_execution(task_id=100, flops_actual=5000)
        tampered = quote._replace(mrenclave="tampered_hash")
        assert TEESimulator.verify_execution(tampered, task_id=100) is False

    def test_signature_deterministic_per_input(self) -> None:
        tee = TEESimulator()
        q1 = tee.sign_execution(task_id=100, flops_actual=5000)
        q2 = tee.sign_execution(task_id=100, flops_actual=5000)
        assert q1.signature_hex == q2.signature_hex


class TestSettlementHandler:
    @pytest.mark.asyncio
    async def test_settle_endpoint(self, aiohttp_client) -> None:
        from aiohttp import web
        handler = SettlementHandler(Web3Relayer())
        app = web.Application()
        handler.register_routes(app)
        client = await aiohttp_client(app)

        resp = await client.post("/settle", json={
            "task_id": 1,
            "provider_address": "0xabc",
            "consumer_address": "0xdef",
            "flops_actual": 5000,
            "signature_hex": "0x1234",
            "mrenclave": "hash",
            "certificate_chain": ["cert1"],
            "price_per_petaflop": 0.03,
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_balance_endpoint(self, aiohttp_client) -> None:
        from aiohttp import web
        handler = SettlementHandler(Web3Relayer())
        app = web.Application()
        handler.register_routes(app)
        client = await aiohttp_client(app)

        resp = await client.get("/balances/0xtest")
        assert resp.status == 200
        data = await resp.json()
        assert data["balance"] == 1000.0

    @pytest.mark.asyncio
    async def test_ledger_endpoint(self, aiohttp_client) -> None:
        from aiohttp import web
        handler = SettlementHandler(Web3Relayer())
        app = web.Application()
        handler.register_routes(app)
        client = await aiohttp_client(app)

        resp = await client.get("/ledger")
        assert resp.status == 200
        data = await resp.json()
        assert "balances" in data
