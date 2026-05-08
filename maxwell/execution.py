"""
maxwell.execution — Provider / Standalone execution engine.

Handles LLM backend streaming via the Backend protocol, per-token FLOPs
metering with budget enforcement, micropayment negotiation, and TEE
attestation signature generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from .backends import Backend, get_backend
from .crypto import TEESimulator, TEEQuote, HAS_WEB3
from .models import FunnelStats, Task
from .oracle import FLOPsExceeded, FLOPsLimiter, TaskBudget, ModelConfig

__all__ = ["execute_stream"]

logger = logging.getLogger("maxwell.execution")


async def _handle_micropayment(
    client_msg_queue: asyncio.Queue,
    actual_tokens: int,
    task: Task,
    tee: TEESimulator | None,
) -> str | None:
    """
    Negotiate a micropayment with the consumer.

    Returns None on success, or an error message string to yield and break.
    """
    try:
        client_msg = await asyncio.wait_for(client_msg_queue.get(), timeout=2.0)
    except asyncio.TimeoutError:
        return "\n\n<TRUNCATED: Payment Timeout! Client did not sign nonce.>"

    if "signature" not in client_msg:
        return "\n\n<TRUNCATED: Invalid micro-payment format!>"

    quote = TEEQuote(
        public_key=client_msg.get("public_key", ""),
        flops_actual=client_msg.get("flops_actual", 0),
        signature_hex=client_msg.get("signature", ""),
        mrenclave=client_msg.get("mrenclave", ""),
        certificate_chain=client_msg.get("certificate_chain", []),
    )

    # Anti-fraud monotonic check
    if quote.flops_actual < actual_tokens:
        return "\n\n<TRUNCATED: Fraud detected! Nonce decreased.>"

    # Verify Consumer's signature
    if tee and HAS_WEB3 and not tee.verify_execution(quote, task.id):
        return "\n\n<TRUNCATED: Invalid micro-payment signature!>"

    return None


async def execute_stream(
    task: Task,
    model: ModelConfig,
    budget: TaskBudget,
    stats: FunnelStats,
    tee: TEESimulator | None = None,
    backend_url: str = "",
    backend_type: str = "ollama",
    client_msg_queue: asyncio.Queue | None = None,
    backend: Backend | None = None,
) -> AsyncGenerator[str, None]:
    """
    Execute an inference task and stream results with FLOPs metering.

    Handles both real LLM backends and simulated mode via the Backend protocol.
    Appends TEE attestation signature at the end if TEE is configured.

    Args:
        backend: Optional pre-configured Backend instance. If not provided,
                 one is created from backend_type/backend_url.
    """
    if backend is None:
        backend = get_backend(backend_type, backend_url)

    actual_tokens_generated = 0

    async for token in backend.stream(task.payload, model.name):
        try:
            budget.consume_tokens(1)
            actual_tokens_generated += 1
            await stats.increment("total_flops_estimated", 2.0 * model.active_params)

            if client_msg_queue and actual_tokens_generated % 10 == 0:
                yield f"<REQUEST_PAYMENT_NONCE:{actual_tokens_generated}>"
                err = await _handle_micropayment(
                    client_msg_queue, actual_tokens_generated, task, tee,
                )
                if err is not None:
                    yield err
                    return

            yield token
        except FLOPsExceeded:
            yield "\n\n<TRUNCATED: FLOPs Budget Exceeded! Generation stopped.>"
            logger.warning("Stream truncated: Budget exceeded for task %d", task.id)
            return

    # TEE Cryptographic Signature Generation
    if tee:
        actual_flops = int(budget.consumed_flops)
        quote = tee.sign_execution(task.id, actual_flops)
        tee_payload = {
            "public_key": quote.public_key,
            "flops_actual": quote.flops_actual,
            "signature": quote.signature_hex,
            "mrenclave": quote.mrenclave,
            "certificate_chain": quote.certificate_chain,
        }
        yield f"\n\n<TEE_SIGNATURE> {json.dumps(tee_payload)}\n"
        logger.info("[TEE Attestation] Task %d signed with %d FLOPs", task.id, actual_flops)

    yield "\n<DONE>"
