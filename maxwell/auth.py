"""
maxwell.auth — API Authentication (HMAC-SHA256)

Provides request-level authentication for the Maxwell API:
- HMAC-SHA256 signature verification (anti-tampering)
- Timestamp-based replay prevention (±300s window)
- Per-key identification for multi-tenant rate limiting
- CLI key generation utility

Usage:
    # Generate a key pair
    from maxwell.auth import generate_api_key
    key_id, secret = generate_api_key()

    # Client-side signing
    import hmac, hashlib, time
    ts = str(int(time.time()))
    body = '{"payload": "hello"}'
    sig = hmac.new(secret.encode(), (ts + body).encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-Maxwell-Key": key_id,
        "X-Maxwell-Signature": sig,
        "X-Maxwell-Timestamp": ts,
    }
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any

from aiohttp import web

__all__ = [
    "APIKeyStore",
    "auth_middleware",
    "generate_api_key",
    "verify_hmac",
]

logger = logging.getLogger("maxwell.auth")

# Header names
HEADER_KEY = "X-Maxwell-Key"
HEADER_SIGNATURE = "X-Maxwell-Signature"
HEADER_TIMESTAMP = "X-Maxwell-Timestamp"

# Replay window in seconds
REPLAY_WINDOW = 300


def generate_api_key() -> tuple[str, str]:
    """Generate a (key_id, secret) pair for API access."""
    key_id = "mxk_" + secrets.token_hex(8)
    secret = secrets.token_hex(32)
    return key_id, secret


def verify_hmac(
    secret: str,
    timestamp: str,
    body: str,
    signature: str,
) -> bool:
    """Verify HMAC-SHA256 signature over (timestamp + body)."""
    expected = hmac.new(
        secret.encode("utf-8"),
        (timestamp + body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class APIKeyStore:
    """
    Manages API key → secret mappings.

    Keys can be loaded from:
    - A JSON file: {"keys": {"mxk_abc": "secret123", ...}}
    - Environment variable MAXWELL_API_KEYS: "key1:secret1,key2:secret2"
    - Programmatic insertion via add()

    If no keys are configured, authentication is DISABLED (open access).
    """

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        """Auth is enabled when at least one key is registered."""
        return len(self._keys) > 0

    def add(self, key_id: str, secret: str) -> None:
        self._keys[key_id] = secret

    def get_secret(self, key_id: str) -> str | None:
        return self._keys.get(key_id)

    def load_from_env(self) -> int:
        """
        Load keys from MAXWELL_API_KEYS env var.
        Format: "key_id1:secret1,key_id2:secret2"
        Returns number of keys loaded.
        """
        raw = os.environ.get("MAXWELL_API_KEYS", "")
        if not raw:
            return 0
        count = 0
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                kid, sec = pair.split(":", 1)
                self.add(kid.strip(), sec.strip())
                count += 1
        if count:
            logger.info("Loaded %d API key(s) from environment", count)
        return count

    def load_from_file(self, path: str) -> int:
        """
        Load keys from a JSON file.
        Format: {"keys": {"key_id": "secret", ...}}
        Returns number of keys loaded.
        """
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r") as f:
                data = json.load(f)
            keys = data.get("keys", {})
            for kid, sec in keys.items():
                self.add(kid, sec)
            if keys:
                logger.info("Loaded %d API key(s) from %s", len(keys), path)
            return len(keys)
        except Exception as exc:
            logger.error("Failed to load API keys from %s: %s", path, exc)
            return 0

    def __len__(self) -> int:
        return len(self._keys)


# Paths that do NOT require authentication
PUBLIC_PATHS = frozenset({
    "/healthz",
    "/dashboard",
    "/ledger",
})

# Path prefixes that are public
PUBLIC_PREFIXES = ("/balances/", "/ws/")


@web.middleware
async def auth_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    """
    aiohttp middleware for HMAC-SHA256 request authentication.

    Skips authentication for public paths and when no keys are configured.
    """
    store: APIKeyStore | None = request.app.get("api_key_store")

    # If no store or no keys configured, pass through (open mode)
    if not store or not store.enabled:
        return await handler(request)

    # Public endpoints bypass auth
    if request.path in PUBLIC_PATHS or any(request.path.startswith(p) for p in PUBLIC_PREFIXES):
        return await handler(request)

    # Extract headers
    key_id = request.headers.get(HEADER_KEY, "")
    signature = request.headers.get(HEADER_SIGNATURE, "")
    timestamp = request.headers.get(HEADER_TIMESTAMP, "")

    if not key_id or not signature or not timestamp:
        return web.json_response(
            {"error": "Missing authentication headers"},
            status=401,
        )

    # Lookup key
    secret = store.get_secret(key_id)
    if secret is None:
        return web.json_response(
            {"error": "Invalid API key"},
            status=401,
        )

    # Replay window check
    try:
        ts = int(timestamp)
    except ValueError:
        return web.json_response(
            {"error": "Invalid timestamp format"},
            status=401,
        )

    now = int(time.time())
    if abs(now - ts) > REPLAY_WINDOW:
        return web.json_response(
            {"error": "Request timestamp expired"},
            status=401,
        )

    # Read body for signature verification
    body = await request.text()

    # Verify HMAC
    if not verify_hmac(secret, timestamp, body, signature):
        return web.json_response(
            {"error": "Invalid signature"},
            status=401,
        )

    # Attach authenticated key_id to request for downstream use
    request["authenticated_key"] = key_id

    return await handler(request)
