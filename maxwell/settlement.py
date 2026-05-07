"""
maxwell.settlement — L1/L2 Settlement Node (Web3 Relayer)

This module implements a lightweight FastAPI server that acts as a blockchain relayer.
It accepts TEE and State Channel signatures and submits them to an EVM-compatible chain
(e.g., Ethereum, Arbitrum) using web3.py.
"""

import os
from contextlib import asynccontextmanager
import logging
from typing import Dict, Any, AsyncGenerator
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from web3 import Web3
from eth_account import Account

from maxwell.crypto import TEESimulator, TEEQuote

logger = logging.getLogger("maxwell.settlement")

class SettleRequest(BaseModel):
    task_id: int
    provider_address: str
    consumer_address: str
    flops_actual: int
    signature_hex: str
    mrenclave: str
    certificate_chain: list[str]
    price_per_petaflop: float
    is_state_channel: bool = True  # If True, signature is from Consumer. If False, from TEE.

# Minimal ABI for MaxwellSettlement contract
SETTLEMENT_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "taskId", "type": "uint256"},
            {"internalType": "uint256", "name": "flopsActual", "type": "uint256"},
            {"internalType": "bytes", "name": "consumerSignature", "type": "bytes"}
        ],
        "name": "settleStateChannel",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "taskId", "type": "uint256"},
            {"internalType": "uint256", "name": "flopsActual", "type": "uint256"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"}
        ],
        "name": "reportExecution",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

class Web3Relayer:
    def __init__(self) -> None:
        rpc_url = os.environ.get("WEB3_RPC_URL", "http://127.0.0.1:8545")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        # ExtraDataToPOAMiddleware or geth_poa_middleware omitted for compatibility
        
        # Load relayer account to pay for gas
        pk = os.environ.get("RELAYER_PRIVATE_KEY")
        if pk:
            self.account = Account.from_key(pk)
        else:
            # Mock account for dev if not provided
            self.account = Account.create()
            
        contract_addr = os.environ.get("CONTRACT_ADDRESS")
        if not contract_addr:
            # Dummy address for dev
            contract_addr = "0x0000000000000000000000000000000000000000"
            
        self.contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(contract_addr),
            abi=SETTLEMENT_ABI
        )
        
        self.mock_mode = not bool(os.environ.get("WEB3_RPC_URL"))
        if self.mock_mode:
            logger.warning("Running in MOCK mode. No real transactions will be sent.")
            self.mock_settled_tasks: Dict[int, int] = {}
            self.mock_balances: Dict[str, float] = {}

    def get_balance(self, address: str) -> float:
        if self.mock_mode:
            return self.mock_balances.get(address, 1000.0)
        # Not implemented for real chain here since balance is in ERC20
        return 0.0

    def submit_transaction(self, func_call: Any) -> str:
        """Sign and submit transaction to EVM chain"""
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
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            return self.w3.to_hex(tx_hash)
        except Exception as e:
            logger.error(f"Failed to submit transaction: {e}")
            raise HTTPException(status_code=500, detail="Blockchain transaction failed")

relayer = Web3Relayer()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Web3 Relayer Node started.")
    yield
    logger.info("Web3 Relayer Node shutting down.")

app = FastAPI(title="Maxwell Web3 Relayer", lifespan=lifespan)


@app.post("/settle")
async def settle_transaction(req: SettleRequest) -> dict[str, Any]:
    """
    Process a settlement transaction between a consumer and a provider.
    Verifies the TEE signature and transfers funds if valid.
    """
    if req.is_state_channel:
        # State channel path
        if relayer.mock_mode:
            previous_flops = relayer.mock_settled_tasks.get(req.task_id, 0)
            if req.flops_actual <= previous_flops:
                logger.warning(f"State channel nonce {req.flops_actual} <= {previous_flops}")
                raise HTTPException(status_code=400, detail="State channel nonce must be monotonically increasing")
            relayer.mock_settled_tasks[req.task_id] = req.flops_actual
            
        try:
            signature_bytes = bytes.fromhex(req.signature_hex.replace("0x", ""))
        except ValueError:
            signature_bytes = b""
            
        func_call = relayer.contract.functions.settleStateChannel(
            req.task_id,
            req.flops_actual,
            signature_bytes
        )
        tx_hash = relayer.submit_transaction(func_call)
        logger.info(f"Relayed state channel settlement for task {req.task_id}, tx: {tx_hash}")
        
    else:
        # Full TEE execution path
        # Verify the signature cryptographically before relaying
        quote = TEEQuote(
            public_key=req.provider_address,
            flops_actual=req.flops_actual,
            signature_hex=req.signature_hex,
            mrenclave=req.mrenclave,
            certificate_chain=req.certificate_chain
        )
        is_valid = TEESimulator.verify_execution(quote, req.task_id)
        
        if not is_valid:
            logger.warning(f"Invalid TEE signature from provider {req.provider_address} for task {req.task_id}")
            raise HTTPException(status_code=400, detail="Invalid TEE signature")
            
        try:
            signature_bytes = bytes.fromhex(req.signature_hex.replace("0x", ""))
        except ValueError:
            signature_bytes = b""
            
        func_call = relayer.contract.functions.reportExecution(
            req.task_id,
            req.flops_actual,
            signature_bytes
        )
        tx_hash = relayer.submit_transaction(func_call)
        logger.info(f"Relayed TEE execution report for task {req.task_id}, tx: {tx_hash}")
        
    # Calculate approximate cost for info
    petaflops = req.flops_actual / 1e15
    cost = petaflops * req.price_per_petaflop
    
    return {
        "status": "success",
        "task_id": req.task_id,
        "cost_estimated": cost,
        "tx_hash": tx_hash if not relayer.mock_mode else "0xmock"
    }

@app.get("/balances/{address}")
async def get_balance(address: str) -> dict[str, Any]:
    balance = relayer.get_balance(address)
    return {"address": address, "balance": balance}

@app.get("/ledger")
async def get_ledger() -> dict[str, Any]:
    if relayer.mock_mode:
        return {"balances": relayer.mock_balances}
    return {"balances": {}}
