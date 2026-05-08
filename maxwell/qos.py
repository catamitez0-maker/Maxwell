"""
maxwell.qos — Per-client rate limiting via token bucket algorithm.

Provides async-safe QoS enforcement with LRU eviction for multi-tenant
environments where each client_id gets its own rate-limit bucket.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

__all__ = ["TokenBucket", "ClientBucketManager"]


class TokenBucket:
    """Async-safe token bucket for per-client rate limiting."""

    __slots__ = ("capacity", "fill_rate", "tokens", "last_update", "last_access", "_lock")

    def __init__(self, capacity: float, fill_rate: float) -> None:
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
        self.last_update = time.time()
        self.last_access = time.time()
        self._lock = asyncio.Lock()

    async def consume(self, tokens: float = 1.0) -> bool:
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.last_update = now
            self.last_access = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


class ClientBucketManager:
    """LRU-ordered collection of per-client TokenBuckets with size cap."""

    def __init__(
        self,
        capacity: float = 5.0,
        fill_rate: float = 1.0,
        max_buckets: int = 10_000,
    ) -> None:
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.max_buckets = max_buckets
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()

    async def consume(self, client_id: str, tokens: float = 1.0) -> bool:
        """Get or create bucket for client_id, then consume tokens."""
        if client_id not in self._buckets:
            while len(self._buckets) >= self.max_buckets:
                self._buckets.popitem(last=False)
            self._buckets[client_id] = TokenBucket(self.capacity, self.fill_rate)
        else:
            self._buckets.move_to_end(client_id)
        return await self._buckets[client_id].consume(tokens)
