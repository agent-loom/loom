# Agent Platform

生产 + 开发一体化的多业务 Agent 平台。支持多 Agent、统一路由、Hermes 真实 SDK 接入、DevFlow AI 研发自动化（Plane → GitLab → Codex → MR 全闭环）。

## 当前能力（S8 阶段）

| 能力 | 状态 |
|------|------|
| 多 Agent Package（myj / echo） | ✅ |
| Agent Manifest v1 + Registry + Router | ✅ |
| NativeRuntime / HermesRuntime / LangGraphRuntime | ✅ |
| Plane Webhook → DevFlow → GitLab 分支 / MR | ✅ |
| Codex / Claude Code Runner 真实编码 | ✅ |
| Codex 结果回写 Plane（评论 + 状态扭转） | ✅ |
| GitLab → Plane 反向状态同步 | ✅ |
| RBAC + API Key + 多租户隔离 | ✅ |
| Prometheus metrics / OTel tracing / Langfuse | ✅ |
| Knowledge / RAG（Weaviate） | ✅ |
| WebSocket streaming + HITL 审批 | ✅ |
| Admin API（9 端点 + eval + audit） | ✅ |
| Alembic 数据库迁移 | ✅ |

## 本地开发

```bash
uv sync --extra dev
uv run pytest            # 1609 passed
```

启动 API（自动加载 .env）：

```bash
uv run uvicorn agent_platform.api.app:create_app --factory \
  --host 0.0.0.0 --port 8000 --log-level info --env-file .env
```

端到端验证（无需真实外部依赖）：

```bash
uv run python scripts/devflow_e2e_test.py    # mock E2E，6 场景 23 断言
uv run python scripts/devflow_real_e2e.py    # 真实 Plane + GitLab
```

## 关键环境变量（.env）

| 变量 | 说明 |
|------|------|
| `PLANE_BASE_URL` | Plane 实例地址（不含 /api，如 `http://10.x.x.x:3333`） |
| `PLANE_API_KEY` | Plane API 密钥 |
| `PLANE_WEBHOOK_SECRET` | Plane → 平台 webhook 签名密钥 |
| `GITLAB_BASE_URL` | GitLab 实例地址 |
| `GITLAB_TOKEN` | GitLab Personal Access Token |
| `GITLAB_WEBHOOK_SECRET` | GitLab → 平台 webhook 签名密钥 |
| `GITLAB_PROJECT_ID` | 目标 GitLab 项目 ID |
| `DEVFLOW_REPO_URL` | 含凭证的 git clone URL |
| `DEVFLOW_DEFAULT_BRANCH` | 默认分支（如 `master`） |
| `DEVFLOW_RUNNER_ADAPTER` | `mock` / `codex` / `claude_code` |
| `DEVFLOW_SANDBOX_MODE` | Codex 沙箱模式：`bypass` (默认) 或 `docker`。**⚠️ 生产环境必须使用 docker 模式，否则有严重的进程逃逸风险** |
| `PLANE_AI_DEVELOPING_STATE_ID` | Plane "AI Developing" 状态 ID |
| `PLANE_TESTING_STATE_ID` | Plane "Testing" 状态 ID |

## DevFlow 状态流

```
Plane: Ready for AI Dev
  → 平台收 webhook → 创建 GitLab 分支 + MR
  → Codex 自动编码
    → 成功 → Plane 评论 ✅ + 状态 Testing
    → 失败 → Plane 评论 ❌ + 状态回退 AI Developing

GitLab MR merged → Plane 状态 Staging/Done
GitLab Pipeline success → Plane 状态 Human Review
```

## GitLab 反向 Webhook 配置

在 GitLab 项目 Settings → Webhooks 添加：

- **URL**: `http://<your-mac-ip>:8000/api/v1/integrations/gitlab/webhook`
- **Secret token**: 见 `.env` 中的 `GITLAB_WEBHOOK_SECRET`
- **触发事件**: Merge request events + Pipeline events

## 文档

从 [docs/README.md](docs/README.md) 开始阅读。
