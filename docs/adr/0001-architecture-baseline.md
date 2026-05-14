# ADR-0001 架构基线决策

## 状态

Accepted for MVP draft.

## 日期

2026-05-14

## 背景

项目目标是构建一个生产 + 开发一体化的 Agent Platform：

1. 生产侧支持多个业务 Agent，为前端、门店设备、后台和消息渠道提供问答、推荐、工具调用等能力。
2. 研发侧支持 AI + 人 + vibe coding 流程，让新增业务 Agent 时可以通过需求理解、任务拆解、自动编码、测试、评审和发布门禁完成。

已有参考：

1. `myj` 类型业务 Agent 需要强业务协议、门店上下文、商品/位置/促销工具和 RAG。
2. Hermes 具备通用 Agent runtime、tool registry、plugin、session、gateway 等能力。
3. GitLab 适合承载代码、MR、CI、Review 和制品。
4. Plane 已部署为需求和 Work Item 看板，MVP 接入最小 PlaneAdapter，但不做完整双向同步。

## 决策

### 1. 平台自研核心契约

自研以下平台核心：

1. `AgentRequest / AgentResponse`
2. `AgentManifest v1`
3. `Agent Registry`
4. `Agent Router`
5. `Tool Registry`
6. `Eval Runner`
7. `DevFlow Task Pack`

原因：这些是业务平台的稳定边界，不能被某一个 runtime 或开源项目绑定。

### 2. Runtime 使用可插拔 Backend

定义：

```python
class RuntimeBackend:
    name: str

    async def run(self, request):
        ...
```

MVP 先实现：

```text
NativeRuntimeBackend
```

后续可接：

```text
HermesBackend
LangGraphBackend
```

原因：`myj` 这类业务 Agent 需要保留强业务协议和现有迁移路径；Hermes 更适合作为可插拔能力来源，不应直接成为平台本体。

### 3. 不深 fork Hermes

Hermes 使用策略：

```text
官方 Hermes / 上游版本
    +
Platform Hermes Adapter
```

不在 Hermes core 里写业务逻辑，不把 `myj` 代码塞进 Hermes。

允许轻量 patch 的条件：

1. Hermes 缺少必要 hook。
2. Hermes bug 阻塞生产。
3. 需要结构化 trace 或更严格 permission。

### 4. GitLab 作为工程交付系统

GitLab 负责：

1. Repository
2. Branch
3. Merge Request
4. CI
5. Review
6. Artifact
7. Environment trigger

Agent Platform 负责：

1. Agent 注册
2. Manifest 校验
3. Eval 结果管理
4. 灰度发布状态
5. Runtime 观测

### 5. Plane 作为需求看板，MVP 做最小接入

MVP 使用已部署 Plane 作为需求和 Work Item 看板。第一阶段只实现最小 PlaneAdapter：

1. 读取 Work Item。
2. 更新 Work Item 状态。
3. 写入评论。
4. 写入 GitLab MR / Eval report 链接。
5. 接收 Plane webhook 触发 `Ready for AI Dev -> GitLab MR`。

第一阶段不做完整 Plane 双向同步，不把发布状态、runtime trace、Agent Registry 状态完整复制进 Plane。

原因：Plane 已部署，接入成本可控；但平台仍必须把生产状态保留在 Agent Platform 内，避免 Plane 成为生产事实源。

### 6. 先契约后实现

正式编码前先冻结：

1. `mvp.md`
2. `agent-request-response.md`
3. `agent-manifest-v1.md`
4. `devflow-task-pack.md`

核心接口变化必须更新文档。

## 备选方案

### 方案 A：直接基于 Dify 二次开发

优点：

1. 已有平台 UI。
2. 已有 workflow、RAG、Agent 应用管理。

缺点：

1. 产品模型会绑定 Dify。
2. 业务 manifest、GitLab DevFlow、AI coding 流程仍需大量自研。
3. 深改成本高。

结论：不作为 MVP 主路线。

### 方案 B：直接基于 Hermes fork

优点：

1. 可复用 Hermes runtime、tools、plugins。
2. 快速获得通用 Agent 能力。

缺点：

1. Hermes 不是多业务 Agent 平台。
2. 深 fork 后上游合并成本高。
3. 业务平台逻辑会污染 Hermes core。

结论：不采用。只做 adapter。

### 方案 C：GitLab-only，不接 Plane

优点：

1. 系统少。
2. Issue、MR、CI、Review 一体。

缺点：

1. 产品需求和 AI 需求理解体验较弱。
2. 长期做 AI 研发工作台不如 Plane 灵活。

结论：当前不作为主路线。Plane 已部署，MVP 应实现最小 PlaneAdapter；GitLab-only 只作为 Plane 不可用时的降级方案。

## 后果

正面影响：

1. 平台边界清晰。
2. 后续可以替换 runtime。
3. 新增 Agent 有标准 package 和 manifest。
4. AI coding agent 有明确任务约束。
5. GitLab CI 可以作为质量门禁。

代价：

1. 需要自研平台胶水层。
2. 初期没有完整 UI。
3. 需要维护 manifest schema、eval runner 和 adapter。
4. 需要额外设计 DevFlow 状态机。

## MVP 验证点

ADR 是否成立，需要通过以下验证：

1. `myj` demo Agent 可以通过 manifest 注册。
2. `/api/v1/agent/chat` 可以路由到 `myj`。
3. eval runner 可以跑 `myj` golden cases。
4. GitLab CI 可以执行 manifest 校验和 eval。
5. 一个 task pack 可以驱动 coding agent 完成一个小变更。
