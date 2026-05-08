"""
maxwell.client — Python SDK for Maxwell Protocol API.

Provides a simple client to interact with a Maxwell proxy node,
handling HMAC-SHA256 authentication, streaming responses, and
settlement operations.

Usage (async):
    from maxwell.client import MaxwellClient

    async with MaxwellClient("http://localhost:8080", key_id="mxk_...", secret="...") as client:
        # Streaming
        async for token in client.stream("Explain quantum computing"):
            print(token, end="")

        # Non-streaming
        result = await client.query("What is 2+2?")
        print(result)

        # Health check
        health = await client.health()
        print(health)

Usage (sync wrapper):
    from maxwell.client import MaxwellClient

    client = MaxwellClient("http://localhost:8080")
    result = client.query_sync("Hello world")
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import logging
from typing import Any, AsyncGenerator

import aiohttp

__all__ = ["MaxwellClient"]

logger = logging.getLogger("maxwell.client")


class MaxwellClient:
    """
    Python SDK client for the Maxwell Protocol API.

    Handles HMAC-SHA256 authentication, streaming/non-streaming queries,
    health checks, and settlement operations.

    Args:
        base_url: Maxwell node URL (e.g. "http://localhost:8080").
        key_id: API key identifier (e.g. "mxk_abc123"). None to skip auth.
        secret: API key secret for HMAC signing. Required if key_id is set.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        key_id: str | None = None,
        secret: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.key_id = key_id
        self.secret = secret
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "MaxwellClient":
        self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    def _sign_request(self, body: str) -> dict[str, str]:
        """
        Generate HMAC-SHA256 authentication headers.

        Returns empty dict if no key_id/secret configured (open mode).
        """
        if not self.key_id or not self.secret:
            return {}

        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.secret.encode(),
            (timestamp + body).encode(),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-Maxwell-Key": self.key_id,
            "X-Maxwell-Signature": signature,
            "X-Maxwell-Timestamp": timestamp,
        }

    # ── Query Methods ────────────────────────────────────────────────

    async def query(self, payload: str) -> str:
        """
        Send a query and return the complete response.

        Args:
            payload: Input text to process.

        Returns:
            Complete response text.

        Raises:
            MaxwellAPIError: On non-2xx response.
        """
        session = self._ensure_session()
        body = f'{{"payload": "{payload}"}}'
        headers = {
            "Content-Type": "application/json",
            **self._sign_request(body),
        }

        async with session.post(
            f"{self.base_url}/v1/proxy", data=body, headers=headers,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise MaxwellAPIError(resp.status, error_text)
            return await resp.text()

    async def stream(self, payload: str) -> AsyncGenerator[str, None]:
        """
        Send a query and stream the response token by token.

        Args:
            payload: Input text to process.

        Yields:
            Response chunks as they arrive.

        Raises:
            MaxwellAPIError: On non-2xx response.
        """
        session = self._ensure_session()
        body = f'{{"payload": "{payload}"}}'
        headers = {
            "Content-Type": "application/json",
            **self._sign_request(body),
        }

        async with session.post(
            f"{self.base_url}/v1/proxy", data=body, headers=headers,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise MaxwellAPIError(resp.status, error_text)
            async for chunk in resp.content.iter_any():
                yield chunk.decode("utf-8")

    # ── Info Methods ─────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """
        Check node health (public endpoint, no auth required).

        Returns:
            Health status dict with uptime, request count, etc.
        """
        session = self._ensure_session()
        async with session.get(f"{self.base_url}/healthz") as resp:
            return await resp.json()

    async def stats(self) -> dict[str, Any]:
        """
        Get detailed funnel statistics.

        Returns:
            Stats dict with layer counts, FLOPs, QPS, etc.
        """
        session = self._ensure_session()
        headers = self._sign_request("")
        async with session.get(
            f"{self.base_url}/v1/stats", headers=headers,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise MaxwellAPIError(resp.status, error_text)
            return await resp.json()

    # ── Settlement Methods ───────────────────────────────────────────

    async def settle(
        self,
        task_id: int,
        provider_address: str,
        consumer_address: str,
        flops_actual: int,
        signature_hex: str,
        mrenclave: str,
        certificate_chain: list[str],
        price_per_petaflop: float,
        is_state_channel: bool = True,
    ) -> dict[str, Any]:
        """
        Submit a settlement transaction.

        Returns:
            Settlement result with tx_hash and cost estimate.
        """
        import json
        session = self._ensure_session()
        data = {
            "task_id": task_id,
            "provider_address": provider_address,
            "consumer_address": consumer_address,
            "flops_actual": flops_actual,
            "signature_hex": signature_hex,
            "mrenclave": mrenclave,
            "certificate_chain": certificate_chain,
            "price_per_petaflop": price_per_petaflop,
            "is_state_channel": is_state_channel,
        }
        body = json.dumps(data)
        headers = {
            "Content-Type": "application/json",
            **self._sign_request(body),
        }
        async with session.post(
            f"{self.base_url}/settle", data=body, headers=headers,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise MaxwellAPIError(resp.status, error_text)
            return await resp.json()

    async def balance(self, address: str) -> dict[str, Any]:
        """Check balance for an address."""
        session = self._ensure_session()
        async with session.get(
            f"{self.base_url}/balances/{address}",
        ) as resp:
            return await resp.json()

    # ── Sync Wrappers ────────────────────────────────────────────────

    def query_sync(self, payload: str) -> str:
        """Synchronous wrapper for query(). Creates a temporary event loop."""
        return asyncio.run(self._sync_query(payload))

    async def _sync_query(self, payload: str) -> str:
        async with MaxwellClient(
            self.base_url, self.key_id, self.secret, self.timeout.total or 30.0,
        ) as client:
            return await client.query(payload)

    def health_sync(self) -> dict[str, Any]:
        """Synchronous wrapper for health()."""
        return asyncio.run(self._sync_health())

    async def _sync_health(self) -> dict[str, Any]:
        async with MaxwellClient(
            self.base_url, self.key_id, self.secret, self.timeout.total or 30.0,
        ) as client:
            return await client.health()


class MaxwellAPIError(Exception):
    """Raised when the Maxwell API returns a non-2xx status."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"Maxwell API error {status}: {message}")
