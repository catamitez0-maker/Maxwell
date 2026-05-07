"""
Core filtering primitives for the Maxwell pruning funnel.

- L1: BloomFilter — O(1) probabilistic membership test (bitarray + mmh3)
- L2: regex_gate — compiled regex pattern matching
- L3: shannon_entropy / entropy_gate — adaptive Shannon information entropy
- L5: repetition_gate — anti-idle loop detection
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Sequence

import mmh3
import numpy as np
from bitarray import bitarray

__all__ = [
    "BloomFilter",
    "shannon_entropy",
    "entropy_gate",
    "regex_gate",
    "repetition_gate",
]


class BloomFilter:
    """
    L1 — Memory-efficient probabilistic membership filter.

    Uses MurmurHash3 with multiple seeds for O(1) lookup
    with mathematically optimal bit-array sizing and hash count.
    """

    __slots__ = ("size", "hash_count", "bits", "_count")

    def __init__(self, capacity: int = 10_000, fp_rate: float = 0.01) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")
        if not (0 < fp_rate < 1):
            raise ValueError(f"fp_rate must be in (0, 1), got {fp_rate}")

        self.size: int = self._optimal_size(capacity, fp_rate)
        self.hash_count: int = self._optimal_hash_count(self.size, capacity)
        self.bits: bitarray = bitarray(self.size)
        self.bits.setall(0)
        self._count: int = 0

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        m = -(n * math.log(p)) / (math.log(2) ** 2)
        return int(m) + 1

    @staticmethod
    def _optimal_hash_count(m: int, n: int) -> int:
        k = (m / n) * math.log(2)
        return max(1, int(k))

    def _hashes(self, item: str) -> list[int]:
        return [mmh3.hash(item, seed, signed=False) % self.size
                for seed in range(self.hash_count)]

    def add(self, item: str) -> None:
        """Insert an item into the filter."""
        for pos in self._hashes(item):
            self.bits[pos] = 1
        self._count += 1

    def __contains__(self, item: str) -> bool:
        return all(self.bits[pos] for pos in self._hashes(item))

    def __len__(self) -> int:
        return self._count

    @property
    def estimated_fp_rate(self) -> float:
        if self._count == 0:
            return 0.0
        fill = self.bits.count(1) / self.size
        return fill ** self.hash_count


# ── L3: Shannon Entropy ────────────────────────────────────────────


def shannon_entropy(text: str) -> float:
    """
    Calculate Shannon information entropy using numpy vectorized ops.

    Returns bits-per-character.
    """
    if not text:
        return 0.0
    codes = np.frombuffer(text.encode("utf-8"), dtype=np.uint8)
    _, counts = np.unique(codes, return_counts=True)
    probabilities = counts / counts.sum()
    return float(-np.sum(probabilities * np.log2(probabilities)))


def entropy_gate(
    payload: str,
    low_threshold: float = 1.0,
    high_threshold: float = 4.5,
    load_factor: float = 0.0,
) -> bool:
    """
    Adaptive entropy gate — returns True if payload should be BLOCKED.

    Under high load, thresholds tighten (more aggressive pruning).
    """
    dynamic_low = low_threshold * (1.0 + load_factor * 0.5)
    dynamic_high = high_threshold * (1.0 - load_factor * 0.2)
    entropy = shannon_entropy(payload)
    return entropy < dynamic_low or entropy > dynamic_high


# ── L2: Regex Gate ─────────────────────────────────────────────────


def regex_gate(payload: str, rules: Sequence[re.Pattern[str]]) -> bool:
    """L2 — Returns True if payload matches any compiled regex rule."""
    return any(rule.search(payload) for rule in rules)


# ── L5: Anti-Idle Repetition Detector ──────────────────────────────


def repetition_gate(
    payload: str,
    max_ngram_ratio: float = 0.2,
    ngram_size: int = 3,
    min_length: int = 12,
) -> bool:
    """
    L5 — Detect repetitive / looping content (anti-idle).

    Returns True if payload should be BLOCKED.

    Detects payloads with excessively repeated n-gram patterns,
    which indicate idle loops, padding attacks, or meaningless repetition.

    Args:
        payload: input string
        max_ngram_ratio: block if most-frequent ngram ratio exceeds this
        ngram_size: character n-gram window size
        min_length: skip check for short payloads
    """
    if len(payload) < min_length:
        return False

    # Build n-gram frequency distribution
    ngrams = [payload[i:i + ngram_size] for i in range(len(payload) - ngram_size + 1)]
    if not ngrams:
        return False

    counter = Counter(ngrams)
    most_common_count = counter.most_common(1)[0][1]
    ratio = most_common_count / len(ngrams)

    return ratio > max_ngram_ratio
