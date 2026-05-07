"""
HTTP API server for maxwell-proxy (aiohttp-based).

Endpoints:
  POST /v1/proxy  — submit a task to the pruning funnel
  GET  /healthz   — liveness / readiness probe
  GET  /v1/stats  — detailed funnel statistics
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time

import aiohttp
from collections.abc import Awaitable, Callable

from aiohttp import web

from .models import Task
from .proxy import PruningProxy

__all__ = ["MaxwellServer"]

logger = logging.getLogger("maxwell.api")

_task_counter = itertools.count()


def _next_task_id() -> int:
    return time.time_ns() + next(_task_counter)


@web.middleware
async def cors_middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Maxwell-Signature, Upgrade"

    if isinstance(response, web.HTTPException):
        raise response
    return response


class MaxwellServer:
    """Exposes the pruning funnel as an HTTP API."""

    def __init__(
        self,
        proxy: PruningProxy,
        host: str = "0.0.0.0",
        port: int = 8080,
        queue_timeout: float = 2.0,
    ) -> None:
        self.proxy = proxy
        self.host = host
        self.port = port
        self.queue_timeout = queue_timeout
        self._runner: web.AppRunner | None = None

    async def handle_proxy(self, request: web.Request) -> web.StreamResponse | web.Response:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            
            try:
                msg = await ws.receive_json()
            except Exception:
                await ws.send_json({"error": "invalid JSON body"})
                await ws.close()
                return ws
                
            payload: str = msg.get("payload", "")
            if not payload:
                await ws.send_json({"error": "missing 'payload' field"})
                await ws.close()
                return ws
                
            signature: str = msg.get("signature", "")
            task = Task(id=_next_task_id(), payload=payload, signature=signature)
            client_msg_queue = asyncio.Queue()
            
            async def ws_reader() -> None:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        import json
                        try:
                            await client_msg_queue.put(json.loads(msg.data))
                        except Exception:
                            pass
                            
            reader_task = asyncio.create_task(ws_reader())
            
            try:
                async for chunk in self.proxy.process_stream(task, client_msg_queue):
                    await ws.send_str(chunk)
            except Exception as e:
                logger.error("WebSocket streaming error: %s", e)
                await ws.send_str(f"\n<Error: {e}>")
            finally:
                reader_task.cancel()
            return ws

        else:
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "invalid JSON body"}, status=400)

            payload = data.get("payload", "")
            if not payload:
                return web.json_response({"error": "missing 'payload' field"}, status=400)

            signature = request.headers.get("X-Maxwell-Signature", "")
            task = Task(id=_next_task_id(), payload=payload, signature=signature)

            response = web.StreamResponse()
            response.content_type = "text/plain"
            await response.prepare(request)
            
            try:
                async for chunk in self.proxy.process_stream(task):
                    await response.write(chunk.encode("utf-8"))
            except Exception as e:
                logger.error("Streaming error: %s", e)
                await response.write(f"\n<Error: {e}>".encode("utf-8"))
                
            await response.write(b"\n")
            return response

    async def handle_health(self, _request: web.Request) -> web.Response:
        stats = self.proxy.stats
        return web.json_response({
            "status": "ok",
            "uptime": round(stats.uptime, 1),
            "total_requests": stats.total_requests,
            "pruning_rate": round(stats.pruning_rate, 2),
            "circuit_breaker": "OPEN" if stats.is_circuit_open else "CLOSED",
        })

    async def handle_stats(self, _request: web.Request) -> web.Response:
        stats = self.proxy.stats
        return web.json_response({
            "total_requests": stats.total_requests,
            "qps": round(stats.qps, 2),
            "pruning_rate": round(stats.pruning_rate, 2),
            "layers": {
                "L1_bloom_blocked": stats.bloom_blocked,
                "L2_regex_blocked": stats.regex_blocked,
                "L3_entropy_blocked": stats.entropy_blocked,
                "L4_oracle_blocked": stats.oracle_blocked,
                "L5_repetition_blocked": stats.repetition_blocked,
                "circuit_blocked": stats.circuit_blocked,
            },
            "oracle": {
                "total_flops_metered": stats.total_flops_estimated,
                "flops_display": stats.flops_display,
                "model_name": self.proxy.model.name,
                "active_params": self.proxy.model.active_params,
            },
            "passed_to_engine": stats.passed_to_engine,
            "current_load": round(stats.current_load, 4),
            "circuit_breaker": "OPEN" if stats.is_circuit_open else "CLOSED",
            "entropy_thresholds": {
                "low": stats.entropy_low,
                "high": stats.entropy_high,
            },
            "uptime": round(stats.uptime, 1),
            "p2p": {
                "role": self.proxy.role,
                "providers_count": len(self.proxy.p2p_manager.protocol.providers) if self.proxy.p2p_manager else 0,
            }
        })

    async def handle_dashboard(self, _request: web.Request) -> web.Response:
        import os
        html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
        if not os.path.exists(html_path):
            return web.Response(text="Dashboard HTML not found", status=404)
        with open(html_path, "r") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")

    async def start(self) -> None:
        app = web.Application(middlewares=[cors_middleware])

        async def handle_options(_request: web.Request) -> web.Response:
            return web.Response()

        app.router.add_options("/v1/proxy", handle_options)
        app.router.add_post("/v1/proxy", self.handle_proxy)

        app.router.add_options("/healthz", handle_options)
        app.router.add_get("/healthz", self.handle_health)

        app.router.add_options("/v1/stats", handle_options)
        app.router.add_get("/v1/stats", self.handle_stats)

        app.router.add_options("/dashboard", handle_options)
        app.router.add_get("/dashboard", self.handle_dashboard)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("Maxwell API started on %s:%d", self.host, self.port)
