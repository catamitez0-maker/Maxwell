"""
Maxwell Protocol — Decentralized compute metering & heuristic pruning.
"""

__version__ = "0.1.0"

__all__ = [
    "PruningProxy",
    "FunnelStats",
    "Task",
    "Decision",
    "BloomFilter",
    "shannon_entropy",
]

from .filters import BloomFilter, shannon_entropy
from .models import Decision, FunnelStats, Task
from .proxy import PruningProxy
