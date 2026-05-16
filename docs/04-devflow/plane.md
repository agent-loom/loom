# Plane 集成设计

> 相关文档：Plane API 端点速查表见 [`../vendor/plane/endpoints.md`](../vendor/plane/endpoints.md)；OpenAPI 获取方式见 [`../99-reference/plane-docs-acquisition.md`](../99-reference/plane-docs-acquisition.md)。

本文档定义 `agent-platform` 如何接入已部署的 Plane，把 Plane 作为需求、Work Item、看板和 AI 研发任务流的入口。

当前 Plane 实例：

```text
Plane Web: http://10.193.0.147:3333/
Plane API Base URL: http://10.193.0.147:3333/api
Plane Developer Docs: https://developers.plane.so/
```

官方文档确认 Plane 提供：

1. REST API，用于管理 projects、work items、states、labels、types、custom properties、cycles、modules、intake 等。
2. Webhooks，用于接收 project、work item、cycle、module、comment 等事件。
3. MCP Server，可让 Claude Code、Cursor、VSCode、Zed 等 AI 工具通过 MCP 操作 Plane。

## 1. Plane 在平台里的定位

```text
Plane
负责：需求、Work Item、看板、AI 拆解状态、业务验收入口

GitLab
负责：代码、分支、MR、CI、Review、制品

Agent Platform
负责：Agent 注册、Manifest 校验、Eval、灰度、发布、运行态
```

不要把 Agent 运行状态、发布状态、trace 都塞进 Plane。Plane 只保存研发协作需要的摘要和链接。

## 2. 总体集成架构

```mermaid
flowchart TB
    subgraph Plane[Plane]
        Intake[Intake]
        WorkItem[Work Items]
        States[States]
        Labels[Labels]
        CustomProps[Custom Properties]
        Comments[Comments]
        Webhook[Webhooks]
    end

    subgraph DevFlow[Agent Platform DevFlow]
        PlaneAdapter[Plane Adapter]
        RequirementAgent[Requirement Agent]
        PlannerAgent[Planner Agent]
        TaskPack[Task Pack Generator]
        GitLabAdapter[GitLab Adapter]
        EvalAdapter[Eval Adapter]
    end

    subgraph GitLab[GitLab]
        Branch[Branch]
        MR[Merge Request]
        CI[CI Pipeline]
    end

    subgraph Platform[Agent Platform]
        Registry[Agent Registry]
        Eval[Eval Runner]
        Deploy[Deployment Controller]
        Runtime[Runtime]
    end

    Intake --> PlaneAdapter
    WorkItem --> PlaneAdapter
    Webhook --> PlaneAdapter
    PlaneAdapter --> RequirementAgent
    RequirementAgent --> PlannerAgent
    PlannerAgent --> TaskPack
    TaskPack --> GitLabAdapter
    GitLabAdapter --> Branch
    GitLabAdapter --> MR
    MR --> CI
    CI --> EvalAdapter
    EvalAdapter --> PlaneAdapter
    CI --> Registry
    Registry --> Eval
    Eval --> Deploy
    Deploy --> Runtime
    Runtime --> PlaneAdapter
    PlaneAdapter --> Comments
```

## 2.1 Plane / SCM / Coding Runner / Hermes 端到端交互流程

本节是 Plane 与代码平台、AI 编码执行器、Agent Runtime 的主流程事实源。后续如果接入 GitHub、调整 DevFlowOrchestrator、修改 Plane webhook 触发条件或 Hermes runtime 边界，优先更新本节，再同步 `04-devflow/gitlab.md`、`04-devflow/devflow-state-sync-design.md` 和 `03-runtime/hermes-runtime.md`。

当前实现中，Plane、GitLab、Hermes 不是直接两两互调，而是由 `agent-platform` 作为编排层统一打通：

```text
Plane 管需求和看板状态
GitLab 管代码、分支、MR、CI 和制品
agent-platform 管 DevFlow 编排、TaskPack、Eval、发布、运行时路由
Hermes 是 Agent RuntimeBackend 之一，负责运行配置为 hermes backend 的业务 Agent
```

当前代码已经接入的是 **Plane + GitLab**。GitHub 尚未实现主链路；如果需要接 GitHub，应先抽象统一 `ScmAdapter`，再实现 GitHub 的 branch / pull request / comment / workflow artifact 能力。

### 2.1.1 端到端架构图

```mermaid
flowchart LR
    subgraph Plane[Plane]
        WI[Work Item]
        State[Ready for AI Dev]
        Comment[Comments]
        Props[Custom Properties]
    end

    subgraph Platform[Agent Platform]
        Webhook[Plane Webhook Endpoint]
        Orchestrator[DevFlowOrchestrator]
        TaskPack[TaskPackGenerator]
        Runner[CodingAgentRunner]
        Eval[EvalRunner]
        Deploy[Deployment Controller]
        Router[AgentRouter]
        Runtime[RuntimeManager]
    end

    subgraph SCM[SCM 当前为 GitLab / 未来可扩展 GitHub]
        Branch[Feature Branch]
        MR[Merge Request / Pull Request]
        CI[CI Pipeline]
        Artifact[Artifacts]
    end

    subgraph AgentRuntime[Agent Runtime Backends]
        Native[NativeRuntimeBackend]
        Hermes[HermesRuntimeBackend]
        LangGraph[LangGraphRuntimeBackend]
    end

    WI --> State
    State --> Webhook
    Webhook --> Orchestrator
    Orchestrator --> TaskPack
    TaskPack --> Branch
    Branch --> MR
    MR --> CI
    Orchestrator --> Comment
    Orchestrator --> Props
    Orchestrator --> Runner
    Runner --> MR
    CI --> Artifact
    CI --> Eval
    Eval --> Comment
    Eval --> Deploy
    Deploy --> Router
    Router --> Runtime
    Runtime --> Native
    Runtime --> Hermes
    Runtime --> LangGraph
```

### 2.1.2 主时序

```mermaid
sequenceDiagram
    participant Human as Product / Developer
    participant Plane as Plane Work Item
    participant AP as Agent Platform
    participant GitLab as GitLab
    participant Runner as Coding Runner
    participant Eval as Eval Runner
    participant Runtime as Agent Runtime

    Human->>Plane: 创建需求 / 补充验收标准
    Human->>Plane: 状态改为 Ready for AI Dev
    Plane->>AP: POST /api/v1/integrations/plane/webhook
    AP->>AP: 校验事件类型、状态、签名、幂等
    AP->>Plane: get_work_item(project_id, work_item_id)
    AP->>AP: 生成 DevFlow TaskPack
    AP->>GitLab: create_branch(feat/<work_item_id>)
    AP->>GitLab: create_merge_request(...)
    AP->>Plane: add_comment(MR link)
    AP->>Plane: update_custom_properties(gitlab_branch, gitlab_mr_url, gitlab_mr_iid)
    AP->>Runner: run(task_pack, mr_iid, plane ids)
    Runner->>GitLab: 提交代码 / 更新 MR
    GitLab->>Eval: CI 执行测试和 eval
    Eval->>GitLab: MR comment(eval report)
    Eval->>Plane: comment / state update
    Human->>GitLab: Review / merge
    AP->>Runtime: deploy 后运行 agent chat
    Runtime->>Runtime: 根据 manifest 选择 native / hermes / langgraph
```

### 2.1.3 标准 16 步流程

下面 16 步是 Plane、SCM、Coding Runner、Eval、Deployment 和 Hermes Runtime 打通后的标准端到端流程。架构设计、测试用例、runbook 和后续实现任务都应按这组步骤校准。

1. 人在 Plane 创建或补充需求，例如“新增促销推荐 Agent”。
2. 人或 AI 在 Plane Work Item 中补齐背景、验收标准、风险、目标 `agent_id` 和 `task_type`。
3. 人把 Plane Work Item 状态改成 `Ready for AI Dev`。
4. Plane 通过 Webhook 调用 `agent-platform` 的 `/api/v1/integrations/plane/webhook`。
5. `agent-platform` 校验 Webhook 签名、事件类型、delivery 幂等和目标状态。
6. `DevFlowOrchestrator` 从 Plane 拉取 Work Item 详情。
7. `TaskPackGenerator` 根据 Work Item 生成 DevFlow TaskPack。
8. SCM Adapter 创建 feature branch；当前实现为 GitLab branch，未来可扩展为 GitHub branch。
9. SCM Adapter 创建变更请求；当前实现为 GitLab MR，未来可扩展为 GitHub PR。
10. `agent-platform` 把 branch、MR/PR 链接和 DevFlow 摘要评论回 Plane。
11. `agent-platform` 把 `gitlab_branch`、`gitlab_mr_url`、`gitlab_mr_iid` 等工程信息写回 Plane custom properties。
12. `CodingAgentRunner` 接收 TaskPack，调用 mock / codex / claude_code adapter 执行代码修改。
13. Coding Runner 提交代码并更新 MR/PR，变更内容通常包括 manifest、adapter、tools、prompts、evals、tests 和 docs。
14. CI 执行 lint、unit tests、contract tests、agent eval 和 package；Eval 结果回写 MR/PR 和 Plane。
15. Eval 和人工 Review 通过后，Agent Platform 执行 staging / prod canary / prod 部署，并记录 artifact、deployment 和 audit。
16. 业务流量通过 `POST /api/v1/agent/chat` 进入平台，由 `AgentRouter` 和 `RuntimeManager` 根据 manifest 选择 `native`、`hermes` 或 `langgraph` runtime；配置为 `hermes` 的 Agent 才会进入 `HermesRuntimeBackend`。

关键边界：

| 边界 | 规则 |
| --- | --- |
| Plane -> Platform | Plane 只触发需求和状态事件，不直接运行 Agent |
| Platform -> SCM | Platform 通过 adapter 创建 branch 和 MR/PR，不把 SCM 逻辑写死在 Plane adapter |
| SCM -> Runner | Coding Runner 只围绕 TaskPack 和受控工作区改代码 |
| Eval -> Deploy | Eval 是部署门禁的一部分，不应绕过 |
| Runtime -> Hermes | Hermes 是 runtime backend，不处理 Plane webhook，也不直接管理 MR/PR |

### 2.1.4 Plane Webhook 到 DevFlow 的触发条件

当前 `DevFlowOrchestrator` 只处理这些事件：

```text
work_item.updated
work_item
issue.updated
issue
```

工作项状态必须命中 `Ready for AI Dev` 触发集合。最小 payload：

```json
{
  "data": {
    "id": "wi-001",
    "project": "proj-001",
    "name": "新增促销推荐 Agent",
    "state_detail": {"name": "Ready for AI Dev"},
    "properties": {
      "agent_id": "promo_recommendation",
      "task_type": "agent:new"
    }
  }
}
```

字段约定：

| 字段 | 用途 | 当前实现 |
| --- | --- | --- |
| `data.id` | Work Item ID，也作为 TaskPack task_id | 必需 |
| `data.project` / `data.project_id` | Plane Project ID | 必需 |
| `data.name` / `data.title` | 任务标题 | 可选，缺省 `Untitled` |
| `data.state_detail.name` / `data.state.name` | 看板状态 | 必须为 Ready 状态 |
| `data.properties.agent_id` | 目标业务 Agent | 可选 |
| `data.properties.task_type` | DevFlow 任务类型 | 可选，缺省 `platform:change` |

### 2.1.5 回写 Plane 的数据

DevFlow 成功创建 SCM 分支和 MR 后，平台应回写 Plane：

| Plane 位置 | 回写内容 | 目的 |
| --- | --- | --- |
| Comment | MR / PR 链接、分支名、DevFlow 摘要 | 让产品和研发在需求卡片里看到工程入口 |
| Custom Properties | `gitlab_branch`、`gitlab_mr_url`、`gitlab_mr_iid` | 后续状态同步、eval 回写、人工追踪 |
| State | `AI Developing`、`Testing / Eval`、`Human Review` 等 | 让看板反映自动化研发进度 |

当前代码已实现 MR comment 和 custom properties 回写；状态回写依赖配置 `ai_developing_state_id`，完整状态机见 `04-devflow/devflow-state-sync-design.md`。

### 2.1.6 Hermes 在流程中的位置

Hermes 不负责 Plane webhook，也不直接操作 GitLab/GitHub。Hermes 的位置是 **运行时后端**：

```text
前端 / 业务系统
  -> POST /api/v1/agent/chat
  -> AgentRouter
  -> RuntimeManager
  -> 根据 manifest.runtime.backend 选择 HermesRuntimeBackend
  -> ModelGateway / ToolBridge / ResponseMapper
  -> AgentResponse
```

Plane 研发流程和 Hermes runtime 的关系是：

1. Plane 触发新增或修改业务 Agent。
2. DevFlow 生成 TaskPack 并驱动代码变更。
3. 代码变更可能新增 `runtime.backend: hermes` 的 Agent Package。
4. Eval 和部署通过后，线上业务请求进入 `RuntimeManager`。
5. 只有 manifest 指定 `hermes` 的 Agent 才会走 `HermesRuntimeBackend`。

因此不要在 Plane Adapter 中直接依赖 Hermes SDK，也不要让 Hermes Runtime 直接处理需求看板事件。两者通过 `Agent Package + Manifest + Deployment` 间接衔接。

### 2.1.7 GitHub 扩展点

当前主实现是 GitLab。接入 GitHub 时建议不要把 GitHub 逻辑塞进 `DevFlowOrchestrator`，而是新增统一 SCM 抽象：

```python
class ScmAdapter(Protocol):
    async def create_branch(self, project_id: str, branch: str) -> dict: ...
    async def create_change_request(
        self,
        project_id: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        labels: list[str],
    ) -> dict: ...
    async def add_comment(self, project_id: str, change_request_id: int, body: str) -> dict: ...
    async def download_artifacts(self, project_id: str, job_id: str) -> bytes: ...
```

映射关系：

| 平台概念 | GitLab | GitHub |
| --- | --- | --- |
| 代码项目 | project | repository |
| 变更请求 | Merge Request | Pull Request |
| CI | GitLab Pipeline | GitHub Actions |
| 制品 | Job Artifacts | Workflow Artifacts |
| 评论 | MR Notes | Issue / PR Comments |

完成抽象后，`DevFlowOrchestrator` 只依赖 `ScmAdapter`，Plane 流程不需要关心后端是 GitLab 还是 GitHub。

### 2.1.8 当前实现状态

| 能力 | 当前状态 | 代码入口 |
| --- | --- | --- |
| Plane REST Adapter | 已实现 | `src/agent_platform/integrations/plane/adapter.py` |
| Plane Webhook 签名 | 已实现 | `src/agent_platform/integrations/plane/webhook.py` |
| Plane Webhook endpoint | 已实现 | `src/agent_platform/api/app.py` |
| Webhook delivery 幂等 | 已实现，依赖 webhook repo | `src/agent_platform/api/app.py` |
| Ready for AI Dev 触发 DevFlow | 已实现 | `src/agent_platform/devflow/orchestrator.py` |
| Plane -> GitLab branch/MR | 已实现 | `src/agent_platform/devflow/orchestrator.py` |
| GitLab MR 链接回写 Plane | 已实现 | `src/agent_platform/devflow/orchestrator.py` |
| CodingRunner 派发 | 已实现基础版 | `src/agent_platform/devflow/runner/runner.py` |
| GitHub 主链路 | 未实现 | 待新增 `GitHubAdapter` / `ScmAdapter` |
| Hermes RuntimeBackend | 已实现轻量后端 | `src/agent_platform/runtime/hermes.py` |
| Hermes 官方 runtime/planner/memory/event stream | 未完整接入 | 见 `03-runtime/hermes-runtime.md` |

## 3. Plane 项目建议

建议先建 3 个 Project：

| Project | 用途 |
| --- | --- |
| `Agent Platform` | 平台核心能力：router、manifest、runtime、eval、devflow |
| `MYJ Agent` | MYJ 业务 Agent package、工具、eval、迁移任务 |
| `Agent R&D Flow` | AI + 人 + coding agent 研发流程、Plane/GitLab/Hermes 集成 |

也可以先只建一个 `Agent Platform` Project，使用 `agent_id` 自定义字段区分业务 Agent。MVP 推荐先单 Project，降低配置成本。

## 4. Work Item Type 设计

Plane 支持 Work Item Types。建议定义：

| Type | 说明 |
| --- | --- |
| `agent:new` | 新增业务 Agent |
| `agent:change` | 修改已有业务 Agent |
| `tool:new` | 新增平台或业务工具 |
| `tool:change` | 修改工具 |
| `knowledge:sync` | 新增或修改知识源同步 |
| `eval:add` | 增加 eval case |
| `protocol:change` | 修改接口协议 |
| `platform:change` | 修改平台核心 |
| `bug` | 缺陷修复 |
| `docs` | 文档任务 |
| `risk` | 风险或安全问题 |

## 5. State / 看板流转

建议在 Plane 项目里配置这些状态：

```text
Backlog
Clarifying
Designing
Ready for AI Dev
AI Developing
Testing / Eval
Human Review
Staging
Production
Done
Blocked
Canceled
```

状态含义：

| State | 说明 | 自动化 |
| --- | --- | --- |
| `Backlog` | 新需求池 | 手动或 Intake 创建 |
| `Clarifying` | AI / 人澄清需求 | AI 可自动补问题 |
| `Designing` | 架构设计、方案确认 | AI 生成 design brief |
| `Ready for AI Dev` | 可交给 coding agent | 触发 task pack |
| `AI Developing` | Coding agent 开发中 | DevFlow 创建 GitLab MR |
| `Testing / Eval` | CI 和 eval 执行中 | GitLab pipeline webhook 更新 |
| `Human Review` | 等待人审 | MR ready + eval pass |
| `Staging` | 已进入 staging | Agent Platform 回写 |
| `Production` | 已进入生产灰度或全量 | Agent Platform 回写 |
| `Done` | 完成 | 人工或自动关闭 |
| `Blocked` | 阻塞 | 人工标记 |
| `Canceled` | 取消 | 人工标记 |

## 6. Labels 设计

建议预置 labels：

```text
ai-generated
needs-clarification
needs-design
ready-for-ai-dev
codex
claude-code
openhands
manifest
runtime
eval
gitlab
hermes
myj
high-risk
requires-human-approval
```

## 7. Custom Properties 设计

Plane API 支持 Custom Properties。建议为 Work Item 增加：

| Property | 类型 | 示例 | 说明 |
| --- | --- | --- | --- |
| `agent_id` | text / dropdown | `myj` | 涉及的 Agent |
| `task_type` | dropdown | `agent:change` | 和 Work Item Type 对齐 |
| `risk_level` | dropdown | `low` / `medium` / `high` | 风险等级 |
| `runtime_backend` | dropdown | `native` / `hermes` / `langgraph` | 目标 runtime |
| `gitlab_project` | text | `agent-platform` | GitLab 项目 |
| `gitlab_mr` | url | MR 链接 | 关联 MR |
| `task_pack_url` | url / text | artifact URL | DevFlow Task Pack |
| `eval_report_url` | url / text | eval report | 评测报告 |
| `agent_version` | text | `0.1.0` | 关联 Agent 版本 |
| `deployment_env` | dropdown | `dev` / `staging` / `prod` | 发布环境 |

MVP 可以先不用全部字段。第一阶段至少需要：

```text
agent_id
task_type
risk_level
gitlab_mr
eval_report_url
```

## 8. Plane REST API 使用方式

Plane 官方 API 是 REST 风格。自部署实例的 API base URL 应该使用：

```text
http://10.193.0.147:3333/api
```

认证方式：

```http
X-API-Key: <PLANE_API_KEY>
```

或 OAuth：

```http
Authorization: Bearer <oauth_access_token>
```

MVP 推荐先用 Personal Access Token / API Key。

### 8.1 环境变量

```env
PLANE_BASE_URL=http://10.193.0.147:3333/api
PLANE_API_KEY=plane_api_xxx
PLANE_WORKSPACE_SLUG=<workspace_slug>
PLANE_DEFAULT_PROJECT_ID=<project_id>
```

不要把 API Key 写入 manifest、docs 或代码。

### 8.2 关键 API 资源

根据官方 API 导航，DevFlow 主要需要：

| Plane Resource | 用途 |
| --- | --- |
| Project | 获取和管理项目 |
| Work Item | 创建、查询、更新研发任务 |
| Work Item States | 创建和查询看板状态 |
| Work Item Labels | 管理 labels |
| Work Item Types | 管理任务类型 |
| Custom Properties | 管理自定义字段 |
| Work Item Comments | 回写 AI 分析、MR、CI、Eval 结果 |
| Intake | 接收业务需求入口 |
| Cycles | Sprint / 迭代 |
| Modules | 功能模块，例如 manifest、runtime、eval |

## 9. PlaneAdapter 设计

建议封装统一 adapter，不让业务代码直接调 Plane API。

```python
class PlaneAdapter:
    def list_projects(self) -> list[PlaneProject]:
        ...

    def create_work_item(self, request: CreateWorkItemRequest) -> PlaneWorkItem:
        ...

    def get_work_item(self, work_item_id: str) -> PlaneWorkItem:
        ...

    def update_work_item_state(self, work_item_id: str, state_id: str) -> None:
        """state_id 是 Plane 内部 UUID，通过 States API 查询获得。"""
        ...

    def update_custom_properties(self, work_item_id: str, values: dict) -> None:
        ...

    def add_comment(self, work_item_id: str, body: str) -> None:
        ...

    def search_work_items(self, query: str) -> list[PlaneWorkItem]:
        ...
```

Adapter 负责：

1. API base URL。
2. API key。
3. workspace slug。
4. pagination。
5. retry。
6. error mapping。
7. idempotency。
8. markdown comment 格式。

## 10. DevFlow 和 Plane 的事件流

```mermaid
sequenceDiagram
    autonumber
    participant User as Product / Developer
    participant Plane as Plane
    participant Webhook as Plane Webhook
    participant DevFlow as DevFlow Service
    participant AI as Requirement / Planner Agent
    participant GitLab as GitLab

    User->>Plane: create intake / work item
    Plane->>Webhook: work item create event
    Webhook->>DevFlow: POST /webhooks/plane
    DevFlow->>AI: analyze requirement
    AI-->>DevFlow: spec + acceptance + open questions
    DevFlow->>Plane: add comment
    DevFlow->>Plane: move to Clarifying / Designing
    User->>Plane: confirm requirement
    Plane->>Webhook: work item update state=Ready for AI Dev
    Webhook->>DevFlow: trigger task pack
    DevFlow->>GitLab: create branch + MR
    DevFlow->>Plane: update gitlab_mr + state=AI Developing
```

## 11. Webhook 设计

Plane Webhooks 会向指定 URL 发送 HTTP POST。官方文档说明 header 包含：

```http
X-Plane-Delivery: <uuid>
X-Plane-Event: <event>
X-Plane-Signature: <hmac_sha256>
```

支持事件包括：

```text
Project
Work Item
Cycle
Module
Work Item Comment
```

平台 webhook endpoint：

```http
POST /api/v1/integrations/plane/webhook
```

必须做：

1. 校验 `X-Plane-Signature`。
2. 使用 `X-Plane-Delivery` 做幂等。
3. 只处理白名单 workspace。
4. 只处理目标 project。
5. 快速返回 200，复杂任务异步处理。

签名校验伪代码：

```python
expected = hmac_sha256(secret, raw_body)
if not hmac.compare_digest(expected, received_signature):
    raise Unauthorized
```

## 12. MCP Server 使用方式

Plane 官方提供 MCP Server。配置方式和环境变量见 [`../99-reference/plane-docs-acquisition.md`](../99-reference/plane-docs-acquisition.md) §4。

建议用途：

1. Claude Code / Cursor / Codex 读取需求。
2. AI 自动创建或更新 work item。
3. AI 根据 issue 生成 task pack。
4. AI 回写开发总结和风险点。

注意：

1. MCP 适合人机交互和 coding agent 工作台。
2. 服务端自动化仍建议通过 Plane REST API 实现。
3. 不要让 MCP token 具有超过必要范围的权限。

## 13. Work Item 模板

推荐需求模板：

```markdown
## 背景

## 用户场景

## 期望行为

## 非目标

## 涉及 Agent

## 输入协议

## 输出协议

## 涉及工具 / 知识源

## 验收标准

## Eval Cases

## 风险点

## GitLab / MR

## 发布计划
```

AI 需求理解 Agent 回写评论：

```markdown
## AI 需求理解结果

### 规格摘要

### 待澄清问题

### 建议任务拆分

### 建议 Eval Cases

### 风险
```

## 14. Plane 与 GitLab 状态同步

| 事件 | Plane 更新 | GitLab 更新 |
| --- | --- | --- |
| Work Item 进入 `Ready for AI Dev` | 生成 task pack | 创建 branch + draft MR |
| MR 创建成功 | 写入 `gitlab_mr` 字段，状态到 `AI Developing` | MR 描述引用 Plane Work Item |
| CI 开始 | 状态到 `Testing / Eval` | pipeline running |
| CI / Eval 通过 | 状态到 `Human Review` | MR ready for review |
| MR 合并 | 状态到 `Staging` | main merged |
| Agent staging 发布 | 评论发布结果 | environment deploy success |
| Prod 灰度 | 状态到 `Production` | manual deploy job |
| 完成 | 状态到 `Done` | MR closed / merged |

避免双向同步过度：

1. Plane 保存 GitLab MR 链接和摘要。
2. GitLab MR 保存 Plane Work Item 链接。
3. 细节以各自系统为准。

## 15. MVP 落地步骤

第一阶段：

1. 在 Plane 创建 `Agent Platform` Project。
2. 配置 states。
3. 配置 labels。
4. 配置最小 custom properties。
5. 生成 API Key。
6. 在 agent-platform 配置 `PLANE_BASE_URL`、`PLANE_API_KEY`、`PLANE_WORKSPACE_SLUG`。
7. 实现 `PlaneAdapter` 的 work item 查询、评论、状态更新。
8. 实现 webhook endpoint。
9. 打通 `Ready for AI Dev -> GitLab MR`。
10. 把 CI / Eval 结果回写 Plane comment。

第二阶段：

1. 接入 Intake。
2. 接入 MCP 给 Claude Code / Codex 使用。
3. 自动生成 task pack。
4. 自动拆子任务。
5. 支持 Plane Modules / Cycles。

## 16. 安全要求

1. `PLANE_API_KEY` 只放环境变量或 secret manager。
2. Webhook secret 必须校验。
3. Plane webhook 要做幂等，防止重复创建 MR。
4. Coding agent 不能直接拿高权限 Plane token。
5. 生产发布仍以 GitLab + Agent Platform 审批为准，不能只靠 Plane 状态。
6. Plane 评论里不能写密钥、完整用户隐私、内部 API token。

## 17. 官方文档参考

- Plane Developer Docs: https://developers.plane.so/
- API Reference: https://developers.plane.so/api-reference/introduction
- Webhooks: https://developers.plane.so/dev-tools/intro-webhooks
- MCP Server: https://developers.plane.so/dev-tools/mcp-server
