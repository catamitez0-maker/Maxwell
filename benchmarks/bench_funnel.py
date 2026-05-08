#!/usr/bin/env python3
"""
Maxwell Performance Benchmark Suite

Measures throughput, latency percentiles, and per-layer filtering
performance of the Maxwell pruning funnel.

Usage:
    python benchmarks/bench_funnel.py
    python benchmarks/bench_funnel.py --iterations 50000 --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import sys
import os

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maxwell.filters import BloomFilter, shannon_entropy, entropy_gate
from maxwell.models import FunnelStats, Task
from maxwell.oracle import MODELS, FLOPsLimiter, TaskBudget
from maxwell.proxy import PruningProxy
from maxwell.backends import SimulatedBackend


# ── Payloads ─────────────────────────────────────────────────────────

PAYLOADS_MIXED = [
    # Should pass
    "Explain the transformer architecture in detail",
    "What are the benefits of attention mechanisms?",
    "Describe gradient descent optimization",
    "How does backpropagation work in neural networks?",
    "正常的中文推理请求，请解释量子计算",
    # Should be blocked (various layers)
    "aaa",                           # L3: low entropy
    "abcabcabcabcabcabc" * 5,        # L5: repetition
    "<script>alert(1)</script>",     # L2: regex (if pattern loaded)
    "admin_login",                   # L1: bloom (if in blacklist)
    "x" * 200,                       # L3: zero entropy
]

PAYLOADS_CLEAN = [
    "Explain the benefits of distributed computing",
    "What is the difference between TCP and UDP?",
    "Describe how a hash table works internally",
    "How does a B-tree index improve database performance?",
    "What are the tradeoffs of eventual consistency?",
]


# ── Micro-benchmarks ─────────────────────────────────────────────────

def bench_bloom_filter(n: int = 100_000) -> dict:
    """Benchmark Bloom filter insert + lookup."""
    bf = BloomFilter(capacity=n, fp_rate=0.001)
    items = [f"item_{i}" for i in range(n)]

    # Insert
    t0 = time.perf_counter()
    for item in items:
        bf.add(item)
    insert_time = time.perf_counter() - t0

    # Lookup (all hits)
    t0 = time.perf_counter()
    for item in items:
        _ = item in bf
    lookup_time = time.perf_counter() - t0

    # Lookup (all misses)
    misses = [f"miss_{i}" for i in range(n)]
    t0 = time.perf_counter()
    for item in misses:
        _ = item in bf
    miss_time = time.perf_counter() - t0

    return {
        "name": "Bloom Filter",
        "items": n,
        "insert_ops_sec": int(n / insert_time),
        "lookup_hit_ops_sec": int(n / lookup_time),
        "lookup_miss_ops_sec": int(n / miss_time),
        "insert_total_ms": round(insert_time * 1000, 2),
        "lookup_total_ms": round(lookup_time * 1000, 2),
    }


def bench_entropy(n: int = 100_000) -> dict:
    """Benchmark Shannon entropy calculation."""
    payloads = PAYLOADS_CLEAN * (n // len(PAYLOADS_CLEAN))
    latencies = []

    for p in payloads:
        t0 = time.perf_counter()
        shannon_entropy(p)
        latencies.append(time.perf_counter() - t0)

    return {
        "name": "Shannon Entropy",
        "iterations": len(payloads),
        "ops_sec": int(len(payloads) / sum(latencies)),
        "p50_us": round(statistics.quantiles(latencies, n=100)[49] * 1e6, 2),
        "p95_us": round(statistics.quantiles(latencies, n=100)[94] * 1e6, 2),
        "p99_us": round(statistics.quantiles(latencies, n=100)[98] * 1e6, 2),
    }


def bench_entropy_gate(n: int = 100_000) -> dict:
    """Benchmark entropy_gate (entropy + classification)."""
    payloads = (PAYLOADS_CLEAN + ["aaa", "x" * 100]) * (n // 7)
    latencies = []
    passed = 0

    for p in payloads:
        t0 = time.perf_counter()
        val = shannon_entropy(p)
        blocked = entropy_gate(p, low_threshold=1.0, high_threshold=4.5)
        latencies.append(time.perf_counter() - t0)
        if not blocked:
            passed += 1

    return {
        "name": "Entropy Gate (entropy + classify)",
        "iterations": len(payloads),
        "ops_sec": int(len(payloads) / sum(latencies)),
        "pass_rate": round(passed / len(payloads) * 100, 1),
        "p50_us": round(statistics.quantiles(latencies, n=100)[49] * 1e6, 2),
        "p95_us": round(statistics.quantiles(latencies, n=100)[94] * 1e6, 2),
        "p99_us": round(statistics.quantiles(latencies, n=100)[98] * 1e6, 2),
    }


# ── Full Funnel Benchmark ───────────────────────────────────────────

async def bench_funnel_throughput(
    iterations: int = 10_000,
    workers: int = 2,
) -> dict:
    """Benchmark the full PruningProxy funnel throughput."""
    stats = FunnelStats()
    model = MODELS["llama-7b"]
    proxy = PruningProxy(
        stats,
        worker_count=workers,
        model=model,
        max_seq_length=8192,
        role="standalone",
    )

    # Pre-load some blacklist entries
    proxy.bloom.add("admin_login")
    proxy.bloom.add("exec(rm")

    # Override get_backend in execution module for zero-delay benchmarking
    import maxwell.execution as _exec
    _exec.get_backend = lambda *a, **kw: SimulatedBackend(delay=0.0)

    latencies: list[float] = []
    payloads = PAYLOADS_MIXED * (iterations // len(PAYLOADS_MIXED))

    t_total_start = time.perf_counter()

    for i, payload in enumerate(payloads):
        task = Task(id=i, payload=payload)
        t0 = time.perf_counter()
        async for _ in proxy.process_stream(task):
            pass
        latencies.append(time.perf_counter() - t0)

    t_total = time.perf_counter() - t_total_start
    n = len(payloads)

    sorted_lat = sorted(latencies)

    return {
        "name": f"Full Funnel (workers={workers})",
        "iterations": n,
        "total_time_sec": round(t_total, 3),
        "throughput_rps": round(n / t_total, 1),
        "latency_p50_ms": round(sorted_lat[int(n * 0.50)] * 1000, 3),
        "latency_p95_ms": round(sorted_lat[int(n * 0.95)] * 1000, 3),
        "latency_p99_ms": round(sorted_lat[int(n * 0.99)] * 1000, 3),
        "latency_mean_ms": round(statistics.mean(latencies) * 1000, 3),
        "latency_max_ms": round(max(latencies) * 1000, 3),
        "stats": {
            "total_requests": stats.total_requests,
            "passed": stats.passed_to_engine,
            "bloom_blocked": stats.bloom_blocked,
            "regex_blocked": stats.regex_blocked,
            "entropy_blocked": stats.entropy_blocked,
            "repetition_blocked": stats.repetition_blocked,
            "pruning_rate": round(stats.pruning_rate, 1),
        },
    }


async def bench_simulated_backend(tokens: int = 500) -> dict:
    """Benchmark the simulated backend streaming speed."""
    backend = SimulatedBackend(delay=0.0)
    latencies: list[float] = []

    for _ in range(10):
        count = 0
        t0 = time.perf_counter()
        async for _ in backend.stream("test prompt", "llama-7b"):
            count += 1
            if count >= tokens:
                break
        latencies.append(time.perf_counter() - t0)

    return {
        "name": f"Simulated Backend ({tokens} tokens)",
        "tokens_per_run": tokens,
        "runs": len(latencies),
        "tokens_per_sec": round(tokens / statistics.mean(latencies), 1),
        "mean_ms": round(statistics.mean(latencies) * 1000, 2),
    }


# ── Report ───────────────────────────────────────────────────────────

def print_report(results: list[dict]) -> None:
    """Print a formatted benchmark report."""
    sep = "═" * 70
    print(f"\n{sep}")
    print("  ⚡ Maxwell Protocol — Performance Benchmark Report")
    print(f"{sep}\n")

    for r in results:
        name = r.pop("name")
        print(f"  ▸ {name}")
        print(f"  {'─' * 60}")

        # Nested stats
        nested = r.pop("stats", None)

        for k, v in r.items():
            label = k.replace("_", " ").title()
            print(f"    {label:.<40s} {v}")

        if nested:
            print(f"    {'─' * 40}")
            for k, v in nested.items():
                label = k.replace("_", " ").title()
                print(f"    {label:.<40s} {v}")

        print()

    print(f"{sep}")
    print(f"  Completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{sep}\n")


# ── Main ─────────────────────────────────────────────────────────────

async def main(iterations: int = 10_000, workers: int = 2) -> None:
    results = []

    print("\n⏱  Running micro-benchmarks...")
    results.append(bench_bloom_filter())
    results.append(bench_entropy())
    results.append(bench_entropy_gate())

    print("⏱  Running backend benchmark...")
    results.append(await bench_simulated_backend())

    print(f"⏱  Running full funnel benchmark ({iterations} iterations, {workers} workers)...")
    results.append(await bench_funnel_throughput(iterations, workers))

    print_report(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Maxwell Performance Benchmarks")
    parser.add_argument("--iterations", type=int, default=10_000, help="Funnel iterations")
    parser.add_argument("--workers", type=int, default=2, help="Funnel workers")
    args = parser.parse_args()

    asyncio.run(main(args.iterations, args.workers))
