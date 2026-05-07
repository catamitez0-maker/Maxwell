"""
maxwell.p2p — Lightweight P2P Discovery and Routing

Implements a UDP broadcast-based node discovery mechanism for the local network.
Allows Consumer nodes to find Provider nodes automatically without a centralized registry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, cast

logger = logging.getLogger("maxwell.p2p")

BROADCAST_PORT = 18080
BROADCAST_IP = "255.255.255.255"

class ProviderInfo:
    def __init__(self, node_id: str, host: str, port: int, price: float, model: str):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.price = price
        self.model = model
        self.last_seen: float = time.time()
        self.reputation: float = 100.0  # Reputation score [0-100]


class P2PDiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, node_id: str, role: str, api_port: int, price: float = 1.0, model: str = "7B"):
        self.node_id = node_id
        self.role = role
        self.api_port = api_port
        self.price = price
        self.model = model
        self.providers: dict[str, ProviderInfo] = {}
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast(asyncio.DatagramTransport, transport)
        if hasattr(self.transport, "get_extra_info"):
            sock = self.transport.get_extra_info("socket")
            if sock:
                import socket
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                # Allow multiple nodes on the same machine for testing
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except AttributeError:
                    pass

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
            
            # We are a consumer looking for providers
            if self.role == "consumer" and msg.get("type") == "provider_announce":
                node_id = msg["node_id"]
                if node_id not in self.providers:
                    logger.info("Discovered new Provider: %s at %s:%d (Price: %s)", node_id, addr[0], msg["api_port"], msg["price"])
                    self.providers[node_id] = ProviderInfo(
                        node_id=node_id,
                        host=addr[0],
                        port=msg["api_port"],
                        price=msg["price"],
                        model=msg["model"]
                    )
                else:
                    # Update last seen and dynamic attributes
                    p = self.providers[node_id]
                    p.last_seen = time.time()
                    p.price = msg["price"]
                
            # If we are a provider, we could listen for consumers, but for now we just broadcast
        except Exception:
            pass

    def error_received(self, exc: Exception) -> None:
        logger.error("P2P UDP error received: %s", exc)


class P2PManager:
    def __init__(self, node_id: str, role: str, api_port: int, price: float = 1.0, model: str = "7B"):
        self.node_id = node_id
        self.role = role
        self.api_port = api_port
        self.protocol = P2PDiscoveryProtocol(node_id, role, api_port, price, model)
        self._running = False
        self._broadcast_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._running = True
        loop = asyncio.get_running_loop()
        
        # We bind to 0.0.0.0 and listen on BROADCAST_PORT
        # This allows receiving broadcasts.
        try:
            await loop.create_datagram_endpoint(
                lambda: self.protocol,
                local_addr=("0.0.0.0", BROADCAST_PORT),
                allow_broadcast=True,
                reuse_port=True
            )
            logger.info("P2P Node '%s' listening on UDP %d (Role: %s)", self.node_id, BROADCAST_PORT, self.role)
        except OSError as e:
            # Port might be in use if running multiple nodes on same machine.
            # In a real environment, nodes are on different machines.
            # For local testing, we'll just use ephemeral ports for receiving
            # but then we can't easily discover without a registry.
            # We'll allow it to fail gracefully if port is bound, and just broadcast.
            logger.warning("Could not bind UDP %d (usually expected for multiple local nodes): %s", BROADCAST_PORT, e)
            await loop.create_datagram_endpoint(
                lambda: self.protocol,
                local_addr=("0.0.0.0", 0),
                allow_broadcast=True
            )
        
        if self.role == "provider":
            self._broadcast_task = asyncio.create_task(self._announce_loop())
            
        # Cleanup task
        asyncio.create_task(self._cleanup_loop())

    async def _announce_loop(self) -> None:
        while self._running:
            if self.protocol.transport:
                msg = json.dumps({
                    "type": "provider_announce",
                    "node_id": self.protocol.node_id,
                    "api_port": self.protocol.api_port,
                    "price": self.protocol.price,
                    "model": self.protocol.model
                }).encode("utf-8")
                try:
                    self.protocol.transport.sendto(msg, (BROADCAST_IP, BROADCAST_PORT))
                except Exception as e:
                    logger.debug("Failed to broadcast: %s", e)
            await asyncio.sleep(2.0)

    async def _cleanup_loop(self) -> None:
        while self._running:
            now = time.time()
            stale = [nid for nid, p in self.protocol.providers.items() if now - p.last_seen > 10.0]
            for nid in stale:
                logger.info("Provider %s timed out from routing table.", nid)
                del self.protocol.providers[nid]
            await asyncio.sleep(5.0)

    def get_best_provider(self) -> ProviderInfo | None:
        """Find the best provider based on Reputation / Price."""
        valid_providers = [p for p in self.protocol.providers.values() if p.reputation > 0]
        if not valid_providers:
            return None
            
        # Maximize (Reputation / Price). To avoid div by zero, max(price, 0.0001)
        return max(valid_providers, key=lambda p: p.reputation / max(p.price, 0.0001))

    def report_success(self, node_id: str) -> None:
        """Increase reputation for a successful task execution."""
        p = self.protocol.providers.get(node_id)
        if p:
            p.reputation = min(100.0, p.reputation + 2.0)

    def report_failure(self, node_id: str) -> None:
        """Slash reputation for failure or invalid TEE signature."""
        p = self.protocol.providers.get(node_id)
        if p:
            # Massive slashing for failure to ensure malicious nodes are quickly isolated
            p.reputation = max(0.0, p.reputation - 20.0)
            logger.warning(f"[Slashing] Provider {node_id} slashed. New reputation: {p.reputation:.1f}")

    async def stop(self) -> None:
        self._running = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
        if self.protocol.transport:
            self.protocol.transport.close()
