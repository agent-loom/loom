# Agent Platform 开工前文档索引

本文档索引用于约束正式开发前必须确认的设计、契约和工程边界。进入编码前，至少需要读完并确认“开工必读”部分。

## 开工必读

- [MVP 范围与验收标准](mvp.md)
- [Agent Request / Response 契约](contracts/agent-request-response.md)
- [Agent Manifest v1 契约](contracts/agent-manifest-v1.md)
- [DevFlow Task Pack 契约](devflow-task-pack.md)
- [ADR-0001 架构基线决策](adr/0001-architecture-baseline.md)
- [正式开工检查清单](pre-development-checklist.md)
- [设计文档一致性检查报告](consistency-check.md)

## 总体设计

- [多 Agent 平台设计](agent-platform-design.md)
- [AI + 人 + Vibe Coding 研发一体化平台设计](ai-human-vibecoding-rd-platform.md)
- [Hermes Runtime 能力利用设计](hermes-runtime.md)
- [GitLab 集成设计](gitlab.md)
- [Plane 集成设计](plane.md)
- [Plane API / MCP 文档获取方案](plane-docs-acquisition.md)

## 使用方式

1. 先确认 `mvp.md`，冻结第一阶段做什么、不做什么。
2. 再确认两个核心契约：`AgentRequest / AgentResponse` 和 `AgentManifest v1`。
3. 再确认 `DevFlow Task Pack`，约束 Codex / Claude Code / OpenHands 这类 coding agent 如何接入。
4. 最后确认 ADR 和开工检查清单。

正式开发开始后，新增重大技术决策必须追加 ADR；修改接口、manifest 或 task pack 必须同步更新对应契约文档。
