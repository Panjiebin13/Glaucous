# Glaucous

> 雨过天青，海鸥滑翔，代码自有清凉。

一个清爽、可控的 CLI 编程智能体：默认以 Plan 模式（只读）探索需求并产出方案，
经用户授权后切换 Build 模式执行修改；全程在工作区边界与分级审批管控之下。

当前处于 M0 原型阶段（Day 1）：LLM 客户端 + 三个只读工具 + 主循环 + 简版 CLI。

## 快速开始（Day 1 原型）

```bash
pip install -e .
export GLAUCOUS_API_KEY=sk-****
glaucous --workspace /path/to/project
```

配置项（环境变量）：

| 变量 | 说明 | 默认 |
|------|------|------|
| `GLAUCOUS_API_KEY` | API 密钥（必填） | — |
| `GLAUCOUS_BASE_URL` | OpenAI 兼容网关 | `https://api.deepseek.com/v1` |
| `GLAUCOUS_MODEL` | 模型名 | `deepseek-v4-flash` |
| `GLAUCOUS_TEMPERATURE` | 采样温度 | `0.2` |
| `GLAUCOUS_MAX_STEPS` | 主循环步数上限 | `50` |

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## 相关文档

- [编程智能体需求文档](docs/编程智能体需求文档.md)
- [编程智能体概要设计说明书](docs/编程智能体概要设计说明书.md)
- [Glaucous开发计划表](docs/Glaucous开发计划表.md)
- [Day 1 技术设计方案](docs/designs/202608270900-plan-m0-day1-prototype.md)
