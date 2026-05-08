"""
maxwell.routing — Consumer-side P2P routing logic.

Handles Provider selection, WebSocket forwarding, micropayment signing,
and TEE signature verification for remotely executed tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

import aiohttp

from .crypto import TEESimulator, TEEQuote
from .models import Task
from .p2p import P2PManager

__all__ = ["route_to_provider"]

logger = logging.getLogger("maxwell.routing")


async def route_to_provider(
    task: Task,
    p2p_manager: P2PManager,
    tee: TEESimulator | None = None,
) -> AsyncGenerator[str, None]:
    """
    Route a task to the best available Provider via WebSocket.

    Handles:
    - Provider selection (reputation/price weighted)
    - WebSocket streaming relay
    - Micropayment nonce signing
    - TEE signature verification at end of stream
    - Provider reputation updates (success/failure)
    """
    provider = p2p_manager.get_best_provider()
    if not provider:
        yield '{"error": "No available providers on the P2P network"}'
        return

    url = f"ws://{provider.host}:{provider.port}/v1/proxy"
    yield f"[Consumer] Routing task to Provider {provider.node_id} (Price: {provider.price})\n\n"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect(url) as ws:
                await ws.send_json({
                    "payload": task.payload,
                    "token_estimate": task.token_estimate,
                })

                buffer = ""
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        text_chunk = msg.data

                        # Handle micropayment requests from Provider
                        if text_chunk.startswith("<REQUEST_PAYMENT_NONCE:"):
                            nonce_str = (
                                text_chunk.replace("<REQUEST_PAYMENT_NONCE:", "")
                                .replace(">", "")
                                .strip()
                            )
                            try:
                                nonce = int(nonce_str)
                                if tee:
                                    micro_quote = tee.sign_execution(task.id, nonce)
                                    await ws.send_json({
                                        "signature": micro_quote.signature_hex,
                                        "flops_actual": nonce,
                                        "public_key": micro_quote.public_key,
                                        "mrenclave": micro_quote.mrenclave,
                                        "certificate_chain": micro_quote.certificate_chain,
                                    })
                            except ValueError:
                                pass
                            continue

                        buffer += text_chunk

                    # Stream with look-ahead for TEE signature marker
                    marker_idx = buffer.find("<TEE_SIGNATURE>")
                    if marker_idx != -1:
                        if marker_idx > 0:
                            yield buffer[:marker_idx]
                        buffer = buffer[marker_idx:]
                    else:
                        if len(buffer) > 20:
                            yield buffer[:-20]
                            buffer = buffer[-20:]

                # Process remaining buffer
                if buffer:
                    if buffer.startswith("<TEE_SIGNATURE>"):
                        for part in _verify_tee_signature(
                            buffer, task, p2p_manager, provider.node_id,
                        ):
                            yield part
                    else:
                        yield buffer
                        p2p_manager.report_success(provider.node_id)

        except Exception as e:
            p2p_manager.report_failure(provider.node_id)
            yield f"\n\n<Error communicating with Provider: {e}>"


def _verify_tee_signature(
    buffer: str,
    task: Task,
    p2p_manager: P2PManager,
    node_id: str,
) -> list[str]:
    """Parse and verify a TEE signature from the Provider's stream."""
    results: list[str] = []
    try:
        sig_json = (
            buffer.replace("<TEE_SIGNATURE>", "")
            .replace("\n", "")
            .replace("<DONE>", "")
            .strip()
        )
        sig_data = json.loads(sig_json)
        quote = TEEQuote(
            public_key=sig_data["public_key"],
            flops_actual=sig_data["flops_actual"],
            signature_hex=sig_data["signature"],
            mrenclave=sig_data.get("mrenclave", ""),
            certificate_chain=sig_data.get("certificate_chain", []),
        )
        is_valid = TEESimulator.verify_execution(quote, task.id)
        if not is_valid:
            p2p_manager.report_failure(node_id)
            results.append(
                "\n\n[Consumer Warning] Invalid TEE Signature detected! Provider slashed.\n"
            )
        else:
            p2p_manager.report_success(node_id)
            results.append(
                f"\n\n[Consumer Info] TEE Signature Verified OK. FLOPs: {sig_data['flops_actual']}\n"
            )
    except Exception as e:
        p2p_manager.report_failure(node_id)
        results.append(
            f"\n\n[Consumer Warning] Malformed TEE Signature! Provider slashed. ({e})\n"
        )
    return results
