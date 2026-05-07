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
from typing import Any, AsyncGenerator

import aiohttp
from .crypto import TEESimulator, TEEQuote
from .filters import BloomFilter, entropy_gate, regex_gate, repetition_gate
from .models import Decision, FunnelStats, Task
from .oracle import FLOPsExceeded, FLOPsLimiter, TaskBudget, ModelConfig, MODELS
from .p2p import P2PManager

__all__ = ["PruningProxy", "TokenBucket"]

logger = logging.getLogger("maxwell.proxy")


class TokenBucket:
    """Per-client rate limiting via token bucket algorithm."""
    def __init__(self, capacity: float, fill_rate: float):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
        self.last_update = time.time()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.time()
        # Add tokens based on elapsed time
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        self.last_update = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


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
        self.client_buckets: dict[str, TokenBucket] = {}
        # Default bucket: max 5 concurrent requests, refilling 1 request per second
        self.bucket_capacity = 5.0
        self.bucket_fill_rate = 1.0

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

    # ── Streaming Proxy Engine ─────────────────────────────────────

    async def process_stream(self, task: Task, client_msg_queue: asyncio.Queue | None = None) -> AsyncGenerator[str, None]:
        """
        Stream back the result of the proxy.
        Runs L1-L5 inline. If it passes, yields simulated LLM tokens
        and meters FLOPs strictly.
        """
        self.stats.active_streams += 1
        load = self.stats.active_streams / 100.0
        self.stats.current_load = load
        self._update_circuit_breaker(load)

        try:
            payload = task.payload
            
            # L0: QoS Multi-tenant Token Bucket Limit
            bucket = self.client_buckets.setdefault(
                task.client_id, 
                TokenBucket(self.bucket_capacity, self.bucket_fill_rate)
            )
            if not bucket.consume(1.0):
                self.stats.qos_blocked += 1
                await self._record(task, Decision.BLOCKED, "L0: QoS Rate Limited")
                yield '{"error": "Blocked by QoS: Rate Limit Exceeded"}'
                return

            # L4 (Circuit Breaker)
            if self.stats.is_circuit_open:
                self.stats.circuit_blocked += 1
                await self._record(task, Decision.BLOCKED, "Circuit Breaker Open")
                yield '{"error": "service overloaded"}'
                return

            # L1: Bloom Filter
            if payload in self.bloom:
                self.stats.bloom_blocked += 1
                await self._record(task, Decision.BLOCKED, "L1: Bloom Filter")
                yield '{"error": "Blocked by L1: Bloom Filter"}'
                return

            # L2: Regex Rules
            if regex_gate(payload, self.rules):
                self.stats.regex_blocked += 1
                await self._record(task, Decision.BLOCKED, "L2: Regex Rule")
                yield '{"error": "Blocked by L2: Regex Rule"}'
                return

            # L3: Shannon Entropy
            if entropy_gate(payload, self.entropy_low, self.entropy_high, load):
                self.stats.entropy_blocked += 1
                await self._record(task, Decision.BLOCKED, "L3: Entropy")
                yield '{"error": "Blocked by L3: Entropy"}'
                return

            # L5: Anti-Idle Repetition
            if repetition_gate(payload):
                self.stats.repetition_blocked += 1
                await self._record(task, Decision.BLOCKED, "L5: Repetition")
                yield '{"error": "Blocked by L5: Repetition"}'
                return

            # P2P Consumer Routing Logic
            if self.role == "consumer":
                if not self.p2p_manager:
                    yield '{"error": "Consumer mode requires P2PManager"}'
                    return
                provider = self.p2p_manager.get_best_provider()
                if not provider:
                    yield '{"error": "No available providers on the P2P network"}'
                    return
                
                # Forward to Provider via WebSocket
                url = f"ws://{provider.host}:{provider.port}/v1/proxy"
                self.stats.passed_to_engine += 1
                await self._record(task, Decision.PASSED, f"Routed to Provider {provider.node_id}")
                yield f"[Consumer] Routing task to Provider {provider.node_id} (Price: {provider.price})\n\n"
                
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.ws_connect(url) as ws:
                            await ws.send_json({"payload": payload, "token_estimate": task.token_estimate})
                            
                            buffer = ""
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    text_chunk = msg.data
                                    
                                    # Handle micropayment requests
                                    if text_chunk.startswith("<REQUEST_PAYMENT_NONCE:"):
                                        nonce_str = text_chunk.replace("<REQUEST_PAYMENT_NONCE:", "").replace(">", "").strip()
                                        try:
                                            nonce = int(nonce_str)
                                            # Sign the nonce as consumer
                                            if self.tee:
                                                micro_quote = self.tee.sign_execution(task.id, nonce)
                                                await ws.send_json({
                                                    "signature": micro_quote.signature_hex,
                                                    "flops_actual": nonce,
                                                    "public_key": micro_quote.public_key,
                                                    "mrenclave": micro_quote.mrenclave,
                                                    "certificate_chain": micro_quote.certificate_chain
                                                })
                                        except ValueError:
                                            pass
                                        continue
                                        
                                    buffer += text_chunk
                                
                                marker_idx = buffer.find("<TEE_SIGNATURE>")
                                if marker_idx != -1:
                                    if marker_idx > 0:
                                        yield buffer[:marker_idx]
                                    buffer = buffer[marker_idx:]
                                else:
                                    if len(buffer) > 20:
                                        yield buffer[:-20]
                                        buffer = buffer[-20:]
                            
                            if buffer:
                                if buffer.startswith("<TEE_SIGNATURE>"):
                                    try:
                                        sig_json = buffer.replace("<TEE_SIGNATURE>", "").replace("\n", "").replace("<DONE>", "").strip()
                                        sig_data = json.loads(sig_json)
                                        quote = TEEQuote(
                                            public_key=sig_data["public_key"],
                                            flops_actual=sig_data["flops_actual"],
                                            signature_hex=sig_data["signature"],
                                            mrenclave=sig_data.get("mrenclave", ""),
                                            certificate_chain=sig_data.get("certificate_chain", [])
                                        )
                                        is_valid = TEESimulator.verify_execution(quote, task.id)
                                        if not is_valid:
                                            self.p2p_manager.report_failure(provider.node_id)
                                            yield "\n\n[Consumer Warning] Invalid TEE Signature detected! Provider slashed.\n"
                                        else:
                                            self.p2p_manager.report_success(provider.node_id)
                                            yield f"\n\n[Consumer Info] TEE Signature Verified OK. FLOPs: {sig_data['flops_actual']}\n"
                                    except Exception as e:
                                        self.p2p_manager.report_failure(provider.node_id)
                                        yield f"\n\n[Consumer Warning] Malformed TEE Signature! Provider slashed. ({e})\n"
                                else:
                                    yield buffer
                                    self.p2p_manager.report_success(provider.node_id)
                    except Exception as e:
                        self.p2p_manager.report_failure(provider.node_id)
                        yield f"\n\n<Error communicating with Provider: {e}>"
                return

            # Provider / Standalone Execution Logic
            
            # L4: Oracle FLOPs Budget Creation
            try:
                budget = self.oracle_limiter.create_task_budget(task.token_estimate)
                self.stats.total_flops_estimated += budget.consumed_flops
            except FLOPsExceeded:
                self.stats.oracle_blocked += 1
                if self.role == "consumer" and self.p2p_manager and provider:
                    self.p2p_manager.report_failure(provider.node_id)
                await self._record(task, Decision.BLOCKED, "L4: FLOPs Budget Exceeded (Prefill)")
                yield '{"error": "Blocked by L4: Budget Exceeded on Input"}'
                return

            # Passed filters
            self.stats.passed_to_engine += 1
            await self._record(task, Decision.PASSED, "Passed to Execution Engine")
            
            actual_tokens_generated = 0
            
            if self.backend_url:
                # Proxy to actual LLM backend
                async with aiohttp.ClientSession() as session:
                    if self.backend_type == "ollama":
                        payload_data = {
                            "model": self.model.name,
                            "prompt": task.payload,
                            "stream": True
                        }
                        try:
                            async with session.post(self.backend_url, json=payload_data) as resp:
                                async for chunk in resp.content.iter_any():
                                    text = chunk.decode("utf-8")
                                    # Ollama sends NDJSON lines
                                    for line in text.splitlines():
                                        if not line.strip():
                                            continue
                                        try:
                                            data = json.loads(line)
                                            if "response" in data:
                                                word = data["response"]
                                                budget.consume_tokens(1) # approximate 1 token
                                                actual_tokens_generated += 1
                                                self.stats.total_flops_estimated += 2.0 * self.model.active_params * 1
                                                
                                                if client_msg_queue and actual_tokens_generated % 10 == 0:
                                                    yield f"<REQUEST_PAYMENT_NONCE:{actual_tokens_generated}>"
                                                    try:
                                                        client_msg = await asyncio.wait_for(client_msg_queue.get(), timeout=2.0)
                                                        if "signature" not in client_msg:
                                                            yield "\n\n<TRUNCATED: Invalid micro-payment format!>"
                                                            break
                                                            
                                                        from .crypto import TEEQuote
                                                        quote = TEEQuote(
                                                            public_key=client_msg.get("public_key", ""),
                                                            flops_actual=client_msg.get("flops_actual", 0),
                                                            signature_hex=client_msg.get("signature", ""),
                                                            mrenclave=client_msg.get("mrenclave", ""),
                                                            certificate_chain=client_msg.get("certificate_chain", [])
                                                        )
                                                        
                                                        # Anti-fraud monotonic check
                                                        if quote.flops_actual < actual_tokens_generated:
                                                            yield "\n\n<TRUNCATED: Fraud detected! Nonce decreased.>"
                                                            break
                                                            
                                                        # Verify Consumer's signature
                                                        if self.tee and not self.tee.verify_execution(quote, task.id):
                                                            yield "\n\n<TRUNCATED: Invalid micro-payment signature!>"
                                                            break
                                                            
                                                    except asyncio.TimeoutError:
                                                        yield "\n\n<TRUNCATED: Payment Timeout! Client did not sign nonce.>"
                                                        break
                                                        
                                                yield word
                                        except json.JSONDecodeError:
                                            pass
                                        except FLOPsExceeded:
                                            yield "\n\n<TRUNCATED: FLOPs Budget Exceeded! Generation stopped.>"
                                            logger.warning("Stream truncated: Budget exceeded for task %d", task.id)
                                            break
                        except Exception as e:
                            yield f"\n\n<Backend connection error: {e}>"
                            
            else:
                # Simulated Streaming Response
                yield "Here is the response from the simulated Compute Engine:\n"
                words = (task.payload * 2).split() + ["\nAnd", "here", "are", "more", "output", "tokens", "generated", "by", "the", "model."] * 10
                
                for word in words:
                    await asyncio.sleep(0.02)  # Simulate GPU latency
                    try:
                        budget.consume_tokens(1)  # 1 word ~ 1 token for simplicity
                        # Add decoding FLOPs to global stats
                        actual_tokens_generated += 1
                        self.stats.total_flops_estimated += 2.0 * self.model.active_params * 1
                        
                        if client_msg_queue and actual_tokens_generated % 10 == 0:
                            yield f"<REQUEST_PAYMENT_NONCE:{actual_tokens_generated}>"
                            try:
                                client_msg = await asyncio.wait_for(client_msg_queue.get(), timeout=2.0)
                                if "signature" not in client_msg:
                                    yield "\n\n<TRUNCATED: Invalid micro-payment format!>"
                                    break
                                    
                                from .crypto import TEEQuote
                                quote = TEEQuote(
                                    public_key=client_msg.get("public_key", ""),
                                    flops_actual=client_msg.get("flops_actual", 0),
                                    signature_hex=client_msg.get("signature", ""),
                                    mrenclave=client_msg.get("mrenclave", ""),
                                    certificate_chain=client_msg.get("certificate_chain", [])
                                )
                                
                                # Anti-fraud monotonic check
                                if quote.flops_actual < actual_tokens_generated:
                                    yield "\n\n<TRUNCATED: Fraud detected! Nonce decreased.>"
                                    break
                                    
                                # Verify Consumer's signature
                                if self.tee and not self.tee.verify_execution(quote, task.id):
                                    yield "\n\n<TRUNCATED: Invalid micro-payment signature!>"
                                    break
                                    
                            except asyncio.TimeoutError:
                                yield "\n\n<TRUNCATED: Payment Timeout! Client did not sign nonce.>"
                                break
                                
                        yield word + " "
                    except FLOPsExceeded:
                        yield "\n\n<TRUNCATED: FLOPs Budget Exceeded! Generation stopped.>"
                        logger.warning("Stream truncated: Budget exceeded for task %d", task.id)
                        break
            
            # TEE Cryptographic Signature Generation
            if self.tee:
                # Sign the exact actual FLOPs used for this task budget
                actual_flops = int(budget.consumed_flops)
                quote = self.tee.sign_execution(task.id, actual_flops)
                
                tee_payload = {
                    "public_key": quote.public_key,
                    "flops_actual": quote.flops_actual,
                    "signature": quote.signature_hex,
                    "mrenclave": quote.mrenclave,
                    "certificate_chain": quote.certificate_chain
                }
                yield f"\n\n<TEE_SIGNATURE> {json.dumps(tee_payload)}\n"
                logger.info(f"[TEE Attestation Signature generated] for Task {task.id} with {actual_flops} FLOPs.")
                
            # If we successfully completed the local simulation as provider
            # (In reality, Consumer only routes, Standalone does both, Provider only runs execution).
            yield "\n<DONE>"
        finally:
            self.stats.active_streams -= 1
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
