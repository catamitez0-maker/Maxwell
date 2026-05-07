# Contributing to Maxwell Protocol

感谢你对 Maxwell Protocol 的关注！我们欢迎所有形式的贡献。

## 开发环境

```bash
# Clone & setup
git clone https://github.com/YOUR_USERNAME/Maxwell.git
cd Maxwell
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 代码规范

- **Type Hints**: 所有函数必须标注完整的类型签名（`mypy --strict` 通过）
- **Async**: 严禁在 asyncio 事件循环中使用阻塞操作
- **文档**: 所有公共 API 必须包含 docstring

## 测试

```bash
# 运行全部测试
pytest -v

# 类型检查
mypy maxwell/
```

## 提交 PR

1. Fork 本仓库
2. 创建特性分支: `git checkout -b feature/your-feature`
3. 提交更改: `git commit -m "feat: description"`
4. 推送分支: `git push origin feature/your-feature`
5. 创建 Pull Request

## 提交信息格式

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范:

- `feat:` — 新功能
- `fix:` — 修复
- `docs:` — 文档
- `test:` — 测试
- `refactor:` — 重构
- `perf:` — 性能优化

## 项目结构

```
maxwell/
├── filters.py     # L1-L3, L5 过滤算法
├── proxy.py       # 异步漏斗引擎
├── oracle.py      # FLOPs 算力预估
├── api.py         # HTTP API 服务
├── cli.py         # CLI 入口
├── dashboard.py   # Rich 实时仪表盘
└── models.py      # 数据模型

contracts/
└── MaxwellSettlement.sol  # 结算智能合约

tests/
├── test_filters.py
├── test_proxy.py
└── test_oracle.py
```
