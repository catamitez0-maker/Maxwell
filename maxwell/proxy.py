"""
maxwell-proxy — Heuristic pruning gateway (async funnel engine).

Implements the full 5-layer pruning funnel + oracle metering:
  L1: Bloom Filter (O(1) blacklist)
  L2: Regex pattern matching
  L3: Adaptive Shannon entropy gate
  L4: Oracle FLOPs budget enforcement
  L5: Anti-idle repetition detection
  Circuit Breaker: automatic overload protection
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator

from .crypto import TEESimulator, TEEQuote
from .execution import execute_stream
from .filters import BloomFilter, entropy_gate, regex_gate, repetition_gate
from .models import Decision, FunnelStats, Task
from .oracle import FLOPsExceeded, FLOPsLimiter, TaskBudget, ModelConfig, MODELS
from .p2p import P2PManager
from .qos import ClientBucketManager, TokenBucket
from .routing import route_to_provider

__all__ = ["PruningProxy", "TokenBucket"]

logger = logging.getLogger("maxwell.proxy")


class PruningProxy:
    """
    Core async proxy engine with full funnel + oracle + circuit breaker.
    """

    def __init__(
        self,
        stats: FunnelStats | None = None,
        worker_count: int = 1,
        model: ModelConfig | None = None,
        max_seq_length: int = 8192,
        role: str = "standalone",
        p2p_manager: P2PManager | None = None,
        tee: TEESimulator | None = None,
        backend_url: str = "",
        backend_type: str = "ollama",
    ) -> None:
        self.role = role
        self.p2p_manager = p2p_manager
        self.tee = tee
        self.backend_url = backend_url
        self.backend_type = backend_type
        self.stats = stats or FunnelStats()
        self.worker_count = max(1, worker_count)
        self.input_queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=1000)
        self.output_queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=1000)
        self.log_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)

        self._shutdown = asyncio.Event()

        # L1: Bloom filter
        self.bloom = BloomFilter(capacity=10_000, fp_rate=0.01)
        # L2: Compiled regex rules
        self.rules: list[re.Pattern[str]] = []
        # L3: Entropy thresholds
        self.entropy_low = 1.0
        self.entropy_high = 4.5
        # L4: Oracle FLOPs limiter
        self.model = model or MODELS["llama-7b"]
        self.oracle_limiter = FLOPsLimiter(
            model=self.model,
            max_seq_length=max_seq_length,
        )

        # Circuit breaker
        self._high_load_start: float | None = None
        self._circuit_break_threshold = 0.9   # load ratio to trigger
        self._circuit_break_duration = 2.0     # seconds before tripping
        self._circuit_recover_threshold = 0.4  # load ratio to recover
        
        # QoS Multi-tenant Token Buckets
        self.bucket_manager = ClientBucketManager(
            capacity=5.0, fill_rate=1.0, max_buckets=10_000,
        )

        self._last_config_mtime: float = 0.0

    @property
    def is_running(self) -> bool:
        return not self._shutdown.is_set()

    def shutdown(self) -> None:
        self._shutdown.set()

    # ── Config hot-reload ──────────────────────────────────────────

    async def reload_rules(self, config_path: str) -> bool:
        try:
            if not await asyncio.to_thread(os.path.exists, config_path):
                return False
            mtime = await asyncio.to_thread(os.path.getmtime, config_path)
            if mtime <= self._last_config_mtime:
                return False

            def _load_json() -> dict[str, Any]:
                with open(config_path, "r") as f:
                    return json.load(f)

            config = await asyncio.to_thread(_load_json)

            new_bloom = BloomFilter(capacity=10_000, fp_rate=0.01)
            blacklist = config.get("blacklist", [])
            for item in blacklist:
                new_bloom.add(item)
            new_rules = [re.compile(r) for r in config.get("regex_rules", [])]

            self.bloom = new_bloom
            self.rules = new_rules
            self._last_config_mtime = mtime
            logger.info(
                "Config reloaded: %d blacklist, %d regex rules",
                len(blacklist), len(new_rules),
            )
            return True
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in %s: %s", config_path, exc)
            return False
        except Exception as exc:
            logger.error("Config reload failed: %s", exc)
            return False

    # ── Circuit Breaker ────────────────────────────────────────────

    def _update_circuit_breaker(self, load: float) -> None:
        """Automatic circuit breaker based on sustained high load."""
        if load > self._circuit_break_threshold:
            if self._high_load_start is None:
                self._high_load_start = time.time()
            elif time.time() - self._high_load_start > self._circuit_break_duration:
                if not self.stats.is_circuit_open:
                    logger.warning("Circuit breaker OPEN — load %.1f%%", load * 100)
                self.stats.is_circuit_open = True
        else:
            self._high_load_start = None
            if load < self._circuit_recover_threshold and self.stats.is_circuit_open:
                logger.info("Circuit breaker CLOSED — load recovered")
                self.stats.is_circuit_open = False

    # ── Unified Filter Pipeline ───────────────────────────────────

    async def _run_filters(self, task: Task, load: float = 0.0) -> str | None:
        """
        Run L1-L5 filters on a task.

        Returns None if the task passes all filters, or a reason string
        if it should be blocked.
        """
        payload = task.payload

        # Circuit Breaker
        if self.stats.is_circuit_open:
            await self.stats.increment("circuit_blocked")
            return "Circuit Breaker Open"

        # L1: Bloom Filter
        if payload in self.bloom:
            await self.stats.increment("bloom_blocked")
            return "L1: Bloom Filter"

        # L2: Regex Rules
        if regex_gate(payload, self.rules):
            await self.stats.increment("regex_blocked")
            return "L2: Regex Rule"

        # L3: Shannon Entropy
        if entropy_gate(payload, self.entropy_low, self.entropy_high, load):
            await self.stats.increment("entropy_blocked")
            return "L3: Entropy"

        # L5: Anti-Idle Repetition
        if repetition_gate(payload):
            await self.stats.increment("repetition_blocked")
            return "L5: Repetition"

        return None

    # ── Streaming Proxy Engine ─────────────────────────────────────

    async def process_stream(self, task: Task, client_msg_queue: asyncio.Queue | None = None) -> AsyncGenerator[str, None]:
        """
        Stream back the result of the proxy.
        Runs L0 QoS + L1-L5 inline. If it passes, delegates to
        execution engine or consumer router.
        """
        await self.stats.increment("active_streams")
        await self.stats.increment("total_requests")
        load = self.stats.active_streams / 100.0
        self.stats.current_load = load
        self._update_circuit_breaker(load)

        try:
            # L0: QoS Multi-tenant Token Bucket Limit
            if not await self.bucket_manager.consume(task.client_id, 1.0):
                await self.stats.increment("qos_blocked")
                await self._record(task, Decision.BLOCKED, "L0: QoS Rate Limited")
                yield '{"error": "Blocked by QoS: Rate Limit Exceeded"}'
                return

            # L1-L5 Unified Filter Pipeline
            block_reason = await self._run_filters(task, load)
            if block_reason is not None:
                await self._record(task, Decision.BLOCKED, block_reason)
                yield json.dumps({"error": f"Blocked by {block_reason}"})
                return

            # P2P Consumer Routing Logic
            if self.role == "consumer":
                if not self.p2p_manager:
                    yield '{"error": "Consumer mode requires P2PManager"}'
                    return
                await self.stats.increment("passed_to_engine")
                await self._record(task, Decision.PASSED, "Routed to Provider")
                async for chunk in route_to_provider(task, self.p2p_manager, self.tee):
                    yield chunk
                return

            # Provider / Standalone: L4 Oracle FLOPs Budget
            try:
                budget = self.oracle_limiter.create_task_budget(task.token_estimate)
                await self.stats.increment("total_flops_estimated", budget.consumed_flops)
            except FLOPsExceeded:
                await self.stats.increment("oracle_blocked")
                await self._record(task, Decision.BLOCKED, "L4: FLOPs Budget Exceeded (Prefill)")
                yield '{"error": "Blocked by L4: Budget Exceeded on Input"}'
                return

            # Passed all filters — execute
            await self.stats.increment("passed_to_engine")
            await self._record(task, Decision.PASSED, "Passed to Execution Engine")

            async for chunk in execute_stream(
                task=task,
                model=self.model,
                budget=budget,
                stats=self.stats,
                tee=self.tee,
                backend_url=self.backend_url,
                backend_type=self.backend_type,
                client_msg_queue=client_msg_queue,
            ):
                yield chunk

        finally:
            await self.stats.decrement("active_streams")
            self.stats.current_load = self.stats.active_streams / 100.0

    # ── Funnel decision engine (Background Queue worker) ────────────

    async def funnel_worker(self, worker_id: int = 0) -> None:
        """
        Full funnel: Circuit Breaker → L1 → L2 → L3 → L5 → L4 → PASS.
        """
        logger.debug("Funnel worker %d started", worker_id)
        while self.is_running:
            try:
                task = await asyncio.wait_for(self.input_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            payload = task.payload
            load = self.output_queue.qsize() / 1000
            self.stats.current_load = load
            self.stats.entropy_low = self.entropy_low
            self.stats.entropy_high = self.entropy_high

            # Update circuit breaker state
            self._update_circuit_breaker(load)

            # Run unified filter pipeline
            block_reason = await self._run_filters(task, load)
            if block_reason is not None:
                await self._record(task, Decision.BLOCKED, block_reason)
            else:
                # L4: Oracle FLOPs Budget
                try:
                    est = self.oracle_limiter.check(task.token_estimate)
                    await self.stats.increment("total_flops_estimated", est.total_flops)
                    await self.stats.increment("passed_to_engine")
                    await self._record(task, Decision.PASSED, f"FLOPs: {est.total_flops:.2e}")
                    await self.output_queue.put(task)
                except FLOPsExceeded:
                    await self.stats.increment("oracle_blocked")
                    await self._record(task, Decision.BLOCKED, "L4: FLOPs Budget Exceeded")

            self.input_queue.task_done()

        logger.debug("Funnel worker %d stopped", worker_id)

    def create_funnel_tasks(self) -> list[asyncio.Task[None]]:
        return [
            asyncio.create_task(
                self.funnel_worker(i), name=f"funnel-worker-{i}",
            )
            for i in range(self.worker_count)
        ]

    # ── Config watcher ─────────────────────────────────────────────

    async def config_watcher(self, config_path: str) -> None:
        while self.is_running:
            await self.reload_rules(config_path)
            await asyncio.sleep(2)

    # ── Structured JSON logger ─────────────────────────────────────

    async def log_worker(self, log_path: str) -> None:
        f = await asyncio.to_thread(open, log_path, "a", buffering=8192)
        try:
            while self.is_running:
                try:
                    entry = await asyncio.wait_for(self.log_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    await asyncio.to_thread(f.flush)
                    continue
                line = json.dumps(entry, ensure_ascii=False) + "\n"
                try:
                    await asyncio.to_thread(f.write, line)
                    if self.log_queue.empty():
                        await asyncio.to_thread(f.flush)
                except OSError as exc:
                    logger.error("Log write failed: %s", exc)
                self.log_queue.task_done()
        finally:
            await asyncio.to_thread(f.close)

    async def _record(self, task: Task, decision: Decision, reason: str = "") -> None:
        entry = {
            "ts": time.time(),
            "task_id": task.id,
            "payload_hash": hashlib.sha256(task.payload.encode()).hexdigest()[:16],
            "payload_len": len(task.payload),
            "decision": decision.value,
            "reason": reason,
            "load": round(self.stats.current_load, 4),
        }
        try:
            self.log_queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass
