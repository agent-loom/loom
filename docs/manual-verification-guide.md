# 手动验证指南

> Status: Baseline
> Stage: S5
> Last verified against code: 2026-05-17

本文档覆盖平台所有可手动验证的功能模块。所有核心模块**无需外部服务**，直接启动即可验证。

## 目录

- [0. 环境准备与启动](#0-环境准备与启动)
- [1. Agent 注册与发现](#1-agent-注册与发现)
- [2. Agent Chat 对话](#2-agent-chat-对话)
- [3. 多轮会话](#3-多轮会话)
- [4. 部署、回滚与审计](#4-部署回滚与审计)
- [5. Eval 执行](#5-eval-执行)
- [6. Artifact 打包](#6-artifact-打包)
- [7. Admin API](#7-admin-api)
- [8. HITL 审批](#8-hitl-审批)
- [9. API 鉴权](#9-api-鉴权)
- [10. Metrics 指标](#10-metrics-指标)
- [11. Manifest 校验](#11-manifest-校验)
- [12. Smoke Test](#12-smoke-test)
- [13. SQLite 持久化](#13-sqlite-持久化)
- [14. WebSocket 对话](#14-websocket-对话)
- [15. SSE 流式输出](#15-sse-流式输出)
- [16. Swagger UI](#16-swagger-ui)
- [17. DevFlow 真实 Coding Runner E2E](#17-devflow-真实-coding-runner-e2e)
- [18. DevFlow Webhook 驱动真实环境验证](#18-devflow-webhook-驱动真实环境验证)
- [附录 A：环境变量速查](#附录-a环境变量速查)
- [附录 B：已注册 Agent 速查](#附录-b已注册-agent-速查)
- [附录 C：验证检查清单](#附录-c验证检查清单)

---

## 0. 环境准备与启动

### 前置条件

```bash
# 确认 Python 虚拟环境就绪
.venv/bin/python --version

# 确认依赖已安装
.venv/bin/python -c "import agent_platform; print('OK')"

# 确认测试基线
.venv/bin/pytest -q          # 预期 670 passed
.venv/bin/ruff check src/    # 预期 All checks passed
```

### 最简启动

```bash
uv run uvicorn agent_platform.api.app:app --reload --port 8000
```

启动后自动完成：

1. 扫描 `agents/` 目录下的 `manifest.yaml`
2. 注册 4 个 agent：`echo`、`hermes_echo`、`myj`、`promo_recommendation`
3. 启用 stub model provider
4. 所有 Repository 使用 in-memory 实现

### 验证启动成功

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

预期输出：

```json
{"status": "ok"}
```

---

## 1. Agent 注册与发现

```bash
curl -s http://localhost:8000/api/v1/agents | python -m json.tool
```

预期：返回 4 个 agent 的摘要信息（id、version、name、backend）。

验证点：

- [ ] 返回数组长度 = 4
- [ ] 每个 agent 都有 `agent_id`、`version`、`name`
- [ ] backend 分别为 `native`（echo, myj, promo_recommendation）和 `hermes`（hermes_echo）

---

## 2. Agent Chat 对话

### 2.1 Echo Agent（最简链路）

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "echo",
    "input": {"query": "hello world"}
  }' | python -m json.tool
```

验证点：

- [ ] `response.output.text.display` 包含 `"Echo: hello world"`
- [ ] `response.trace` 存在且有 `run_id`
- [ ] 返回有 `session_id`

### 2.2 Hermes Agent（stub fallback 路径）

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "hermes_echo",
    "input": {"query": "你好"}
  }' | python -m json.tool
```

验证点：

- [ ] 未安装 `hermes_agent` SDK 时：output 包含 `"[Hermes-stub] Received: 你好"`
- [ ] 已安装 SDK 时：走真实 SDK 路径，output 不含 `[Hermes-stub]`
- [ ] 无论哪条路径都不报错

### 2.3 MYJ Agent（关键词工具路由）

MYJ agent 使用 `orchestrator_workers` 模式，根据关键词匹配路由到不同工具。

**商品搜索（关键词：商品/饮料/推荐/搜索/找）：**

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "myj",
    "input": {"query": "有什么饮料推荐"},
    "context": {"tenant": {"retailer_id": "test_retailer"}}
  }' | python -m json.tool
```

验证点：

- [ ] `response.trace.tool_calls` 非空
- [ ] tool_calls 包含 `myj.goods_search`

**货架位置（关键词：在哪/位置/货架/哪里）：**

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "myj",
    "input": {"query": "可乐在哪里"},
    "context": {"tenant": {"retailer_id": "test_retailer"}}
  }' | python -m json.tool
```

验证点：

- [ ] tool_calls 包含 `myj.goods_location`

**优惠促销（关键词：优惠/促销/打折/活动/会员）：**

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "myj",
    "input": {"query": "有什么优惠活动"},
    "context": {"tenant": {"retailer_id": "test_retailer"}}
  }' | python -m json.tool
```

验证点：

- [ ] tool_calls 包含 `myj.promotion_lookup`

**直接回复（无关键词命中）：**

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "myj",
    "input": {"query": "你好"},
    "context": {"tenant": {"retailer_id": "test_retailer"}}
  }' | python -m json.tool
```

验证点：

- [ ] 走 `direct_reply` worker
- [ ] `trace.tool_calls` 为空数组

---

## 3. 多轮会话

```bash
# 第一轮 —— 记下返回的 session_id
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "echo", "input": {"query": "第一轮"}}' | python -m json.tool

# 第二轮 —— 传入上面拿到的 session_id
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "echo",
    "session_id": "<替换为第一轮返回的 session_id>",
    "input": {"query": "第二轮"}
  }' | python -m json.tool

# 查看 session 详情
curl -s http://localhost:8000/api/v1/sessions/<session_id> | python -m json.tool
```

验证点：

- [ ] 两次请求返回相同 `session_id`
- [ ] session 详情中 `messages` 数组有 4 条（每轮 user + assistant 各 1 条）
- [ ] session 列表可查

```bash
curl -s http://localhost:8000/api/v1/sessions | python -m json.tool
```

---

## 4. 部署、回滚与审计

### 4.1 部署到 staging

```bash
curl -s -X POST http://localhost:8000/api/v1/agent-packages/echo/versions/0.1.0/deploy \
  -H "Content-Type: application/json" \
  -d '{"channel": "staging"}' | python -m json.tool
```

### 4.2 部署到 prod

```bash
curl -s -X POST http://localhost:8000/api/v1/agent-packages/echo/versions/0.1.0/deploy \
  -H "Content-Type: application/json" \
  -d '{"channel": "prod", "eval_passed": true}' | python -m json.tool
```

### 4.3 查看部署列表

```bash
curl -s http://localhost:8000/api/v1/agent-deployments | python -m json.tool
```

### 4.4 回滚

```bash
curl -s -X POST http://localhost:8000/api/v1/deployments/rollback \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "echo",
    "channel": "prod",
    "actor": "manual_test"
  }' | python -m json.tool
```

### 4.5 审计日志

```bash
curl -s "http://localhost:8000/api/v1/deployments/audit?agent_id=echo" | python -m json.tool
```

验证点：

- [ ] staging 部署成功返回 deployment_id
- [ ] prod 部署成功
- [ ] 部署列表包含新建的 deployment
- [ ] 回滚后审计日志包含 `deploy` 和 `rollback` 两类事件
- [ ] 审计事件有 `timestamp`、`actor`、`channel` 字段

---

## 5. Eval 执行

### 5.1 通过 API 运行

```bash
curl -s -X POST http://localhost:8000/api/v1/evals/run \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "echo"}' | python -m json.tool
```

验证点：

- [ ] 返回 `pass_rate`、`total`、`passed`、`gate_passed` 字段
- [ ] 有 eval case 结果明细

### 5.2 离线运行（不需要启动服务）

```bash
.venv/bin/python scripts/run_agent_eval.py --agent echo
```

验证点：

- [ ] 输出 eval 报告到终端或 JSON 文件
- [ ] 无需服务运行

---

## 6. Artifact 打包

```bash
# 打包 agent
.venv/bin/python scripts/package_agent.py --agent echo

# 查看已有 artifact（需要服务运行）
curl -s http://localhost:8000/api/v1/artifacts?agent_id=echo | python -m json.tool

# 如有 artifact，可下载
curl -s http://localhost:8000/api/v1/artifacts/<artifact_id>/download -o echo.tar.gz
file echo.tar.gz
```

验证点：

- [ ] 打包脚本生成 tar.gz 到 `dist/agents/`
- [ ] artifact metadata 包含 `manifest_sha256`、`package_sha256`
- [ ] 下载内容是合法 gzip 文件

---

## 7. Admin API

先确保已经发过几次 chat 请求，以便有数据可查。

### 7.1 系统状态总览

```bash
curl -s http://localhost:8000/api/v1/admin/status | python -m json.tool
```

验证点：

- [ ] 返回 `agent_count`、`deployment_count`、`session_count`、`run_count`
- [ ] 数值与实际操作一致

### 7.2 Agent 详情

```bash
curl -s http://localhost:8000/api/v1/admin/agents/echo | python -m json.tool
```

验证点：

- [ ] 返回完整 manifest 信息
- [ ] 包含 deployments 列表和最近 runs

### 7.3 工具列表

```bash
curl -s http://localhost:8000/api/v1/admin/tools | python -m json.tool
```

验证点：

- [ ] 列出 `myj.goods_search` 等工具
- [ ] 每个工具有 `risk_level`、`keywords`、`owner`、`timeout_ms`

### 7.4 运行记录

```bash
curl -s http://localhost:8000/api/v1/admin/runs | python -m json.tool
```

### 7.5 会话管理

```bash
# 列出
curl -s http://localhost:8000/api/v1/admin/sessions | python -m json.tool

# 删除
curl -s -X DELETE http://localhost:8000/api/v1/admin/sessions/<session_id>
```

### 7.6 Agent 删除

```bash
curl -s -X DELETE http://localhost:8000/api/v1/admin/agents/echo
# 再查询应返回 404
curl -s http://localhost:8000/api/v1/admin/agents/echo
```

> 注意：删除后重启服务会重新从 agents/ 目录扫描注册。

---

## 8. HITL 审批

### 8.1 启用 HITL 模式

```bash
HITL_ENABLED=true uv run uvicorn agent_platform.api.app:app --reload --port 8000
```

### 8.2 查看待审批列表

```bash
curl -s http://localhost:8000/api/v1/approvals/pending | python -m json.tool
# 初始为空数组
```

### 8.3 触发审批流程

当前所有内置工具的 `risk_level` 都是 `low`，审批 gate 仅对 `high` / `critical` 级别生效。要验证完整审批流程，可临时修改工具风险等级：

```python
# 修改 agents/myj/tools/__init__.py 中某个工具的 risk_level
# 例如将 goods_search 改为 risk_level="high"
```

修改后重启服务，再发送匹配该工具的 chat 请求：

```bash
# 发送触发 high risk 工具的请求
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "myj",
    "input": {"query": "有什么饮料推荐"},
    "context": {"tenant": {"retailer_id": "test_retailer"}}
  }' | python -m json.tool

# 查看 pending 审批
curl -s http://localhost:8000/api/v1/approvals/pending | python -m json.tool

# 审批通过
curl -s -X POST http://localhost:8000/api/v1/approvals/<request_id>/resolve \
  -H "Content-Type: application/json" \
  -d '{"status": "approved", "actor": "test_admin"}' | python -m json.tool

# 审批拒绝
curl -s -X POST http://localhost:8000/api/v1/approvals/<request_id>/resolve \
  -H "Content-Type: application/json" \
  -d '{"status": "rejected", "actor": "test_admin"}' | python -m json.tool
```

验证点：

- [ ] HITL 关闭时（默认）：high risk 工具直接执行（AutoApproveGate）
- [ ] HITL 开启时：high risk 工具触发审批，trace 中显示 `APPROVAL_DENIED` 或 `APPROVAL_EXPIRED`
- [ ] `resolve` 后状态正确更新

> 验证完记得恢复 risk_level 为 `low`。

---

## 9. API 鉴权

### 9.1 启用鉴权

```bash
AGENT_PLATFORM_API_KEY=my-secret-key uv run uvicorn agent_platform.api.app:app --reload --port 8000
```

### 9.2 验证

```bash
# 无 key → 401
curl -s -w "\nHTTP %{http_code}\n" http://localhost:8000/api/v1/agents

# Bearer token → 200
curl -s -H "Authorization: Bearer my-secret-key" http://localhost:8000/api/v1/agents | python -m json.tool

# x-api-key header → 200
curl -s -H "x-api-key: my-secret-key" http://localhost:8000/api/v1/agents | python -m json.tool

# /health 不需要 key → 200
curl -s http://localhost:8000/health

# /docs 不需要 key → 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs
```

验证点：

- [ ] 受保护端点无 key 返回 401
- [ ] `Authorization: Bearer` 和 `x-api-key` 两种方式都可认证
- [ ] `/health`、`/docs`、`/redoc`、`/openapi.json` 不受保护

---

## 10. Metrics 指标

```bash
# 先发几次 chat 请求产生数据，再查看指标
curl -s http://localhost:8000/metrics
```

验证点：

- [ ] 输出 Prometheus text format
- [ ] 包含 `agent_requests_total` 计数器
- [ ] 包含 `agent_request_duration_seconds` 
- [ ] 包含 `tool_calls_total`（如果触发过工具）
- [ ] 发送更多请求后计数器递增

---

## 11. Manifest 校验

```bash
# 校验所有 agent manifest
.venv/bin/python scripts/validate_manifest.py

# 校验单个
.venv/bin/python scripts/validate_manifest.py agents/myj/manifest.yaml
```

验证点：

- [ ] 所有 manifest 校验通过
- [ ] 错误 manifest 能报出具体错误位置

---

## 12. Smoke Test

```bash
# 不需要启动服务，脚本内部使用 TestClient
.venv/bin/python scripts/smoke_test.py
```

验证点：

- [ ] 脚本正常退出，无异常
- [ ] 覆盖 health check + agent chat 基础链路

---

## 13. SQLite 持久化

### 13.1 使用 SQLite 启动

```bash
DATABASE_URL="sqlite+aiosqlite:///./test_manual.db" \
  uv run uvicorn agent_platform.api.app:app --port 8000
```

### 13.2 验证数据持久化

```bash
# 1. 发送 chat 请求
curl -s -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "echo", "input": {"query": "persist test"}}' | python -m json.tool

# 2. 记下 session_id 和 run_id

# 3. Ctrl+C 停掉服务

# 4. 重新启动（相同 DATABASE_URL）
DATABASE_URL="sqlite+aiosqlite:///./test_manual.db" \
  uv run uvicorn agent_platform.api.app:app --port 8000

# 5. 查看 session 是否还在
curl -s http://localhost:8000/api/v1/sessions | python -m json.tool

# 6. 查看 run 是否还在
curl -s http://localhost:8000/api/v1/agent-runs | python -m json.tool
```

验证点：

- [ ] 重启后 session 数据仍然存在
- [ ] 重启后 run 记录仍然存在
- [ ] `test_manual.db` 文件大小 > 0

### 13.3 Alembic Migration

```bash
# 需要在 alembic.ini 中设置 sqlalchemy.url，或手动指定
# 从空库建表
.venv/bin/alembic upgrade head
```

验证点：

- [ ] migration 成功创建 7 张表
- [ ] `alembic current` 显示最新 revision

### 13.4 清理

```bash
rm -f test_manual.db
```

---

## 14. WebSocket 对话

需要 WebSocket 客户端工具（任选一个）：

```bash
# 方式 1: wscat (npm)
npm install -g wscat
wscat -c ws://localhost:8000/ws/agent/chat

# 方式 2: websocat
brew install websocat
websocat ws://localhost:8000/ws/agent/chat

# 方式 3: Python
.venv/bin/python -c "
import asyncio, json, websockets
async def test():
    async with websockets.connect('ws://localhost:8000/ws/agent/chat') as ws:
        await ws.send(json.dumps({
            'agent_id': 'echo',
            'input': {'query': 'websocket test'}
        }))
        resp = await ws.recv()
        print(json.dumps(json.loads(resp), indent=2, ensure_ascii=False))
asyncio.run(test())
"
```

验证点：

- [ ] WebSocket 连接成功建立
- [ ] 发送 JSON 后收到响应
- [ ] 响应包含 `output.text.display`

---

## 15. SSE 流式输出

```bash
curl -N -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "echo",
    "input": {"query": "stream test"},
    "options": {"stream": true}
  }'
```

验证点：

- [ ] 响应以 `text/event-stream` 格式返回
- [ ] 收到 `data:` 开头的事件行

---

## 16. Swagger UI

浏览器打开：

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

验证点：

- [ ] 页面正常加载
- [ ] 所有端点都有描述
- [ ] 可在页面上直接发送请求测试

---

## 17. DevFlow 真实 Coding Runner E2E

用途：验证 `Plane -> GitLab -> Coding Runner -> validation -> commit/push -> GitLab/Plane 回写` 完整链路。

前置条件：

- Plane 实例可访问，且 `.env` 中配置了 `PLANE_BASE_URL`、`PLANE_API_KEY`、`PLANE_WORKSPACE_SLUG`、`PLANE_PROJECT_ID`。
- GitLab 实例可访问，且 `.env` 中配置了 `GITLAB_BASE_URL`、`GITLAB_TOKEN`、`GITLAB_PROJECT_ID`。
- `DEVFLOW_REPO_URL` 指向可 clone/push 的 GitLab 仓库地址。
- `DEVFLOW_DEFAULT_BRANCH` 或 `GITLAB_DEFAULT_BRANCH` 与仓库默认分支一致；当前联调仓库使用 `master`。
- `DEVFLOW_RUNNER_ADAPTER=codex` 时，本机 `codex --version` 可用且已登录。

推荐命令：

```bash
.venv/bin/python scripts/devflow_real_e2e.py
```

可选环境变量：

| 环境变量 | 用途 |
|---|---|
| `DEVFLOW_RUNNER_ADAPTER` | `mock` / `codex` / `claude_code` |
| `DEVFLOW_REPO_URL` | Runner clone/push 的仓库地址 |
| `DEVFLOW_WORKSPACE_BASE_DIR` | workspace 根目录 |
| `DEVFLOW_CLEANUP_ON_SUCCESS` | 成功后是否清理 workspace，排查时建议 `false` |
| `DEVFLOW_TEST_AGENT_ID` | 测试目标 agent，默认 `echo` |
| `DEVFLOW_TEST_TASK_TYPE` | 测试任务类型，默认 `agent:change` |
| `DEVFLOW_TEST_REQUIREMENT` | 覆盖默认测试需求 |

验证点：

- [ ] Plane API 可达
- [ ] GitLab API 可达
- [ ] Plane Work Item 创建成功
- [ ] GitLab feature branch 创建成功
- [ ] GitLab MR 创建成功
- [ ] Coding Runner job 状态为 `SUCCEEDED`
- [ ] `job.result.status == success`
- [ ] `job.result.commit_sha` 非空
- [ ] GitLab MR 有 Runner 报告评论
- [ ] Plane Work Item 有 MR 和 Runner 结果评论

2026-05-18 验证记录：

| 项 | 值 |
|---|---|
| Runner | `codex` |
| 结果 | `13 passed, 0 failed` |
| GitLab MR | `!11` |
| Runner commit | `3d7d6a99dac657bc4987b8891ab839d5cac8f650` |

常见失败和处理：

| 失败 | 原因 | 处理 |
|---|---|---|
| `target branch main is missing` | GitLab 默认分支不是 `main` | 设置 `DEVFLOW_DEFAULT_BRANCH=master` 或 `GITLAB_DEFAULT_BRANCH=master` |
| `path_violation` | TaskPack scope 未覆盖真实 changed files | 检查 `write_allowed`，必要时补充 `pyproject.toml`、`uv.lock`、`eval-report.json` |
| `No such file or directory: pytest` | clean env 下 PATH 找不到 pytest | 当前代码已解析为 `sys.executable -m pytest`；旧版本需升级 |
| Codex 卡住或无输出 | Codex CLI 登录、网络或 app-server 初始化问题 | 先在普通终端运行 `codex exec ...` 验证本机环境 |

---

## 18. DevFlow Webhook 驱动真实环境验证

用途：通过 Plane 改变工单状态触发真实 webhook，逐步确认每个环节正常工作。与第 17 节不同，此节是**手动逐步验证**，适合排查问题或首次接入新环境。

**总时长约 15-30 分钟。**

### 18.0 前置检查

```bash
# 确认平台已启动
curl -s http://localhost:8000/health | python3 -m json.tool

# 确认环境变量完整（以下全部应有值）
grep -E "PLANE_BASE_URL|PLANE_API_KEY|PLANE_PROJECT_ID|PLANE_WEBHOOK_SECRET|\
GITLAB_BASE_URL|GITLAB_TOKEN|GITLAB_PROJECT_ID|GITLAB_WEBHOOK_SECRET|\
DEVFLOW_REPO_URL|DEVFLOW_RUNNER_ADAPTER|DEVFLOW_DEFAULT_BRANCH|\
PLANE_READY_FOR_AI_DEV_STATE_ID|PLANE_AI_DEVELOPING_STATE_ID" .env
```

验证点：
- [ ] `curl /health` 返回 `{"status": "ok"}`
- [ ] 所有必要环境变量均有值（非空）
- [ ] `DEVFLOW_RUNNER_ADAPTER` 已设置（`mock` / `codex` / `claude_code`）

### 18.1 确认平台接收 Plane Webhook

**步骤：**

1. 在 Plane 创建一个新 Work Item（任何标题均可，如「测试 DevFlow 18.1」）
2. 把 Work Item 状态改为 **Ready for AI Dev**
3. 观察平台日志：

```bash
# 实时跟踪平台日志（另一个终端）
tail -f server.log | grep -E "webhook|devflow|owner|branch"
```

验证点：
- [ ] 日志出现 `Plane webhook received` 或类似记录
- [ ] 日志中能看到 work_item_id（GUID 格式）
- [ ] 无 `401 Unauthorized`（说明 webhook secret 正确）
- [ ] 无 `422 Unprocessable`（说明 payload 解析正常）

**常见失败：**

| 现象 | 原因 | 处理 |
|---|---|---|
| 无日志、Plane 回调超时 | 平台未暴露到公网，Plane 无法回调 | 用 `ngrok http 8000` 并更新 Plane webhook URL |
| `401 Unauthorized` | `PLANE_WEBHOOK_SECRET` 与 Plane 配置不匹配 | 重新在 Plane webhook 设置页面获取 secret |
| Plane 状态改变但无 webhook | Plane webhook 未配置或未启用 | 检查 Plane 项目设置 → Webhooks，确认已启用 Work Item 事件 |

### 18.2 确认 Agent Owner 解析

Owner 解析是决定哪个 agent 负责工单的核心步骤，4 种策略按优先级依次尝试。

**验证方式：**

```bash
# 观察日志中的 owner 解析结果
tail -f server.log | grep -E "owner|agent_id|resolve"
```

期望看到类似：
```
DevFlowOrchestrator: owner resolved agent_id=echo strategy=project_mapping work_item=<id>
```

**4 种 Owner 解析策略：**

| 优先级 | 策略 | 触发条件 | 示例 |
|---|---|---|---|
| 1 | explicit | Work Item `custom_properties.agent_id` 有值 | 直接指定 agent |
| 2 | project_mapping | `agent_ownership.yaml` 中 `project_id` 匹配 | 按 Plane 项目归属 |
| 3 | label_mapping | Work Item 标签与 `agent_ownership.yaml` 中 `labels` 匹配 | 按标签分派 |
| 4 | keyword_mapping | Work Item 标题中含配置的关键词 | 按关键词匹配 |

**配置示例（`config/agent_ownership.yaml`）：**

```yaml
agents:
  echo:
    project_mappings:
      - project_id: "ab49f9f8-be43-4923-8a6b-0f49f682719d"
    label_mappings:
      - label: "agent:echo"
    keyword_mappings:
      - keyword: "echo"
        case_sensitive: false
```

验证点：
- [ ] 日志中能看到 `owner resolved agent_id=<your_agent>`
- [ ] 日志中显示使用的 strategy（explicit/project/label/keyword）
- [ ] 无 `no owner found` 日志（否则工单将被跳过）

**若 owner 未解析成功：**

```bash
# 临时验证：直接调用 owner 解析（需启动平台，通过 API 触发）
# 或在 Work Item custom_properties 中手动设置 agent_id=echo
```

### 18.3 确认分支创建

Owner 解析成功后，Orchestrator 应立即在 GitLab 创建 feature branch。

**验证：**

```bash
# 查看 GitLab API 确认分支已创建
curl -s -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_BASE_URL/api/v4/projects/$GITLAB_PROJECT_ID/repository/branches" \
  | python3 -m json.tool | grep '"name"' | head -10
```

期望：看到形如 `devflow/<work_item_id>-<slug>` 的新分支。

同时确认 Plane Work Item 状态已变为 **AI Developing**：

```bash
# 查看 Plane work item 当前状态（需 Plane API）
curl -s -H "X-API-Key: $PLANE_API_KEY" \
  "$PLANE_BASE_URL/api/v1/workspaces/$PLANE_WORKSPACE_SLUG/projects/$PLANE_PROJECT_ID/issues/<work_item_id>/" \
  | python3 -m json.tool | grep '"state"'
```

验证点：
- [ ] GitLab 分支已创建（格式 `devflow/...`）
- [ ] Plane Work Item 状态变为 AI Developing
- [ ] Plane 有评论「分支已创建 — `<branch_name>`」

**常见失败：**

| 现象 | 原因 | 处理 |
|---|---|---|
| `Branch already exists` | 同一 work item 重复触发 | 正常，平台会复用已有分支 |
| `GITLAB_PROJECT_ID` 相关错误 | Project ID 不正确 | 在 GitLab 项目首页获取数字 ID（如 `12556`） |
| 分支创建成功但 Plane 未变状态 | `PLANE_AI_DEVELOPING_STATE_ID` 配置错误 | 从 Plane 状态列表 API 获取正确 ID |

### 18.4 确认 Runner 执行

分支创建后，Runner 会被异步派发执行编码任务。

**查看 Job 状态：**

```bash
# 列出所有 DevFlow jobs
curl -s -H "Authorization: Bearer $AGENT_PLATFORM_API_KEY" \
  http://localhost:8000/api/v1/devflow/jobs | python3 -m json.tool

# 查看特定 job 详情（替换 <job_id>）
curl -s -H "Authorization: Bearer $AGENT_PLATFORM_API_KEY" \
  http://localhost:8000/api/v1/devflow/jobs/<job_id> | python3 -m json.tool
```

**查看 Runner 执行日志：**

```bash
# 查看 job stdout/stderr（替换 <job_id>）
curl -s -H "Authorization: Bearer $AGENT_PLATFORM_API_KEY" \
  "http://localhost:8000/api/v1/devflow/jobs/<job_id>/logs" | python3 -m json.tool

# 只看 stderr（错误输出）
curl -s -H "Authorization: Bearer $AGENT_PLATFORM_API_KEY" \
  "http://localhost:8000/api/v1/devflow/jobs/<job_id>/logs?stream=stderr" \
  | python3 -m json.tool
```

**Job 状态流转：**

```
PENDING → WORKSPACE_CREATING → RUNNING → VALIDATING → COMMITTING → SUCCEEDED
                                                                  ↓ (失败时)
                                                               FAILED / TIMED_OUT
```

验证点：
- [ ] Job 状态最终为 `SUCCEEDED`（mock adapter 约 3s；真实 runner 约 2-5min）
- [ ] `job.result.commit_sha` 非空
- [ ] `job.result.status == "success"`

**常见失败：**

| Job 状态 | 原因 | 处理 |
|---|---|---|
| `FAILED` + `path_violation` | 编码结果修改了 PathGuard 禁止的文件 | 检查 `devflow-runner-workspace-design.md` PathGuard 配置 |
| `FAILED` + `validation_failed` | `pytest` 或 `ruff` 验证未通过 | 查看 job logs stderr |
| `TIMED_OUT` | Runner 超出 600s 限制 | `claude_code` 大任务正常，考虑拆分需求 |
| 无 Job 记录 | runner 派发未触发 | 查看平台日志中 `_dispatch_runner` 相关输出 |

### 18.5 确认 Commit 和 MR 创建

Runner 成功提交代码（`COMMITTING` 状态）后，会自动创建 GitLab MR。

**验证 Commit：**

```bash
# 查看分支最新 commit
curl -s -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_BASE_URL/api/v4/projects/$GITLAB_PROJECT_ID/repository/branches/<branch_name>" \
  | python3 -m json.tool | grep -A5 '"commit"'
```

**验证 MR：**

```bash
# 查看该分支的 Open MR
curl -s -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_BASE_URL/api/v4/projects/$GITLAB_PROJECT_ID/merge_requests?state=opened&source_branch=<branch_name>" \
  | python3 -m json.tool | grep -E '"iid"|"title"|"web_url"'
```

验证点：
- [ ] GitLab 分支有新 commit（commit message 含 `DevFlow`）
- [ ] MR 已创建（非空 Draft 状态，含真实 commit）
- [ ] MR description 含 `<!-- devflow:plane_project_id=... -->` 元数据注释
- [ ] GitLab MR 有平台自动添加的 Runner 结果评论

### 18.6 确认 Plane 回写

MR 创建后，平台应回写 Plane Work Item。

**验证 Plane 评论：**

在 Plane 打开对应 Work Item，应看到：
1. 「分支已创建」评论（Orchestrator 阶段）
2. Runner 执行结果评论（含 commit SHA、diff 统计）
3. MR 链接评论（含 GitLab MR URL）

验证点：
- [ ] Work Item 有 3 条或以上平台自动评论
- [ ] `custom_properties.gitlab_branch` 已更新
- [ ] `custom_properties.gitlab_mr_url` 已更新（由 Runner 在 MR 创建后写入）

### 18.7 GitLab → Plane 反向同步验证

GitLab CI Pipeline 和 MR 状态变化会反向同步到 Plane。

**触发条件：** GitLab 收到 Pipeline 事件或 MR 合并/关闭事件后，通过 webhook 回调平台。

**确认 GitLab webhook 已配置：**

在 GitLab 项目 Settings → Webhooks，应有：
- URL: `http://<平台地址>/api/v1/integrations/gitlab/webhook`
- Secret Token: 与 `GITLAB_WEBHOOK_SECRET` 一致
- 事件勾选: `Pipeline events`、`Merge request events`

**状态映射：**

| GitLab 事件 | Plane 状态变化 |
|---|---|
| Pipeline running | → Testing |
| Pipeline success | → Human Review |
| Pipeline failed | → AI Developing（触发重试逻辑） |
| MR merged | → Staging |
| MR closed | → AI Developing |

验证点：
- [ ] GitLab Pipeline 执行时 Plane 状态变为 Testing
- [ ] Pipeline 成功后 Plane 状态变为 Human Review
- [ ] MR 合并后 Plane 状态变为 Staging

**手动测试反向 webhook（模拟 Pipeline 成功）：**

```bash
# 模拟 GitLab Pipeline success 事件（替换 project_id 和 work_item_id）
curl -s -X POST http://localhost:8000/api/v1/integrations/gitlab/webhook \
  -H "Content-Type: application/json" \
  -H "X-Gitlab-Token: $GITLAB_WEBHOOK_SECRET" \
  -H "X-Gitlab-Event: Pipeline Hook" \
  -d '{
    "object_kind": "pipeline",
    "object_attributes": {"status": "success", "ref": "devflow/test-branch", "id": 999},
    "variables": [
      {"key": "PLANE_PROJECT_ID", "value": "<your_plane_project_id>"},
      {"key": "PLANE_WORK_ITEM_ID", "value": "<your_work_item_id>"}
    ]
  }'
```

期望响应：`{"status": "accepted", "event": "pipeline", "sync": "queued"}`

### 18.8 查看日志排查问题

```bash
# 平台完整日志
tail -100 server.log

# 只看 DevFlow 相关
grep -E "devflow|DevFlow|orchestrator|runner|owner|branch|mr_iid" server.log | tail -50

# 只看错误
grep -E "ERROR|CRITICAL|Exception|Traceback" server.log | tail -20

# 查看 webhook 投递记录（需平台 API）
curl -s -H "Authorization: Bearer $AGENT_PLATFORM_API_KEY" \
  http://localhost:8000/api/v1/admin/status | python3 -m json.tool
```

**常见全局失败原因：**

| 现象 | 可能原因 | 排查命令 |
|---|---|---|
| Plane webhook 触发但无日志 | 平台 DevFlow 功能未启用 | 检查 `PLANE_BASE_URL` 是否在 `.env` 中设置 |
| Owner 解析失败（跳过工单） | `config/agent_ownership.yaml` 未配置，或 Plane project_id 不匹配 | 对比 `.env` 中 `PLANE_PROJECT_ID` 和 yaml 中的 `project_id` |
| Runner 未执行 | 工单 owner 未解析 / Job queue 满 / Runner adapter 异常 | 看日志 `_dispatch_runner` 输出，检查 job 列表 |
| MR 创建后无 Plane 评论 | `PLANE_PROJECT_ID` / `PLANE_WORK_ITEM_ID` 在 task pack 中丢失 | 检查 orchestrator 日志中 task_pack 内容 |
| GitLab→Plane 反向同步无效 | GitLab webhook 未配置，或 `GITLAB_WEBHOOK_SECRET` 不匹配 | 查 GitLab webhook 投递历史（Settings → Webhooks → Recent Deliveries） |

---

## 附录 A：环境变量速查

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `AGENT_PLATFORM_ENV` | `dev` | 环境标识 |
| `AGENT_PLATFORM_REGISTRY_ROOT` | `agents` | Agent manifest 扫描目录 |
| `AGENT_PLATFORM_DEFAULT_AGENT_ID` | 无 | 路由未命中时的默认 agent |
| `AGENT_PLATFORM_API_KEY` | 无 | 设置后启用 API 鉴权 |
| `DATABASE_URL` | 无（in-memory） | 设置后启用 SQLite/PostgreSQL 持久化 |
| `HITL_ENABLED` | 无（false） | 设为 `true` 启用人工审批 |
| `PLANE_BASE_URL` | 无 | Plane 集成（需同时设置下面 4 个） |
| `PLANE_WORKSPACE_SLUG` | 无 | Plane workspace |
| `PLANE_API_KEY` | 无 | Plane API key |
| `PLANE_WEBHOOK_SECRET` | 无 | Plane webhook HMAC 验证 |
| `GITLAB_BASE_URL` | 无 | GitLab 集成（需同时设置下面 2 个） |
| `GITLAB_TOKEN` | 无 | GitLab access token |
| `GITLAB_PROJECT_ID` | 无 | GitLab project ID |
| `DEVFLOW_RUNNER_ADAPTER` | `mock` | DevFlow runner adapter，支持 `mock` / `codex` / `claude_code` |
| `DEVFLOW_REPO_URL` | 无 | Coding Runner clone/push 的仓库地址 |
| `DEVFLOW_DEFAULT_BRANCH` | `main` | DevFlow 创建 MR 的目标分支 |
| `DEVFLOW_WORKSPACE_BASE_DIR` | 系统临时目录 | Coding Runner workspace 根目录 |

> DevFlow 功能需要同时设置 `PLANE_BASE_URL`、`PLANE_API_KEY`、`GITLAB_BASE_URL`、`GITLAB_TOKEN`、`GITLAB_PROJECT_ID`、`DEVFLOW_REPO_URL`。使用真实 Codex/Claude runner 时，还需要本机 CLI 已安装并已登录。

---

## 附录 B：已注册 Agent 速查

| Agent ID | Backend | 模式 | 工具 | 特点 |
|---|---|---|---|---|
| `echo` | native | custom adapter | 无 | 最简验证，回显输入 |
| `hermes_echo` | hermes | SDK / fallback | 无 | 验证 Hermes fallback 链路 |
| `myj` | native | orchestrator_workers | 4 个（搜索/位置/促销/咨询） | 验证关键词路由和工具调用 |
| `promo_recommendation` | native | orchestrator_workers | 2 个（促销搜索/产品排名） | 验证 Knowledge/RAG 注入点 |

---

## 附录 C：验证检查清单

按推荐顺序执行：

| # | 模块 | 命令 | 外部依赖 | 预计耗时 |
|---|---|---|---|---|
| 1 | 启动 + 健康检查 | `curl /health` | 无 | 1 min |
| 2 | Agent 注册发现 | `GET /api/v1/agents` | 无 | 1 min |
| 3 | Echo 对话 | `POST /api/v1/agent/chat` | 无 | 1 min |
| 4 | Hermes fallback | chat hermes_echo | 无 | 1 min |
| 5 | 工具路由 (myj) | chat + 检查 tool_calls | 无 | 3 min |
| 6 | 多轮会话 | 2 次 chat + GET session | 无 | 2 min |
| 7 | 部署/回滚/审计 | deploy → rollback → audit | 无 | 3 min |
| 8 | Eval 执行 | `POST /evals/run` | 无 | 1 min |
| 9 | Admin 状态 | `GET /admin/status` | 无 | 1 min |
| 10 | Admin 工具列表 | `GET /admin/tools` | 无 | 1 min |
| 11 | Metrics | `GET /metrics` | 无 | 1 min |
| 12 | API 鉴权 | 设置 API_KEY + 测试 401/200 | 无 | 2 min |
| 13 | Manifest 校验 | `scripts/validate_manifest.py` | 无 | 1 min |
| 14 | Smoke test | `scripts/smoke_test.py` | 无 | 1 min |
| 15 | Swagger UI | 浏览器打开 /docs | 无 | 1 min |
| 16 | SQLite 持久化 | 设置 DATABASE_URL + 重启验证 | 无 | 3 min |
| 17 | WebSocket | wscat / Python 脚本 | wscat 或 websockets | 2 min |
| 18 | HITL 审批 | 设置 HITL_ENABLED + 修改 risk_level | 无 | 5 min |
| 19 | DevFlow 真实 Runner E2E | `scripts/devflow_real_e2e.py` | Plane + GitLab + Codex/Claude CLI | 5-15 min |
| 20 | DevFlow Webhook 手动验证 | 第 18 节逐步操作 | Plane + GitLab + ngrok（可选） | 15-30 min |
| | **合计** | | | **~55 min** |

全部通过即可确认平台核心功能模块正常工作。
