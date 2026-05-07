"""
Core data models with strict type hints.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

__all__ = ["Decision", "Task", "FunnelStats"]


class Decision(str, Enum):
    """Funnel decision outcome."""
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"


@dataclass
class Task:
    """A unit of work entering the pruning funnel."""
    id: int
    payload: str
    signature: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def token_estimate(self) -> int:
        """Rough token count estimate (~4 chars per token for English)."""
        return max(1, len(self.payload) // 4)


@dataclass
class FunnelStats:
    """Real-time statistics for the pruning funnel."""
    total_requests: int = 0

    # L1-L3 pruning counters
    bloom_blocked: int = 0       # L1
    regex_blocked: int = 0       # L2
    entropy_blocked: int = 0     # L3

    # L4 oracle
    oracle_blocked: int = 0      # L4: FLOPs budget exceeded
    total_flops_estimated: float = 0.0  # cumulative FLOPs metered

    # L5 anti-idle
    repetition_blocked: int = 0  # L5: anti-idle repetition detector

    # Circuit breaker
    circuit_blocked: int = 0
    is_circuit_open: bool = False

    # Pass-through
    passed_to_engine: int = 0
    
    # Streaming load
    active_streams: int = 0

    # System
    start_time: float = field(default_factory=time.time)
    current_load: float = 0.0
    entropy_low: float = 1.0
    entropy_high: float = 4.5

    @property
    def total_blocked(self) -> int:
        return (
            self.bloom_blocked + self.regex_blocked + self.entropy_blocked
            + self.oracle_blocked + self.repetition_blocked + self.circuit_blocked
        )

    @property
    def pruning_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return (self.total_blocked / self.total_requests) * 100

    @property
    def uptime(self) -> float:
        return time.time() - self.start_time

    @property
    def qps(self) -> float:
        """Requests per second since start."""
        elapsed = self.uptime
        if elapsed <= 0:
            return 0.0
        return self.total_requests / elapsed

    @property
    def flops_display(self) -> str:
        """Human-readable cumulative FLOPs."""
        f = self.total_flops_estimated
        if f >= 1e15:
            return f"{f / 1e15:.2f} PFLOPs"
        elif f >= 1e12:
            return f"{f / 1e12:.2f} TFLOPs"
        elif f >= 1e9:
            return f"{f / 1e9:.2f} GFLOPs"
        elif f >= 1e6:
            return f"{f / 1e6:.2f} MFLOPs"
        else:
            return f"{f:.0f} FLOPs"
