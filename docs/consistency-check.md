# 设计文档一致性检查报告

检查日期：2026-05-15

本文档记录 `docs/` 下设计文档的一致性检查结果和已统一的设计准则。

## 1. 检查范围

已检查：

```text
docs/README.md
docs/mvp.md
docs/agent-platform-design.md
docs/ai-human-vibecoding-rd-platform.md
docs/gitlab.md
docs/plane.md
docs/plane-docs-acquisition.md
docs/hermes-runtime.md
docs/devflow-task-pack.md
docs/pre-development-checklist.md
docs/contracts/agent-request-response.md
docs/contracts/agent-manifest-v1.md
docs/adr/0001-architecture-baseline.md
docs/vendor/plane/README.md
docs/vendor/plane/endpoints.md
```

未逐行人工审查但已作为事实来源保留：

```text
docs/vendor/plane/openapi.yaml
docs/vendor/plane/openapi.json
```

## 2. 当前统一结论

### 2.1 平台边界

一致结论：

```text
Agent Platform 是平台本体。
业务 Agent 是 Agent Package。
Hermes / LangGraph / Native 是可插拔 RuntimeBackend。
Plane 是需求和 Work Item 看板。
GitLab 是工程交付闭环。
```

### 2.2 MVP 范围

一致结论：

```text
MVP 必须实现 NativeRuntimeBackend。
MVP 必须实现最小 PlaneAdapter。
MVP 必须实现 GitLab Adapter。
MVP 不做完整 Plane 双向同步。
MVP 不做完整 Web 管理后台。
MVP 不深 fork Hermes。
MVP 不完整替换 MYJ。
```

### 2.3 Plane 术语

统一术语：

```text
Plane 内对象：Work Item
GitLab 内对象：Issue / MR
泛化研发概念：Issue 可以出现，但涉及 Plane 时应写 Work Item
```

Plane API 使用规则：

```text
优先使用 /work-items/ endpoint。
不优先使用旧 /issues/ endpoint。
```

例外：

1. `docs/vendor/plane/openapi.yaml` / `openapi.json` 是官方 schema 快照，保留原始 `/issues/` 路径。
2. `docs/vendor/plane/README.md` 和 `endpoints.md` 可以提到旧 `/issues/`，用于提醒 adapter 避免使用旧路径。

### 2.4 Plane OpenAPI 状态

当前事实：

```text
GET http://10.193.0.147:3333/api/schema/ -> 200
GET http://10.193.0.147:3333/api/schema/swagger-ui/ -> 200
GET http://10.193.0.147:3333/api/schema/?format=openapi-json -> 404
```

JSON 获取方式：

```bash
curl -sS -H 'Accept: application/json' \
  -o docs/vendor/plane/openapi.json \
  http://10.193.0.147:3333/api/schema/
```

### 2.5 Hermes 定位

一致结论：

```text
Hermes 是 RuntimeBackend + Tool/Plugin/Provider 能力来源。
不把 Agent Platform 做进 Hermes。
不把 MYJ 业务写进 Hermes core。
不深 fork Hermes。
```

MYJ 当前策略：

```text
阶段 1：MYJ 使用 NativeRuntimeBackend + adapter 接现有 MYJ。
阶段 2：MYJ 工具、prompt、knowledge、eval manifest 化。
阶段 3：低风险 MYJ 子能力试 HermesBackend。
阶段 4：再评估主链路是否用 HermesBackend。
```

### 2.6 Agent 契约

统一版本：

```text
Agent API protocol_version: agent-chat/v1
Agent Manifest api_version: agent.platform/v1
DevFlow Task Pack api_version: devflow.agent-platform/v1
```

核心对象：

```text
AgentRequest
AgentResponse
AgentManifest
RuntimeBackend
AgentPackage
AgentVersion
AgentDeployment
ToolDefinition
EvalSuite
EvalRun
DevelopmentTask
TaskPack
```

## 3. 本次已修正的问题

| 问题 | 修正 |
| --- | --- |
| `mvp.md` 中 Plane 角色过于弱化 | 改为 MVP 做最小 PlaneAdapter |
| ADR 仍写 Plane Adapter 第二阶段 | 改为 Plane 已部署，MVP 做最小接入 |
| 多处把 Plane 对象称为 `Issue` | 涉及 Plane 的地方改为 `Work Item` |
| DevFlow 示例使用旧的 Plane 任务 URL 风格 | 改为当前 Plane 实例地址占位 |
| Plane JSON 文档获取方式不准确 | 明确当前实例需使用 `Accept: application/json` |
| GitLab 文档中的 Plane 看板节点名不统一 | 改为 `Work Item / Kanban` |
| 开工检查清单没有 Plane P0 项 | 增加 Plane project、states、API key、webhook secret |

## 4. 仍需注意

### 4.1 Plane OpenAPI 中的旧 endpoint

OpenAPI 快照中同时存在：

```text
/issues/
/work-items/
```

后续实现 `PlaneAdapter` 时，应优先使用 `work-items`。不要因为 generated client 里有 `issues` 方法就默认使用旧接口。

### 4.2 GitLab Issue 仍然是合法术语

文档中仍会出现 GitLab Issue，这是合理的。判定规则：

```text
Plane -> Work Item
GitLab -> Issue
通用项目管理概念 -> 可用 Issue，但最好补充 Work Item
```

### 4.3 Plane 不作为生产事实源

Plane 可以显示 Agent 发布摘要和链接，但生产状态以 Agent Platform 为准：

```text
Agent Registry
Agent Deployment
Eval Report
Runtime Trace
```

这些不能以 Plane 字段为唯一事实源。

### 4.4 Hermes 接入顺序

文档统一为：

```text
MVP 主链路：NativeRuntimeBackend
研发侧试点：HermesBackend
低风险生产 Agent：HermesBackend
MYJ 主链路：后续评估
```

## 5. 后续文档维护规则

1. 修改 `AgentRequest / AgentResponse` 必须同步更新 `docs/contracts/agent-request-response.md`。
2. 修改 manifest 字段必须同步更新 `docs/contracts/agent-manifest-v1.md`。
3. 修改 DevFlow task pack 必须同步更新 `docs/devflow-task-pack.md`。
4. 修改 Plane API 使用方式必须同步更新 `docs/plane.md`、`docs/plane-docs-acquisition.md` 和 `docs/vendor/plane/endpoints.md`。
5. 修改 Hermes 接入策略必须同步更新 `docs/hermes-runtime.md` 和 ADR。
6. 新增重大决策必须新增 ADR。

## 6. 当前状态

本次检查后，设计文档在以下核心方向上保持一致：

1. 多 Agent 平台边界。
2. MYJ 作为 Agent Package 接入。
3. Hermes 作为可插拔 RuntimeBackend。
4. Plane + GitLab + Agent Platform 三方职责划分。
5. MVP 范围和不做事项。
6. Plane OpenAPI 获取和 `/work-items/` 优先策略。
7. Agent API、Manifest、DevFlow Task Pack 三个核心契约。
