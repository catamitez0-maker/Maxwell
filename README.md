# ⚡ Maxwell Protocol

**Heuristic pruning gateway & compute metering for AI inference.**

Maxwell sits between clients and LLM backends, filtering adversarial/junk traffic through a multi-layer pruning funnel before it reaches expensive GPU compute. Every token that passes is metered in FLOPs, enabling real-time cost tracking and optional on-chain settlement.

[![CI](https://github.com/maxwell-protocol/maxwell/actions/workflows/ci.yml/badge.svg)](https://github.com/maxwell-protocol/maxwell/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Architecture

```
Client → [HMAC Auth] → [Bloom Filter] → [Regex] → [Entropy Gate]
                  → [FLOPs Oracle] → [Repetition Gate] → [Circuit Breaker]
                  → Backend (Ollama / OpenAI / vLLM / Simulated)
                  → [TEE Attestation] → Response
```

### Pruning Layers

| Layer | Mechanism | Purpose |
|-------|-----------|---------|
| L1 | Bloom Filter | O(1) blacklist lookup (zero false negatives) |
| L2 | Regex Engine | Pattern-based injection detection |
| L3 | Shannon Entropy Gate | Block low-entropy junk (repetitive spam) |
| L4 | FLOPs Oracle | Per-model compute budget enforcement |
| L5 | Repetition Gate | Detect repeated-substring abuse |
| L6 | Circuit Breaker | Auto-throttle under overload |

---

## Installation

```bash
# Core only (6 dependencies — filtering, metering, API server)
pip install maxwell-protocol

# With TEE/Settlement (Web3 + ECDSA)
pip install maxwell-protocol[web3]

# With P2P discovery (Kademlia DHT)
pip install maxwell-protocol[p2p]

# With GPU telemetry (NVML)
pip install maxwell-protocol[gpu]

# Everything
pip install maxwell-protocol[full]
```

### From source

```bash
git clone https://github.com/maxwell-protocol/maxwell.git
cd maxwell
pip install -e ".[full,dev]"
```

---

## Quick Start

### 1. Initialize a project

```bash
maxwell init my-project
cd my-project
```

This generates:
- `maxwell.toml` — configuration file
- `rules.json` — blacklist + regex patterns
- `api_keys.json` — API key pair
- `logs/` — log directory

### 2. Start the server

```bash
# With config file
maxwell serve --config maxwell.toml

# Or with CLI flags
maxwell serve --port 8080 --model-name llama-7b

# Simulation mode (no backend required)
maxwell serve --mode simulate
```

### 3. Send a request

```bash
# Generate a key
maxwell keygen
# → Key ID: mxk_abc123
# → Secret: deadbeef...

# Send authenticated request
curl -X POST http://localhost:8080/v1/proxy \
  -H "Content-Type: application/json" \
  -H "X-Maxwell-Key: mxk_abc123" \
  -H "X-Maxwell-Signature: $(python3 -c "
import hmac, hashlib, time
ts = str(int(time.time()))
body = '{\"payload\": \"Explain quantum computing\"}'
print(hmac.new(b'YOUR_SECRET', (ts+body).encode(), hashlib.sha256).hexdigest())
")" \
  -H "X-Maxwell-Timestamp: $(date +%s)" \
  -d '{"payload": "Explain quantum computing"}'
```

---

## Python SDK

```python
from maxwell.client import MaxwellClient

# Async usage
async with MaxwellClient(
    "http://localhost:8080",
    key_id="mxk_abc123",
    secret="your_secret_here",
) as client:

    # Non-streaming
    result = await client.query("What is 2+2?")
    print(result)

    # Streaming
    async for token in client.stream("Explain transformers"):
        print(token, end="")

    # Health check (no auth required)
    health = await client.health()
    print(health)  # {"status": "ok", "uptime": 120.5, ...}

    # Stats
    stats = await client.stats()
    print(stats["layers"])  # {"L1_bloom_blocked": 42, ...}
```

### Sync wrapper

```python
from maxwell.client import MaxwellClient

client = MaxwellClient("http://localhost:8080")
result = client.query_sync("Hello world")
health = client.health_sync()
```

---

## Embeddable API

Use Maxwell as a library in your own application:

```python
from maxwell import PruningProxy, FunnelStats, MaxwellConfig, get_backend
from maxwell.oracle import MODELS

# Create components
stats = FunnelStats()
model = MODELS["llama-7b"]
backend = get_backend("simulated")

proxy = PruningProxy(
    stats,
    worker_count=2,
    model=model,
    max_seq_length=8192,
)

# Load rules
await proxy.reload_rules("rules.json")

# Process a task
from maxwell.models import Task
task = Task(id=1, payload="Hello, explain attention mechanisms")

async for token in proxy.process_stream(task):
    print(token, end="")
```

---

## Configuration

Maxwell supports three configuration sources (highest priority first):

1. **CLI arguments** — `maxwell serve --port 9090`
2. **TOML file** — `maxwell serve --config maxwell.toml`
3. **Environment variables** — `MAXWELL_PORT=9090`

### maxwell.toml

```toml
[server]
host = "0.0.0.0"
port = 8080
mode = "server"         # "server" or "simulate"
role = "standalone"     # "standalone", "consumer", "provider", "settlement"

[funnel]
entropy_low = 1.0       # Below this → block
entropy_high = 4.5      # Above this → pass
workers = 2
rules_path = "rules.json"

[model]
name = "llama-7b"       # See supported models below
max_seq_length = 8192

[backend]
backend_url = ""        # Empty = simulated mode
backend_type = "ollama" # "ollama", "openai", "vllm"

[auth]
api_keys_path = "api_keys.json"

[logging]
log_path = "logs/maxwell_access.jsonl"
verbose = false
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAXWELL_PORT` | `8080` | Server port |
| `MAXWELL_HOST` | `0.0.0.0` | Bind address |
| `MAXWELL_MODEL_NAME` | `llama-7b` | Model for FLOPs estimation |
| `MAXWELL_BACKEND_URL` | `""` | LLM backend URL |
| `MAXWELL_VERBOSE` | `false` | Debug logging |
| `MAXWELL_API_KEYS` | `""` | `key:secret,key:secret` pairs |
| `WEB3_RPC_URL` | `""` | Ethereum RPC endpoint |
| `RELAYER_PRIVATE_KEY` | `""` | Settlement signing key |

### Supported Models

| Model | Active Params | FLOPs/Token |
|-------|---------------|-------------|
| `llama-7b` | 7B | 14 GFLOPs |
| `llama-13b` | 13B | 26 GFLOPs |
| `llama-70b` | 70B | 140 GFLOPs |
| `mixtral-8x7b` | 12.9B | 25.8 GFLOPs |
| `gpt-4-turbo` | ~200B | 400 GFLOPs |

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/proxy` | ✅ | Submit inference task (streaming response) |
| GET | `/healthz` | ❌ | Health check / liveness probe |
| GET | `/v1/stats` | ✅ | Detailed funnel statistics |
| GET | `/dashboard` | ❌ | Web dashboard |
| POST | `/settle` | ✅ | Submit settlement transaction |
| GET | `/balances/{addr}` | ❌ | Check address balance |
| GET | `/ledger` | ❌ | View transaction ledger |

### Authentication

All authenticated endpoints require HMAC-SHA256 headers:

```
X-Maxwell-Key: <key_id>
X-Maxwell-Signature: HMAC-SHA256(secret, timestamp + body)
X-Maxwell-Timestamp: <unix_epoch_seconds>
```

Requests older than 300 seconds are rejected (replay protection).

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[full,dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check maxwell/

# Type check
mypy maxwell/
```

---

## License

MIT © Maxwell Protocol Contributors
