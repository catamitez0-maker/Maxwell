"""
maxwell.p2p — Lightweight P2P Discovery and Routing

Implements a Kademlia DHT-based node discovery mechanism for the global network.
Allows Consumer nodes to find Provider nodes automatically across WANs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, cast

from kademlia.network import Server
import socket

logger = logging.getLogger("maxwell.p2p")

INDEX_KEY = "maxwell:providers_index"
DHT_PORT_BASE = 8468

class ProviderInfo:
    def __init__(self, node_id: str, host: str, port: int, price: float, model: str):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.price = price
        self.model = model
        self.last_seen: float = time.time()
        self.reputation: float = 100.0  # Reputation score [0-100]

class P2PManager:
    def __init__(self, node_id: str, role: str, api_port: int, price: float = 1.0, model: str = "7B", bootstrap_node: str = "", public_ip: str = "127.0.0.1"):
        self.node_id = node_id
        self.role = role
        self.api_port = api_port
        self.price = price
        self.model = model
        self.bootstrap_node = bootstrap_node
        self.public_ip = public_ip
        self.providers: dict[str, ProviderInfo] = {}
        self._running = False
        self._announce_task: asyncio.Task[None] | None = None
        self._discover_task: asyncio.Task[None] | None = None
        self.dht_server = Server()
        
    def _get_available_port(self, start_port: int) -> int:
        for port in range(start_port, start_port + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.bind(('', port))
                    return port
            except OSError:
                continue
        return start_port

    async def start(self) -> None:
        self._running = True
        
        # Start DHT Server
        dht_port = self._get_available_port(DHT_PORT_BASE)
        await self.dht_server.listen(dht_port)
        logger.info("Kademlia DHT Node '%s' listening on UDP %d (Role: %s)", self.node_id, dht_port, self.role)
        
        if self.bootstrap_node:
            try:
                host, port_str = self.bootstrap_node.split(":")
                await self.dht_server.bootstrap([(host, int(port_str))])
                logger.info("Bootstrapped DHT with node %s", self.bootstrap_node)
            except Exception as e:
                logger.warning("Failed to bootstrap with %s: %s", self.bootstrap_node, e)
        
        if self.role == "provider":
            self._announce_task = asyncio.create_task(self._announce_loop())
            
        if self.role == "consumer":
            self._discover_task = asyncio.create_task(self._discover_loop())
            
        asyncio.create_task(self._cleanup_loop())

    async def _announce_loop(self) -> None:
        while self._running:
            # Announce specific node info
            provider_key = f"maxwell:provider:{self.node_id}"
            info = {
                "node_id": self.node_id,
                "host": self.public_ip,
                "api_port": self.api_port,
                "price": self.price,
                "model": self.model
            }
            try:
                await self.dht_server.set(provider_key, json.dumps(info))
                
                # Update global index
                index_data = await self.dht_server.get(INDEX_KEY)
                node_list = []
                if index_data:
                    try:
                        node_list = json.loads(index_data)
                        if not isinstance(node_list, list):
                            node_list = []
                    except Exception:
                        pass
                        
                if self.node_id not in node_list:
                    node_list.append(self.node_id)
                    # Keep index relatively clean
                    if len(node_list) > 100:
                        node_list = node_list[-100:]
                    await self.dht_server.set(INDEX_KEY, json.dumps(node_list))
                
                logger.debug("Provider %s announced to DHT", self.node_id)
            except Exception as e:
                logger.debug("DHT announce failed: %s", e)
                
            await asyncio.sleep(30.0)

    async def _discover_loop(self) -> None:
        while self._running:
            try:
                index_data = await self.dht_server.get(INDEX_KEY)
                if index_data:
                    node_list = json.loads(index_data)
                    if isinstance(node_list, list):
                        for nid in node_list:
                            # Avoid querying ourselves
                            if nid == self.node_id:
                                continue
                            
                            provider_key = f"maxwell:provider:{nid}"
                            p_data = await self.dht_server.get(provider_key)
                            if p_data:
                                try:
                                    p_info = json.loads(p_data)
                                    # Add to providers list
                                    nid = p_info["node_id"]
                                    if nid not in self.providers:
                                        logger.info("DHT Discovered Provider: %s at %s:%s", nid, p_info.get("host"), p_info.get("api_port"))
                                        self.providers[nid] = ProviderInfo(
                                            node_id=nid,
                                            host=p_info.get("host", "127.0.0.1"),
                                            port=p_info.get("api_port", 8080),
                                            price=p_info.get("price", 1.0),
                                            model=p_info.get("model", "7B")
                                        )
                                    else:
                                        p = self.providers[nid]
                                        p.last_seen = time.time()
                                        p.price = p_info.get("price", p.price)
                                except Exception:
                                    pass
            except Exception as e:
                logger.debug("DHT discovery failed: %s", e)
                
            await asyncio.sleep(15.0)

    async def _cleanup_loop(self) -> None:
        while self._running:
            now = time.time()
            stale = [nid for nid, p in self.providers.items() if now - p.last_seen > 120.0]
            for nid in stale:
                logger.info("Provider %s timed out from routing table.", nid)
                del self.providers[nid]
            await asyncio.sleep(30.0)

    def get_best_provider(self) -> ProviderInfo | None:
        valid_providers = [p for p in self.providers.values() if p.reputation > 0]
        if not valid_providers:
            return None
        return max(valid_providers, key=lambda p: p.reputation / max(p.price, 0.0001))

    def report_success(self, node_id: str) -> None:
        p = self.providers.get(node_id)
        if p:
            p.reputation = min(100.0, p.reputation + 2.0)

    def report_failure(self, node_id: str) -> None:
        p = self.providers.get(node_id)
        if p:
            p.reputation = max(0.0, p.reputation - 20.0)
            logger.warning(f"[Slashing] Provider {node_id} slashed. New reputation: {p.reputation:.1f}")

    async def stop(self) -> None:
        self._running = False
        if self._announce_task:
            self._announce_task.cancel()
        if self._discover_task:
            self._discover_task.cancel()
        self.dht_server.stop()
