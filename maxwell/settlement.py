"""
maxwell.settlement — Local Settlement Node (Virtual Blockchain Ledger)

This module implements a lightweight FastAPI server that acts as a mock blockchain.
It verifies TEE signatures and manages the virtual balances of consumers and providers.
"""

from contextlib import asynccontextmanager
import logging
from typing import Dict, Any, AsyncGenerator
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException

from maxwell.crypto import TEESimulator

logger = logging.getLogger("maxwell.settlement")

class SettleRequest(BaseModel):
    task_id: int
    provider_address: str
    consumer_address: str
    flops_actual: int
    signature_hex: str
    price_per_petaflop: float

class LedgerState:
    def __init__(self) -> None:
        # Mapping from address to balance (in some virtual token, e.g., Maxwell Tokens - MXT)
        self.balances: Dict[str, float] = {}
        # We start everyone with 1000 MXT
        self.INITIAL_BALANCE = 1000.0

    def get_balance(self, address: str) -> float:
        if address not in self.balances:
            self.balances[address] = self.INITIAL_BALANCE
        return self.balances[address]

    def transfer(self, from_address: str, to_address: str, amount: float) -> bool:
        if self.get_balance(from_address) < amount:
            return False
        self.balances[from_address] -= amount
        
        # Ensure destination exists
        self.get_balance(to_address)
        self.balances[to_address] += amount
        return True


# Global ledger
ledger = LedgerState()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Local Settlement Node started.")
    yield
    logger.info("Local Settlement Node shutting down.")

app = FastAPI(title="Maxwell Settlement Node", lifespan=lifespan)


@app.post("/settle")
async def settle_transaction(req: SettleRequest) -> dict[str, Any]:
    """
    Process a settlement transaction between a consumer and a provider.
    Verifies the TEE signature and transfers funds if valid.
    """
    # 1. Verify the signature cryptographically
    is_valid = TEESimulator.verify_execution(
        public_key=req.provider_address,
        task_id=req.task_id,
        flops_actual=req.flops_actual,
        signature_hex=req.signature_hex
    )
    
    if not is_valid:
        logger.warning(f"Invalid TEE signature from provider {req.provider_address} for task {req.task_id}")
        raise HTTPException(status_code=400, detail="Invalid TEE signature")
        
    # 2. Calculate the cost
    # req.flops_actual is raw FLOPs. Cost = (FLOPs / 1e15) * price_per_petaflop
    petaflops = req.flops_actual / 1e15
    cost = petaflops * req.price_per_petaflop
    
    # 3. Process transfer
    success = ledger.transfer(req.consumer_address, req.provider_address, cost)
    if not success:
        logger.error(f"Insufficient funds for consumer {req.consumer_address}")
        raise HTTPException(status_code=402, detail="Insufficient funds")
        
    logger.info(f"Settled task {req.task_id}: {cost:.8f} MXT transferred from {req.consumer_address} to {req.provider_address}")
    
    return {
        "status": "success",
        "task_id": req.task_id,
        "cost": cost,
        "provider_balance": ledger.get_balance(req.provider_address),
        "consumer_balance": ledger.get_balance(req.consumer_address)
    }

@app.get("/balances/{address}")
async def get_balance(address: str) -> dict[str, Any]:
    balance = ledger.get_balance(address)
    return {"address": address, "balance": balance}

@app.get("/ledger")
async def get_ledger() -> dict[str, Any]:
    return {"balances": ledger.balances}
