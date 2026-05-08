"""
maxwell.settlement — L1/L2 Settlement Node (Web3 Relayer)

Handles blockchain settlement by accepting TEE / State Channel signatures
and submitting them to an EVM-compatible chain via web3.py.

No longer depends on FastAPI — HTTP handlers are provided as aiohttp-
compatible methods via SettlementHandler, registered by MaxwellServer
when role=settlement.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from web3 import Web3
from eth_account import Account

from .crypto import TEESimulator, TEEQuote

__all__ = ["Web3Relayer", "SettlementHandler", "SettleRequest"]

logger = logging.getLogger("maxwell.settlement")


# ── Request model (replaces pydantic BaseModel) ─────────────────────


@dataclass
class SettleRequest:
    """Settlement request payload."""
    task_id: int
    provider_address: str
    consumer_address: str
    flops_actual: int
    signature_hex: str
    mrenclave: str
    certificate_chain: list[str]
    price_per_petaflop: float
    is_state_channel: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SettleRequest":
        """Parse and validate from a JSON dict. Raises ValueError on missing fields."""
        required = [
            "task_id", "provider_address", "consumer_address",
            "flops_actual", "signature_hex", "mrenclave",
            "certificate_chain", "price_per_petaflop",
        ]
        missing = [f for f in required if f not in data]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
        return cls(
            task_id=int(data["task_id"]),
            provider_address=str(data["provider_address"]),
            consumer_address=str(data["consumer_address"]),
            flops_actual=int(data["flops_actual"]),
            signature_hex=str(data["signature_hex"]),
            mrenclave=str(data["mrenclave"]),
            certificate_chain=list(data["certificate_chain"]),
            price_per_petaflop=float(data["price_per_petaflop"]),
            is_state_channel=bool(data.get("is_state_channel", True)),
        )


# ── Minimal ABI for MaxwellSettlement contract ──────────────────────

SETTLEMENT_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "taskId", "type": "uint256"},
            {"internalType": "uint256", "name": "flopsActual", "type": "uint256"},
            {"internalType": "bytes", "name": "consumerSignature", "type": "bytes"},
        ],
        "name": "settleStateChannel",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "taskId", "type": "uint256"},
            {"internalType": "uint256", "name": "flopsActual", "type": "uint256"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"},
        ],
        "name": "reportExecution",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


# ── Web3 Relayer (pure business logic, no HTTP framework) ───────────


class Web3Relayer:
    """Blockchain relayer that signs and submits settlement transactions."""

    def __init__(self) -> None:
        rpc_url = os.environ.get("WEB3_RPC_URL", "http://127.0.0.1:8545")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))

        # Load relayer account to pay for gas
        pk = os.environ.get("RELAYER_PRIVATE_KEY")
        if pk:
            self.account = Account.from_key(pk)
        else:
            self.account = Account.create()

        contract_addr = os.environ.get("CONTRACT_ADDRESS")
        if not contract_addr:
            contract_addr = "0x0000000000000000000000000000000000000000"

        self.contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(contract_addr),
            abi=SETTLEMENT_ABI,
        )

        self.mock_mode = not bool(os.environ.get("WEB3_RPC_URL"))
        if self.mock_mode:
            logger.warning("Running in MOCK mode. No real transactions will be sent.")
            self.mock_settled_tasks: dict[int, int] = {}
            self.mock_balances: dict[str, float] = {}

    def get_balance(self, address: str) -> float:
        if self.mock_mode:
            return self.mock_balances.get(address, 1000.0)
        return 0.0

    def submit_transaction(self, func_call: Any) -> str:
        """Sign and submit transaction to EVM chain."""
        if self.mock_mode:
            return "0xmocktxhash"

        try:
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            tx = func_call.build_transaction({
                "chainId": self.w3.eth.chain_id,
                "gas": 300000,
                "gasPrice": self.w3.eth.gas_price,
                "nonce": nonce,
            })
            signed_tx = self.w3.eth.account.sign_transaction(
                tx, private_key=self.account.key,
            )
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            return self.w3.to_hex(tx_hash)
        except Exception as e:
            logger.error("Failed to submit transaction: %s", e)
            raise RuntimeError("Blockchain transaction failed") from e

    def settle(self, req: SettleRequest) -> dict[str, Any]:
        """
        Process a settlement. Returns result dict or raises on error.
        """
        tx_hash: str = ""

        if req.is_state_channel:
            # State channel path — monotonic nonce enforcement
            if self.mock_mode:
                previous_flops = self.mock_settled_tasks.get(req.task_id, 0)
                if req.flops_actual <= previous_flops:
                    logger.warning(
                        "State channel nonce %d <= %d",
                        req.flops_actual, previous_flops,
                    )
                    raise ValueError(
                        "State channel nonce must be monotonically increasing"
                    )
                self.mock_settled_tasks[req.task_id] = req.flops_actual

            try:
                signature_bytes = bytes.fromhex(
                    req.signature_hex.replace("0x", ""),
                )
            except ValueError:
                signature_bytes = b""

            func_call = self.contract.functions.settleStateChannel(
                req.task_id, req.flops_actual, signature_bytes,
            )
            tx_hash = self.submit_transaction(func_call)
            logger.info(
                "Relayed state channel settlement for task %d, tx: %s",
                req.task_id, tx_hash,
            )

        else:
            # Full TEE execution path — verify signature first
            quote = TEEQuote(
                public_key=req.provider_address,
                flops_actual=req.flops_actual,
                signature_hex=req.signature_hex,
                mrenclave=req.mrenclave,
                certificate_chain=req.certificate_chain,
            )
            if not TEESimulator.verify_execution(quote, req.task_id):
                logger.warning(
                    "Invalid TEE signature from provider %s for task %d",
                    req.provider_address, req.task_id,
                )
                raise ValueError("Invalid TEE signature")

            try:
                signature_bytes = bytes.fromhex(
                    req.signature_hex.replace("0x", ""),
                )
            except ValueError:
                signature_bytes = b""

            func_call = self.contract.functions.reportExecution(
                req.task_id, req.flops_actual, signature_bytes,
            )
            tx_hash = self.submit_transaction(func_call)
            logger.info(
                "Relayed TEE execution report for task %d, tx: %s",
                req.task_id, tx_hash,
            )

        petaflops = req.flops_actual / 1e15
        cost = petaflops * req.price_per_petaflop

        return {
            "status": "success",
            "task_id": req.task_id,
            "cost_estimated": cost,
            "tx_hash": tx_hash if not self.mock_mode else "0xmock",
        }


# ── aiohttp Handlers ────────────────────────────────────────────────


class SettlementHandler:
    """aiohttp request handlers for settlement endpoints."""

    def __init__(self, relayer: Web3Relayer | None = None) -> None:
        self.relayer = relayer or Web3Relayer()

    async def handle_settle(self, request: web.Request) -> web.Response:
        """POST /settle — process a settlement transaction."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"}, status=400,
            )

        try:
            req = SettleRequest.from_dict(data)
        except ValueError as e:
            return web.json_response(
                {"error": str(e)}, status=400,
            )

        try:
            result = self.relayer.settle(req)
            return web.json_response(result)
        except ValueError as e:
            return web.json_response(
                {"error": str(e)}, status=400,
            )
        except RuntimeError as e:
            return web.json_response(
                {"error": str(e)}, status=500,
            )

    async def handle_balance(self, request: web.Request) -> web.Response:
        """GET /balances/{address}"""
        address = request.match_info["address"]
        balance = self.relayer.get_balance(address)
        return web.json_response({"address": address, "balance": balance})

    async def handle_ledger(self, _request: web.Request) -> web.Response:
        """GET /ledger"""
        if self.relayer.mock_mode:
            return web.json_response(
                {"balances": self.relayer.mock_balances},
            )
        return web.json_response({"balances": {}})

    def register_routes(self, app: web.Application) -> None:
        """Register settlement routes on an aiohttp Application."""
        app.router.add_post("/settle", self.handle_settle)
        app.router.add_get("/balances/{address}", self.handle_balance)
        app.router.add_get("/ledger", self.handle_ledger)
