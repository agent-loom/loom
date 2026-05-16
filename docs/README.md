# Agent Platform 文档索引

本文档索引用于约束设计、契约、实现差距和工程边界。继续开发或重构前，至少需要读完并确认“必读”部分。

## 必读

- [文档阶段管理地图](document-stage-map.md)
- [MVP 范围与验收标准](00-baseline/mvp.md)
- [Agent Request / Response 契约](01-contracts/agent-request-response.md)
- [Agent Manifest v1 契约](01-contracts/agent-manifest-v1.md)
- [DevFlow Task Pack 契约](01-contracts/devflow-task-pack.md)
- [ADR-0001 架构基线决策](adr/0001-architecture-baseline.md)
- [正式开工检查清单](00-baseline/pre-development-checklist.md)
- [设计文档一致性检查报告](00-baseline/consistency-check.md)
- [实现与设计差距分析](implementation-gap.md)
- [下一阶段技术设计计划](next-stage-design-plan.md)
- [S5 平台生产化与规模化开发计划](development-plan-s5.md)

## 总体设计

- [多 Agent 平台设计](02-architecture/agent-platform-design.md)
- [Agent Platform 核心功能设计](02-architecture/agent-platform-core-design.md)
- [AI + 人 + Vibe Coding 研发一体化平台设计](02-architecture/ai-human-vibecoding-rd-platform.md)
- [Hermes Runtime 能力利用设计](03-runtime/hermes-runtime.md)
- [GitLab 集成设计](04-devflow/gitlab.md)
- [Plane 集成设计](04-devflow/plane.md)
- [Plane API / MCP 文档获取方案](99-reference/plane-docs-acquisition.md)

## 按阶段阅读

| 阶段 | 目标 | 入口文档 |
| --- | --- | --- |
| S0 架构基线 | 理解平台边界、MVP、核心决策 | [文档阶段管理地图](document-stage-map.md)、[ADR-0001](adr/0001-architecture-baseline.md) |
| S1 MVP 骨架 | 理解当前已实现能力和核心契约 | [实现与设计差距分析](implementation-gap.md)、[Agent Request / Response 契约](01-contracts/agent-request-response.md)、[Agent Manifest v1 契约](01-contracts/agent-manifest-v1.md) |
| S2 生产化底座 | 设计持久化、制品、发布、权限、观测 | [下一阶段技术设计计划](next-stage-design-plan.md) |
| S3 Hermes 真接入 | 验证真实 Hermes RuntimeBackend | [Hermes Runtime 能力利用设计](03-runtime/hermes-runtime.md) |
| S4 AI 研发闭环 | 设计 runner、workspace、Plane/GitLab 状态同步 | [AI + 人 + Vibe Coding 研发一体化平台设计](02-architecture/ai-human-vibecoding-rd-platform.md)、[DevFlow Task Pack 契约](01-contracts/devflow-task-pack.md) |
| S5 平台生产化与规模化 | 主链路可靠性校准、Hermes SDK、knowledge/RAG、MCP、观测、治理 | [S5 平台生产化与规模化开发计划](development-plan-s5.md)、[下一阶段技术设计计划](next-stage-design-plan.md) |

## 使用方式

1. 先确认 `document-stage-map.md`，明确当前工作属于哪个阶段。
2. 再确认 `implementation-gap.md`，明确当前实现和目标设计之间的真实差距。
3. 如果改协议或 package，确认 `AgentRequest / AgentResponse` 和 `AgentManifest v1`。
4. 如果改研发自动化，确认 `DevFlow Task Pack`、Plane 和 GitLab 设计。
5. 如果新增重大技术路线，新增 ADR。

正式开发开始后，新增重大技术决策必须追加 ADR；修改接口、manifest 或 task pack 必须同步更新对应契约文档。
