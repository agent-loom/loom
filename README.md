# Agent Platform

生产 + 开发一体化的多业务 Agent 平台。

## MVP

当前 MVP 聚焦：

- Agent Manifest v1
- Agent Registry / Router
- Native RuntimeBackend
- `POST /api/v1/agent/chat`
- Eval Runner
- DevFlow Task Pack
- Plane / GitLab adapter 骨架
- `myj` demo Agent Package

## 本地开发

```bash
uv sync --extra dev
uv run pytest
uv run python scripts/smoke_test.py
```

启动 API：

```bash
uv run uvicorn agent_platform.api.app:app --reload
```

## 文档

从 [docs/README.md](docs/README.md) 开始阅读。
