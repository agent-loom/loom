# 文档阶段管理地图

本文档用于解决文档数量增长后的管理问题：每份文档必须归属到明确阶段、明确用途，并知道什么时候需要更新。

原则：

1. 不用文件数量判断复杂度，用阶段和用途管理复杂度。
2. `docs/README.md` 是入口。
3. 本文档是阶段地图。
4. `implementation-gap.md` 是当前实现状态和下一步优先级的事实来源。
5. 契约文档是实现必须遵守的边界。

## 1. 阶段定义

| 阶段 | 名称 | 目标 | 当前状态 |
| --- | --- | --- | --- |
| S0 | 架构基线 | 冻结平台边界、MVP、核心契约 | 已完成，持续维护 |
| S1 | MVP 骨架 | 跑通多 Agent、统一 API、manifest、runtime、eval、DevFlow 骨架 | 已基本实现 |
| S2 | 生产化底座 | 持久化、artifact、发布审计、回滚、权限、观测 | 部分完成；Registry/Deployment/Audit/Artifact/Knowledge 相关接线已在工作树中实现，但 ruff/manifest 质量门禁未通过 |
| S3 | Hermes 真接入 | 从 stub/adapter 原型变成真实 Hermes runtime 能力 | 部分完成；Spike A 已完成，官方 Hermes SDK Spike B 待实施 |
| S4 | AI 研发闭环 | CodingAgentRunner、workspace、path guard、Plane/GitLab 状态同步 | 部分完成；runner/workspace/path guard 基础已实现，真实 runner 配置、job 持久化、失败恢复待补 |
| S5 | 平台生产化与规模化 | 主链路可靠性校准、semantic routing、model gateway、knowledge/RAG、admin API、MCP、治理 | ✅ 已完成 Phase 0-3（670 tests passed, ruff clean）；入口为 `development-plan-s5.md` |

## 2. 文档状态定义

| 状态 | 含义 | 维护要求 |
| --- | --- | --- |
| `Baseline` | 已作为架构基线、契约或持续更新的事实来源 | 修改需要同步 tests / ADR / gap |
| `Draft` | 设计内容已完整但未开始实现 | 实现前需 review 和对齐契约 |
| `Implemented` | 已基本对应当前实现 | 实现变更必须同步更新 |
| `Partially Implemented` | 部分实现，仍有 gap | gap 必须写在 `implementation-gap.md` |
| `Planned` | 下一阶段设计输入，文档尚未创建或仅有大纲 | 实现前必须细化或拆 ADR |
| `Reference` | 外部资料或背景材料 | 不作为实现事实源 |

## 3. 按阶段的文档地图

### S0. 架构基线

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| `adr/0001-architecture-baseline.md` | Baseline | 平台边界和技术路线基线 |
| `00-baseline/mvp.md` | Baseline | MVP 做什么、不做什么 |
| `00-baseline/consistency-check.md` | Baseline | 文档一致性规则和术语规范 |
| `00-baseline/pre-development-checklist.md` | Reference | 开工前检查清单，当前用于回溯治理项 |

### S1. MVP 骨架和核心契约

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| `01-contracts/agent-request-response.md` | Baseline | 统一 Agent API 契约 |
| `01-contracts/agent-manifest-v1.md` | Baseline | Agent package manifest 契约 |
| `01-contracts/devflow-task-pack.md` | Baseline | AI coding agent 的结构化任务输入 |
| `02-architecture/agent-platform-design.md` | Partially Implemented | 生产平台总体设计 |
| `02-architecture/agent-platform-core-design.md` | Partially Implemented | 平台核心能力去业务化、动态工具加载、hook/policy 管线设计 |
| `02-architecture/ai-human-vibecoding-rd-platform.md` | Partially Implemented | 生产 + 研发一体化总体设计 |
| `implementation-gap.md` | Baseline | 当前实现和设计差距事实来源（持续更新） |

### S2. 生产化底座

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| `next-stage-design-plan.md` | Baseline | 下一阶段设计清单和顺序 |
| `manual-verification-guide.md` | Baseline | 平台功能模块手动验证指南（16 个模块，~30 min） |
| `development-plan-s2.md` | Historical Plan | S2-S4 开发计划和任务跟踪；记录基础组件实现历史，不作为当前完成度事实源 |
| `development-plan-s5.md` | Completed | S5 平台生产化与规模化执行计划；Phase 0-3 全部完成，670 tests passed |
| `05-production/persistence-storage-design.md` | Implemented | repository/migration 基础已实现；Registry/Deployment/Audit 主链路已在 S5 Phase 0 接入 |
| `05-production/package-artifact-release-design.md` | Partially Implemented | ArtifactStore Protocol 化 + LocalArtifactStore 已完成；远程 registry (S3/GitLab) 未实现 |
| `05-production/security-tenant-policy-design.md` | Partially Implemented | Scoped API key、tool permission、secret、脱敏已完成；ApprovalGate HITL 已完成；RBAC/scopes endpoint enforcement 待补 |
| `05-production/observability-eval-feedback-design.md` | Partially Implemented | OpenTelemetry 接入、@traced decorator、MetricsCollector 已完成；Langfuse 适配层已实现；结构化 trace event schema 待完善 |

### S3. Hermes 真接入

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| `03-runtime/hermes-runtime.md` | Partially Implemented | Hermes 接入边界和能力映射；Spike B 已完成 SDK 工具桥接和 fallback；官方 runtime/planner/memory/event stream 待 S6 |
| `03-runtime/hermes-backend-spike.md` | Implemented | Spike A + Spike B 均已完成；工具桥接、结果规范化、fallback 路径全部实现 |

### S4. AI 研发闭环

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| `04-devflow/gitlab.md` | Partially Implemented | GitLab 交付闭环设计 |
| `04-devflow/plane.md` | Partially Implemented | Plane Work Item、看板集成，以及 Plane / SCM / Coding Runner / Hermes 端到端交互流程 |
| `04-devflow/devflow-runner-workspace-design.md` | Draft | CodingAgentRunner、workspace、path guard |
| `04-devflow/devflow-state-sync-design.md` | Draft | Plane/GitLab 状态同步、幂等、重试、DLQ |

### S5. 平台生产化与规模化

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| `development-plan-s5.md` | Completed | S5 执行入口：Phase 0-3 全部完成（19 项任务，670 tests passed） |
| `06-scale/semantic-routing-policy-design.md` | Implemented | semantic routing rule schema 和 manifest 自动加载；`ManifestRoutingRule` 已实现 |
| `06-scale/model-gateway-design.md` | Implemented | 模型 provider、ChatResult、token/cost 统计；多 provider 路由已实现 |
| `06-scale/knowledge-rag-design.md` | Partially Implemented | KnowledgeService + WeaviateKnowledgeBackend 已实现；真实 vector backend 连接待补 |
| `99-reference/plane-docs-acquisition.md` | Reference | Plane API/MCP 文档获取方式 |
| `vendor/plane/*` | Reference | Plane OpenAPI 原始快照 |

## 4. 当前阶段阅读路径

### 4.1 继续实现平台底座

按顺序读：

1. `implementation-gap.md`
2. `next-stage-design-plan.md`
3. `01-contracts/agent-request-response.md`
4. `01-contracts/agent-manifest-v1.md`
5. 待补：`05-production/persistence-storage-design.md`
6. 待补：`05-production/package-artifact-release-design.md`

### 4.2 设计 Hermes 真接入

按顺序读：

1. `implementation-gap.md` 中 Runtime 与 Hermes 差距
2. `03-runtime/hermes-runtime.md`
3. 待补：`03-runtime/hermes-backend-spike.md`
4. `01-contracts/agent-manifest-v1.md` 中 runtime / extensions 规则

### 4.3 设计 AI 研发闭环

按顺序读：

1. `02-architecture/ai-human-vibecoding-rd-platform.md`
2. `01-contracts/devflow-task-pack.md`
3. `04-devflow/plane.md`，其中 `2.1` 节是 Plane / SCM / Coding Runner / Hermes 交互流程主入口
4. `04-devflow/gitlab.md`
5. 待补：`04-devflow/devflow-runner-workspace-design.md`
6. 待补：`04-devflow/devflow-state-sync-design.md`

### 4.4 新增业务 Agent

按顺序读：

1. `01-contracts/agent-manifest-v1.md`
2. `01-contracts/agent-request-response.md`
3. `02-architecture/agent-platform-design.md` 中 Agent Package 和路由部分
4. `01-contracts/devflow-task-pack.md`
5. `implementation-gap.md` 中当前限制

## 5. 文档维护规则

### 5.1 什么时候必须更新文档

| 变更 | 必须更新 |
| --- | --- |
| 修改 API request/response 字段 | `01-contracts/agent-request-response.md` |
| 修改 manifest 字段或校验规则 | `01-contracts/agent-manifest-v1.md` |
| 修改 task pack schema | `01-contracts/devflow-task-pack.md` |
| 修改 Plane API 使用方式 | `04-devflow/plane.md`、`99-reference/plane-docs-acquisition.md`、`vendor/plane/endpoints.md` |
| 修改 GitLab 流程或 CI gate | `04-devflow/gitlab.md` |
| 修改 Hermes 接入边界 | `03-runtime/hermes-runtime.md`，必要时新增 ADR |
| 修改部署、路由、持久化、DevFlow 实现状态 | `implementation-gap.md` |
| 新增重大技术路线 | `adr/` 新增 ADR |
| 修改文档阶段定义或文档状态 | `document-stage-map.md` 和 `README.md` |

### 5.2 新文档命名规则

使用主题名，不使用日期名：

```text
<topic>-design.md
<topic>-spike.md
<topic>-runbook.md
```

示例：

```text
05-production/persistence-storage-design.md
03-runtime/hermes-backend-spike.md
04-devflow/devflow-runner-workspace-design.md
```

### 5.3 每份设计文档建议包含的元信息

```text
Status: Draft | Baseline | Implemented | Partially Implemented | Superseded
Stage: S0 | S1 | S2 | S3 | S4 | S5
Owner: platform
Last verified against code: YYYY-MM-DD
```

## 6. 当前目录结构

```text
docs/
  README.md
  document-stage-map.md
  implementation-gap.md
  next-stage-design-plan.md
  00-baseline/
  01-contracts/
  02-architecture/
  03-runtime/
  04-devflow/
  05-production/
  06-scale/
  99-reference/
  adr/
  vendor/
```
