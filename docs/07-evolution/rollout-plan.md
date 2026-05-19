# 自进化 Agent 系统落地路线

> Status: Draft
> Stage: S9 Proposal
> Owner: platform
> Last updated: 2026-05-19

本文档定义自进化能力的阶段推进计划。目标是先建立低风险、可验证、可审计的闭环，再逐步扩大自动化范围。

## Phase 0：文档和契约

目标：明确边界，不写代码。

交付：

1. `self-evolving-agent-system.md`
2. `evolution-engine-design.md`
3. `memory-and-skills-design.md`
4. `improvement-proposal-contract.md`
5. `risk-policy.md`
6. `rollout-plan.md`

验收：

1. 明确 Hermes、Agent Platform、DevFlow、Plane、GitLab 的职责。
2. 明确自动化允许范围和禁止范围。
3. 明确 proposal 到 Plane/TaskPack/MR 的映射。
4. 明确 Candidate Store 与 Promotion Workflow。

## Phase 1：Eval Failure -> Proposal

目标：先不自动创建 Plane，只生成候选资产和提案。

范围：

1. 从 eval failure 生成 `ProposalDraft` / `MemoryCandidate`。
2. 从 agent run trace 生成 evidence 摘要。
3. 风险等级只支持 Low/High。
4. 输出 JSON/YAML 文件或 API response。

验收：

1. 运行 eval 失败后可生成 candidate。
2. candidate 可晋升为 `ImprovementProposal`。
3. proposal 包含 `agent_id`、`risk`、`evidence`、`allowed_paths`、`validation`。
4. High risk 不会进入 DevFlow。

## Phase 1.5：Background Review Fork

目标：借鉴 Hermes self-improvement loop，引入受限后台 review fork，但只输出 candidate。

范围：

1. AgentRun / EvalRun 完成后异步触发 review fork。
2. review fork 只读取脱敏 evidence。
3. review fork 只允许写 `ProposalDraft`、`MemoryCandidate`、`SkillDraft`、`EvalCaseDraft`、`ReviewReport`。
4. 禁止 shell、web unrestricted、git、deploy、secret 工具。
5. 记录 review_fork 审计事件。

验收：

1. 普通 chat 请求不被 review fork 阻塞。
2. review fork 输出结构化 candidate。
3. review fork 无法调用 runner 或 deploy。
4. 生成的 candidate 都带 source evidence。

## Phase 1.6：Candidate Store + Promotion Workflow

目标：让 Hermes 能充分输出候选资产，同时确保 Platform 保留最终治理权。

范围：

1. 实现 Candidate schema。
2. 支持 candidate validate/promote/reject。
3. 支持 `MemoryCandidate -> EvolutionMemory`。
4. 支持 `ProposalDraft -> ImprovementProposal`。
5. 记录 promotion audit event。

验收：

1. Hermes 可以写 candidate。
2. candidate 不会直接影响 runtime。
3. candidate 晋升必须通过 Platform validation。
4. rejected candidate 能记录原因，用于 Hermes 自我改进。

## Phase 2：Proposal -> Plane Work Item

目标：把低风险 proposal 转成 Plane Work Item。

范围：

1. 新增 `POST /api/v1/evolution/proposals/{id}/dispatch-to-plane`。
2. Plane Work Item 写入 proposal 摘要和 evidence。
3. Plane custom properties 写入 `agent_id`、`proposal_id`、`risk_level`。
4. 不自动推进 `Ready for AI Dev`，先人工确认。

验收：

1. Plane 上能看到 proposal 来源和验证命令。
2. 重复 proposal 不会重复创建多个 Work Item。
3. Work Item 能被现有 ownership resolver 正确映射到 Agent。

## Phase 3：Low Risk 自动 DevFlow

目标：低风险 proposal 可自动进入 DevFlow。

范围：

1. 只允许 prompt/eval/docs/contract tests。
2. 自动创建 Plane Work Item 后推进到 `Ready for AI Dev`。
3. 复用现有 Plane -> GitLab -> Codex runner 链路。
4. Runner 必须严格执行 `allowed_paths` / `blocked_paths`。

验收：

1. eval failure 能自动产生 MR。
2. MR 中包含 proposal ID、evidence、risk、validation report。
3. Codex 没有修改 blocked paths。
4. 新 eval case 被加入并通过。
5. 人类 review 仍是 merge 前必需步骤。

## Phase 3.5：Runner Checkpoint + Command Guard

目标：在允许低风险自动改代码前补齐安全兜底。

范围：

1. runner 执行前创建 workspace checkpoint。
2. validation 前创建 checkpoint。
3. commit 前创建 checkpoint。
4. command guard 拦截 hard block 命令。
5. path guard 命中 blocked paths 时停止并保留现场。

验收：

1. blocked path 变更不会被 commit。
2. 危险命令不会执行。
3. validation 失败时能查看 diff、checkpoint 和 runner log。
4. MR report 包含 checkpoint id 和安全检查结果。

## Phase 4：Feedback / Log Pattern -> Proposal

目标：从线上反馈和日志模式生成提案。

范围：

1. 用户负反馈聚合。
2. tool timeout/error 聚合。
3. routing error 聚合。
4. Hermes/Evolution Analyst 做摘要和 root cause 分类。

验收：

1. 重复用户反馈聚合为一个 proposal。
2. proposal 能追溯到 run/trace/tool/eval evidence。
3. Medium/High 自动降级为人工确认或报告。

## Phase 5：生产化治理

目标：进入可长期运行状态。

范围：

1. ProposalRepository SQL 持久化。
2. 去重、限流、暂停策略。
3. Admin UI 查看 proposals、evidence、状态。
4. 指标和告警。
5. Agent 级开关。
6. Evolution Insights。
7. Trajectory / RepairTrajectory 数据沉淀。

验收：

1. 可按 agent/tenant/risk/status 查询 proposal。
2. 可暂停某个 Agent 的自动触发。
3. 可查看 proposal 到 Plane/MR/eval/release 的完整链路。
4. 可统计自进化 MR 的通过率、回归率、平均修复时间。
5. 可统计 proposal 质量、review fork 命中率、memory 使用效果。

## 推荐下一步

建议 S9 第一批只做 Phase 1 + Phase 1.5 + Phase 2：

```text
Eval Failure
  -> Candidate
  -> Background Review Fork 审计
  -> Platform promote
  -> ImprovementProposal
  -> 人工确认
  -> Plane Work Item
```

原因：

1. 风险最低。
2. 能快速验证 proposal 契约是否正确。
3. 不会直接扩大自动代码修改范围。
4. 可以复用现有 Eval、Plane、DevFlow 基础设施。
