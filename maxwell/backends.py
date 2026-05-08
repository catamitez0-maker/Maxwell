"""
maxwell.backends — Pluggable LLM inference backend interface.

Defines the Backend protocol and built-in implementations:
- OllamaBackend: Streams from an Ollama-compatible HTTP API
- SimulatedBackend: Generates synthetic tokens for testing

Third parties can implement the Backend protocol to add custom backends
(e.g., vLLM, TGI, OpenAI-compatible).

Usage:
    from maxwell.backends import OllamaBackend, SimulatedBackend

    backend = OllamaBackend(url="http://localhost:11434/api/generate")
    async for token in backend.stream("Hello world", model="llama-7b"):
        print(token, end="")
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Protocol, runtime_checkable

import aiohttp

__all__ = [
    "Backend",
    "OllamaBackend",
    "SimulatedBackend",
    "get_backend",
]

logger = logging.getLogger("maxwell.backends")


@runtime_checkable
class Backend(Protocol):
    """Protocol for LLM inference backends."""

    async def stream(
        self,
        prompt: str,
        model: str,
        **kwargs: object,
    ) -> AsyncGenerator[str, None]:
        """
        Stream generated tokens from the backend.

        Args:
            prompt: Input text to send to the model.
            model: Model identifier (e.g. "llama-7b").

        Yields:
            Individual tokens/words as strings.
        """
        ...  # pragma: no cover


class OllamaBackend:
    """Streams from an Ollama-compatible HTTP API."""

    def __init__(self, url: str) -> None:
        self.url = url

    async def stream(
        self,
        prompt: str,
        model: str,
        **kwargs: object,
    ) -> AsyncGenerator[str, None]:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.url, json=payload) as resp:
                    async for chunk in resp.content.iter_any():
                        text = chunk.decode("utf-8")
                        for line in text.splitlines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                if "response" in data:
                                    yield data["response"]
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            logger.error("Ollama backend error: %s", e)
            yield f"\n\n<Backend connection error: {e}>"


class SimulatedBackend:
    """Generates synthetic tokens for testing / no-backend mode."""

    def __init__(self, delay: float = 0.02) -> None:
        self.delay = delay

    async def stream(
        self,
        prompt: str,
        model: str,
        **kwargs: object,
    ) -> AsyncGenerator[str, None]:
        yield "Here is the response from the simulated Compute Engine:\n"
        words = (prompt * 2).split() + [
            "\nAnd", "here", "are", "more", "output", "tokens",
            "generated", "by", "the", "model.",
        ] * 10
        for word in words:
            await asyncio.sleep(self.delay)
            yield word + " "


def get_backend(backend_type: str, url: str = "") -> Backend:
    """
    Factory function to create a Backend instance.

    Args:
        backend_type: One of "ollama", "simulated".
        url: Backend URL (required for ollama).

    Returns:
        A Backend instance.
    """
    if url and backend_type == "ollama":
        return OllamaBackend(url)
    return SimulatedBackend()
