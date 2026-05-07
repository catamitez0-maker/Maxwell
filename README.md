# ⚡ Maxwell Protocol

[English](#english) | [中文](#chinese)

> *"Stop paying for Token length. Pay for real FLOPs."*
> 
> *停止为信息载体买单，为真实的物理做功付费。*

[![CI](https://github.com/catamitez0-maker/Maxwell/actions/workflows/ci.yml/badge.svg)](https://github.com/catamitez0-maker/Maxwell/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

<a id="english"></a>
## 🇬🇧 English

Maxwell is a decentralized, open-source **AI Compute Settlement and Filtering Protocol**. It intercepts invalid requests through heuristic pruning and accurately meters the real FLOPs consumed during AI inference, serving as foundational infrastructure for a fair compute network.

### Architecture

```
  Request ───► ┌──────────────────────────────────────┐
               │          maxwell-proxy                │
               │  ┌─ L1: Bloom Filter (O(1))          │
               │  ├─ L2: Regex Rules                  │
               │  ├─ L3: Shannon Entropy Gate         │
               │  ├─ L4: Oracle FLOPs Budget ─────────│──► maxwell-oracle
               │  ├─ L5: Anti-Idle Repetition         │    (FLOPs ≈ 2×N×S)
               │  └─ Circuit Breaker                  │
               └────────────┬─────────────────────────┘
                            │ PASSED + FLOPs budgeted
               ┌────────────▼─────────────────────────┐
               │        Compute Engine (GPU)           │
               │  (Streaming dynamic FLOPs metering)   │
               └────────────┬─────────────────────────┘
                            │ Actual FLOPs
               ┌────────────▼─────────────────────────┐
               │      maxwell-contracts (Solidity)     │
               │  FLOPs-based settlement on-chain      │
               └──────────────────────────────────────┘
```

### Core Subsystems

| Subsystem | Description | Status |
|--------|------|------|
| **maxwell-proxy** | 5-layer heuristic pruning gateway + streaming circuit breaker | ✅ Production Ready |
| **maxwell-oracle** | Transformer FLOPs estimation + dynamic streaming budget cutoff | ✅ Integrated |
| **maxwell-contracts** | Solidity settlement contract (priced by FLOPs) | ✅ Contract Template |

### Quick Start

#### Docker (Recommended)

```bash
git clone https://github.com/catamitez0-maker/Maxwell.git
cd Maxwell
docker compose up -d
```

#### Local Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Server Mode
maxwell --mode server --port 8080 --workers 4

# Simulation Mode (with synthetic traffic)
maxwell --mode simulate --rate 0.01 --verbose
```

### API Endpoints

| Method | Path | Description |
|--------|------|------|
| `POST` | `/v1/proxy` | Submit tasks to the pruning funnel (StreamingResponse) |
| `GET` | `/healthz` | Health check (Docker probe) |
| `GET` | `/v1/stats` | Detailed statistics (5-layer blocks + real-time FLOPs + circuit breaker) |

#### Submit Task

```bash
curl -N -X POST http://localhost:8080/v1/proxy \
  -H "Content-Type: application/json" \
  -d '{"payload": "your AI inference request"}'
```
*Note: The response will stream the generated content and dynamically meter FLOPs. It will truncate immediately if the budget is exceeded.*

### Dynamic Rules Configuration

Edit `rules.json` (hot-reloaded, no restart required):

```json
{
  "blacklist": ["known_bad_hash", "spam_pattern"],
  "regex_rules": ["^.{0,3}$", "exec\\("]
}
```

### Smart Contracts

`contracts/MaxwellSettlement.sol` implements:

- **Provider Registration**: Stake deposits + set price per FLOP
- **Consumer Deposits**: Pre-fund compute expenses
- **Settlement by FLOPs**: `cost = flops × price_per_petaflop / 1e15`
- **Dispute Mechanism**: 1-hour challenge window
- **Protocol Fee**: 0.5% platform cut

### Tech Stack

- **Core**: Python 3.10+ / asyncio
- **Algorithms**: `bitarray` + `mmh3` (Bloom Filter) · `numpy` (Shannon Entropy)
- **Networking**: `aiohttp` (Async HTTP & Streaming)
- **CLI**: `typer` + `rich` (Live Dashboard)
- **Contracts**: Solidity ^0.8.20
- **Deployment**: Docker multi-stage · non-root

---

<a id="chinese"></a>
## 🇨🇳 中文

Maxwell 是一套去中心化的开源 **AI 算力结算与过滤协议**。通过启发式剪枝技术拦截无效请求，动态流式计量 AI 推理的真实 FLOPs，为公平的算力网络提供底层基础设施。

### 架构

```
  Request ───► ┌──────────────────────────────────────┐
               │          maxwell-proxy                │
               │  ┌─ L1: Bloom Filter (O(1))          │
               │  ├─ L2: Regex Rules                  │
               │  ├─ L3: Shannon Entropy Gate         │
               │  ├─ L4: Oracle FLOPs Budget ─────────│──► maxwell-oracle
               │  ├─ L5: Anti-Idle Repetition         │    (FLOPs ≈ 2×N×S)
               │  └─ Circuit Breaker                  │
               └────────────┬─────────────────────────┘
                            │ PASSED + FLOPs budgeted
               ┌────────────▼─────────────────────────┐
               │        Compute Engine (GPU)           │
               │  (Streaming dynamic FLOPs metering)   │
               └────────────┬─────────────────────────┘
                            │ Actual FLOPs
               ┌────────────▼─────────────────────────┐
               │      maxwell-contracts (Solidity)     │
               │  FLOPs-based settlement on-chain      │
               └──────────────────────────────────────┘
```

### 三大子系统

| 子系统 | 说明 | 状态 |
|--------|------|------|
| **maxwell-proxy** | 5 层启发式剪枝网关 + 流式生成与熔断器 | ✅ 生产可用 |
| **maxwell-oracle** | Transformer FLOPs 预估 + 动态流式算力预算截断 | ✅ 已集成 |
| **maxwell-contracts** | Solidity 结算合约 (按 FLOPs 定价) | ✅ 合约模板 |

### 快速开始

#### Docker (推荐)

```bash
git clone https://github.com/catamitez0-maker/Maxwell.git
cd Maxwell
docker compose up -d
```

#### 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Server 模式
maxwell --mode server --port 8080 --workers 4

# Simulation 模式 (含仿真并发流量)
maxwell --mode simulate --rate 0.01 --verbose
```

### API 端点

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/v1/proxy` | 提交任务到剪枝漏斗 (StreamingResponse) |
| `GET` | `/healthz` | 健康检查 (Docker probe) |
| `GET` | `/v1/stats` | 详细统计 (5 层拦截 + 实时 FLOPs + 熔断器) |

#### 提交任务

```bash
curl -N -X POST http://localhost:8080/v1/proxy \
  -H "Content-Type: application/json" \
  -d '{"payload": "your AI inference request"}'
```
*注：该接口为流式返回。如果大模型输出期间可用算力预算耗尽，将立即强制截断数据流。*

### 动态规则配置

编辑 `rules.json`（热加载，无需重启）：

```json
{
  "blacklist": ["known_bad_hash", "spam_pattern"],
  "regex_rules": ["^.{0,3}$", "exec\\("]
}
```

### 智能合约

`contracts/MaxwellSettlement.sol` 实现了：

- **Provider 注册**: 质押保证金 + 设定 FLOPs 单价
- **Consumer 充值**: 预存计算费用
- **按 FLOPs 结算**: `cost = flops × price_per_petaflop / 1e15`
- **争议机制**: 1 小时挑战窗口期
- **协议费**: 0.5% 平台抽成

### 技术栈

- **核心**: Python 3.10+ / asyncio
- **算法**: `bitarray` + `mmh3` (Bloom Filter) · `numpy` (信息熵)
- **网络**: `aiohttp` (异步 HTTP 及流式返回)
- **CLI**: `typer` + `rich` (实时并发仪表盘)
- **合约**: Solidity ^0.8.20
- **部署**: Docker multi-stage · non-root

## License

[MIT](LICENSE)
