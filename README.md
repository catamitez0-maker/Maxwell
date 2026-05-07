# ⚡ Maxwell Protocol

> *"Stop paying for Token length. Pay for real FLOPs."*
> *停止为信息载体买单，为真实的物理做功付费。*

[![CI](https://github.com/catamitez0-maker/Maxwell/actions/workflows/ci.yml/badge.svg)](https://github.com/catamitez0-maker/Maxwell/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Maxwell 是一套去中心化的开源 **AI 算力结算与过滤协议**。通过启发式剪枝技术拦截无效请求，精确计量 AI 推理的真实 FLOPs，为公平的算力网络提供底层基础设施。

## 架构

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
                            │ PASSED + FLOPs metered
               ┌────────────▼─────────────────────────┐
               │        Compute Engine (GPU)           │
               └────────────┬─────────────────────────┘
                            │ Actual FLOPs
               ┌────────────▼─────────────────────────┐
               │      maxwell-contracts (Solidity)     │
               │  FLOPs-based settlement on-chain      │
               └──────────────────────────────────────┘
```

## 三大子系统

| 子系统 | 说明 | 状态 |
|--------|------|------|
| **maxwell-proxy** | 5 层启发式剪枝网关 + 熔断器 | ✅ 生产可用 |
| **maxwell-oracle** | Transformer FLOPs 预估 + 算力预算熔断 | ✅ 已集成 |
| **maxwell-contracts** | Solidity 结算合约 (按 FLOPs 定价) | ✅ 合约模板 |

## 快速开始

### Docker (推荐)

```bash
git clone https://github.com/catamitez0-maker/Maxwell.git
cd Maxwell
docker compose up -d
```

### 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Server 模式
maxwell --mode server --port 8080 --workers 4

# Simulation 模式 (含仿真流量)
maxwell --mode simulate --rate 0.01 --verbose
```

## API 端点

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/v1/proxy` | 提交任务到剪枝漏斗 |
| `GET` | `/healthz` | 健康检查 (Docker probe) |
| `GET` | `/v1/stats` | 详细统计 (5 层拦截 + FLOPs + 熔断器) |

### 提交任务

```bash
curl -X POST http://localhost:8080/v1/proxy \
  -H "Content-Type: application/json" \
  -d '{"payload": "your AI inference request"}'
```

### 查看统计

```json
{
  "total_requests": 1000,
  "qps": 42.5,
  "pruning_rate": 72.3,
  "layers": {
    "L1_bloom_blocked": 150,
    "L2_regex_blocked": 180,
    "L3_entropy_blocked": 203,
    "L4_oracle_blocked": 12,
    "L5_repetition_blocked": 178,
    "circuit_blocked": 0
  },
  "oracle": {
    "total_flops_metered": 3940000000000.0,
    "flops_display": "3.94 TFLOPs",
    "model_params": 7000000000
  },
  "circuit_breaker": "CLOSED"
}
```

## 规则配置

编辑 `rules.json`（热加载，无需重启）：

```json
{
  "blacklist": ["known_bad_hash", "spam_pattern"],
  "regex_rules": ["^.{0,3}$", "exec\\("]
}
```

## CLI 参数

```
maxwell --help

Options:
  --mode          server / simulate
  --host          绑定地址 (default: 0.0.0.0)
  --port          绑定端口 (default: 8080)
  --workers       漏斗 worker 数量 (default: 2)
  --model-params  模型参数量 (default: 7B)
  --max-seq       最大序列长度 (default: 8192)
  --entropy-low   低熵阈值 (default: 1.0)
  --entropy-high  高熵阈值 (default: 4.5)
  --config        规则配置路径 (default: rules.json)
  --verbose       调试日志
```

## 智能合约

`contracts/MaxwellSettlement.sol` 实现了：

- **Provider 注册**: 质押保证金 + 设定 FLOPs 单价
- **Consumer 充值**: 预存计算费用
- **按 FLOPs 结算**: `cost = flops × price_per_petaflop / 1e15`
- **争议机制**: 1 小时挑战窗口期
- **协议费**: 0.5% 平台抽成

## 测试

```bash
pytest -v              # 全部测试
mypy maxwell/          # 类型检查
```

## 技术栈

- **核心**: Python 3.10+ / asyncio
- **算法**: `bitarray` + `mmh3` (Bloom Filter) · `numpy` (信息熵)
- **网络**: `aiohttp` (异步 HTTP)
- **CLI**: `typer` + `rich` (实时仪表盘)
- **合约**: Solidity ^0.8.20
- **部署**: Docker multi-stage · non-root

## License

[MIT](LICENSE)
