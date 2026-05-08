"""
Maxwell Protocol — Decentralized compute metering & heuristic pruning.

Core exports (always available):
    PruningProxy, FunnelStats, Task, Decision
    BloomFilter, shannon_entropy, entropy_gate
    MaxwellConfig, estimate_flops, MODELS
    Backend, get_backend

Optional exports (require extras):
    TEESimulator       — pip install maxwell-protocol[web3]
    Web3Relayer        — pip install maxwell-protocol[web3]
    P2PManager         — pip install maxwell-protocol[p2p]
    hardware_monitor   — pip install maxwell-protocol[gpu]
"""

__version__ = "0.2.0"

# ── Core (always available) ──────────────────────────────────────────

from .backends import Backend, SimulatedBackend, OllamaBackend, get_backend
from .client import MaxwellClient, MaxwellAPIError
from .config import MaxwellConfig
from .filters import BloomFilter, shannon_entropy, entropy_gate
from .models import Decision, FunnelStats, Task
from .oracle import FLOPsLimiter, ModelConfig, MODELS
from .proxy import PruningProxy
from .qos import TokenBucket, ClientBucketManager

__all__ = [
    # Core
    "PruningProxy",
    "FunnelStats",
    "Task",
    "Decision",
    "BloomFilter",
    "shannon_entropy",
    "entropy_gate",
    "MaxwellConfig",
    "FLOPsLimiter",
    "ModelConfig",
    "MODELS",
    "Backend",
    "SimulatedBackend",
    "OllamaBackend",
    "get_backend",
    "TokenBucket",
    "ClientBucketManager",
    # Version
    "__version__",
]

# ── Optional: Web3 / TEE ─────────────────────────────────────────────

try:
    from .crypto import TEESimulator, TEEQuote, HAS_WEB3
    if HAS_WEB3:
        __all__.extend(["TEESimulator", "TEEQuote"])
except ImportError:
    pass

try:
    from .settlement import Web3Relayer, SettlementHandler
    __all__.extend(["Web3Relayer", "SettlementHandler"])
except ImportError:
    pass

# ── Optional: P2P ────────────────────────────────────────────────────

try:
    from .p2p import P2PManager, HAS_KADEMLIA
    if HAS_KADEMLIA:
        __all__.extend(["P2PManager"])
except ImportError:
    pass

# ── Optional: GPU ────────────────────────────────────────────────────

try:
    from .hardware import hardware_monitor
    __all__.append("hardware_monitor")
except ImportError:
    pass
