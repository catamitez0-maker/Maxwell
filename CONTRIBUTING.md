# Contributing to Maxwell Protocol / 参与贡献 Maxwell Protocol

[English](#english) | [中文](#chinese)

---

<a id="english"></a>
## 🇬🇧 English

Thank you for your interest in Maxwell Protocol! We welcome all forms of contribution.

### Development Environment

```bash
# Clone & setup
git clone https://github.com/catamitez0-maker/Maxwell.git
cd Maxwell
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Code Standards

- **Type Hints**: All functions must have complete type signatures (must pass `mypy --strict`).
- **Async**: Blocking operations within the `asyncio` event loop are strictly prohibited.
- **Documentation**: All public APIs must include docstrings.

### Testing

```bash
# Run all tests
pytest -v

# Type checking
mypy maxwell/
```

### Submit a PR

1. Fork this repository
2. Create your feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m "feat: description"`
4. Push to the branch: `git push origin feature/your-feature`
5. Open a Pull Request

### Commit Message Format

Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation
- `test:` — Testing
- `refactor:` — Code refactoring
- `perf:` — Performance optimization

### Project Structure

```
maxwell/
├── filters.py     # L1-L3, L5 filtering algorithms
├── proxy.py       # Async funnel engine & streaming logic
├── oracle.py      # FLOPs estimation & dynamic budget metering
├── api.py         # HTTP API service & StreamingResponse
├── cli.py         # CLI entrypoint & load simulator
├── dashboard.py   # Rich real-time dashboard
└── models.py      # Data models

contracts/
└── MaxwellSettlement.sol  # Settlement smart contract

tests/
├── test_filters.py
├── test_proxy.py
└── test_oracle.py
```

---

<a id="chinese"></a>
## 🇨🇳 中文

感谢你对 Maxwell Protocol 的关注！我们欢迎所有形式的贡献。

### 开发环境

```bash
# Clone & setup
git clone https://github.com/catamitez0-maker/Maxwell.git
cd Maxwell
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 代码规范

- **Type Hints**: 所有函数必须标注完整的类型签名（`mypy --strict` 通过）
- **Async**: 严禁在 asyncio 事件循环中使用阻塞操作
- **文档**: 所有公共 API 必须包含 docstring

### 测试

```bash
# 运行全部测试
pytest -v

# 类型检查
mypy maxwell/
```

### 提交 PR

1. Fork 本仓库
2. 创建特性分支: `git checkout -b feature/your-feature`
3. 提交更改: `git commit -m "feat: description"`
4. 推送分支: `git push origin feature/your-feature`
5. 创建 Pull Request

### 提交信息格式

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范:

- `feat:` — 新功能
- `fix:` — 修复
- `docs:` — 文档
- `test:` — 测试
- `refactor:` — 重构
- `perf:` — 性能优化

### 项目结构

```
maxwell/
├── filters.py     # L1-L3, L5 过滤算法
├── proxy.py       # 异步漏斗引擎与流式生成逻辑
├── oracle.py      # FLOPs 算力预估与动态预算拦截
├── api.py         # HTTP API 服务与流式响应
├── cli.py         # CLI 入口与高并发模拟器
├── dashboard.py   # Rich 实时仪表盘
└── models.py      # 数据模型

contracts/
└── MaxwellSettlement.sol  # 结算智能合约

tests/
├── test_filters.py
├── test_proxy.py
└── test_oracle.py
```
