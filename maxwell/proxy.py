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
import json
import logging
import os
import re
import time
from typing import Any

from .filters import BloomFilter, entropy_gate, regex_gate, repetition_gate
from .models import Decision, FunnelStats, Task
from .oracle import FLOPsExceeded, FLOPsLimiter, estimate_flops

__all__ = ["PruningProxy"]

logger = logging.getLogger("maxwell.proxy")


class PruningProxy:
    """
    Core async proxy engine with full funnel + oracle + circuit breaker.
    """

    def __init__(
        self,
        stats: FunnelStats | None = None,
        worker_count: int = 1,
        model_params: int = 7_000_000_000,
        max_seq_length: int = 8192,
    ) -> None:
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
        self.oracle_limiter = FLOPsLimiter(
            model_params=model_params,
            max_seq_length=max_seq_length,
        )
        self.model_params = model_params

        # Circuit breaker
        self._high_load_start: float | None = None
        self._circuit_break_threshold = 0.9   # load ratio to trigger
        self._circuit_break_duration = 2.0     # seconds before tripping
        self._circuit_recover_threshold = 0.4  # load ratio to recover

        self._last_config_mtime: float = 0.0

    @property
    def is_running(self) -> bool:
        return not self._shutdown.is_set()

    def shutdown(self) -> None:
        self._shutdown.set()

    # ── Config hot-reload ──────────────────────────────────────────

    async def reload_rules(self, config_path: str) -> bool:
        try:
            if not os.path.exists(config_path):
                return False
            mtime = os.path.getmtime(config_path)
            if mtime <= self._last_config_mtime:
                return False

            with open(config_path, "r") as f:
                config: dict[str, Any] = json.load(f)

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

    # ── Funnel decision engine ─────────────────────────────────────

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

            # ── Circuit Breaker ──
            if self.stats.is_circuit_open:
                self.stats.circuit_blocked += 1
                await self._record(task, Decision.BLOCKED, "Circuit Breaker Open")

            # ── L1: Bloom Filter ──
            elif payload in self.bloom:
                self.stats.bloom_blocked += 1
                await self._record(task, Decision.BLOCKED, "L1: Bloom Filter")

            # ── L2: Regex Rules ──
            elif regex_gate(payload, self.rules):
                self.stats.regex_blocked += 1
                await self._record(task, Decision.BLOCKED, "L2: Regex Rule")

            # ── L3: Shannon Entropy ──
            elif entropy_gate(payload, self.entropy_low, self.entropy_high, load):
                self.stats.entropy_blocked += 1
                await self._record(task, Decision.BLOCKED, "L3: Entropy")

            # ── L5: Anti-Idle Repetition ──
            elif repetition_gate(payload):
                self.stats.repetition_blocked += 1
                await self._record(task, Decision.BLOCKED, "L5: Repetition (Anti-Idle)")

            # ── L4: Oracle FLOPs Budget ──
            else:
                try:
                    est = self.oracle_limiter.check(task.token_estimate)
                    self.stats.total_flops_estimated += est.total_flops
                    self.stats.passed_to_engine += 1
                    await self._record(task, Decision.PASSED, f"FLOPs: {est.total_flops:.2e}")
                    await self.output_queue.put(task)
                except FLOPsExceeded:
                    self.stats.oracle_blocked += 1
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
        while self.is_running:
            try:
                entry = await asyncio.wait_for(self.log_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            try:
                await asyncio.to_thread(self._write_log_line, log_path, line)
            except OSError as exc:
                logger.error("Log write failed: %s", exc)
            self.log_queue.task_done()

    @staticmethod
    def _write_log_line(path: str, line: str) -> None:
        with open(path, "a") as f:
            f.write(line)

    async def _record(self, task: Task, decision: Decision, reason: str = "") -> None:
        entry = {
            "ts": time.time(),
            "task_id": task.id,
            "payload_preview": task.payload[:50],
            "decision": decision.value,
            "reason": reason,
            "load": round(self.stats.current_load, 4),
        }
        try:
            self.log_queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass
