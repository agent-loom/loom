# 自进化 Agent 系统落地路线

> Status: Phase 0-3 已实现（基础版），Phase 1.5/1.6/3.5/4增强/5 待实现
> Stage: S9
> Owner: platform
> Last updated: 2026-05-20

本文档定义自进化能力的阶段推进计划。目标是先建立低风险、可验证、可审计的闭环，再逐步扩大自动化范围。

与 `development-plan-s9.md` 的对照关系见该文件"与 rollout-plan.md 对照"一节。

---

## Phase 0：文档和契约 — ✅ 完成

目标：明确边界，不写代码。

交付：

1. `self-evolving-agent-system.md`
2. `evolution-engine-design.md`
3. `memory-and-skills-design.md`
4. `improvement-proposal-contract.md`
5. `risk-policy.md`
6. `rollout-plan.md`
7. `hermes-lessons-for-self-evolution.md`

验收：

1. ✅ 明确 Hermes、Agent Platform、DevFlow、Plane、GitLab 的职责。
2. ✅ 明确自动化允许范围和禁止范围。
3. ✅ 明确 proposal 到 Plane/TaskPack/MR 的映射。
4. ✅ 明确 Candidate Store 与 Promotion Workflow（设计层面）。

---

## Phase 1：Eval Failure -> Proposal — ✅ 完成

目标：从 eval failure 生成改进提案，含风险分类、证据绑定和 Plane 分发。

实现说明：实际实现直接生成 `ImprovementProposal`（而非设计中先生成 Candidate 再晋升），因为 Candidate Store 尚未实现。后续 Phase 1.6 实现 Candidate Store 后可补齐。

范围：

1. ✅ 从 eval failure 生成 `ImprovementProposal`。
2. ✅ 风险等级支持 Low/Medium/High。
3. ✅ 输出 API response。

验收：

1. ✅ 运行 eval 失败后可生成 proposal。
2. ✅ proposal 包含 `agent_id`、`risk`、`evidence`、`allowed_paths`、`validation`。
3. ✅ High risk 不会进入 DevFlow。

---

## Phase 1.5：Background Review Fork — ⬜ 待实现

目标：借鉴 Hermes self-improvement loop，引入受限后台 review fork，但只输出 candidate。

前置条件：Phase 1.6（Candidate Store）完成。

对应 development-plan：Phase 7。

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

---

## Phase 1.6：Candidate Store + Promotion Workflow — ⬜ 待实现

目标：让 Hermes 能充分输出候选资产，同时确保 Platform 保留最终治理权。

对应 development-plan：Phase 6。

范围：

1. 实现 Candidate schema（7 种候选类型）。
2. 支持 candidate validate/promote/reject。
3. 支持 `MemoryCandidate -> EvolutionMemory`。
4. 支持 `ProposalDraft -> ImprovementProposal`。
5. 记录 promotion audit event。

验收：

1. Hermes 可以写 candidate。
2. candidate 不会直接影响 runtime。
3. candidate 晋升必须通过 Platform validation。
4. rejected candidate 能记录原因，用于 Hermes 自我改进。

---

## Phase 2：Proposal -> Plane Work Item — ✅ 完成

目标：把低风险 proposal 转成 Plane Work Item。

范围：

1. ✅ `POST /api/v1/evolution/proposals/{id}/dispatch-to-plane`。
2. ✅ Plane Work Item 写入 proposal 摘要和 evidence。
3. ✅ Plane custom properties 写入 `agent_id`、`proposal_id`、`risk_level`。
4. ✅ 低风险自动推进到 `Ready for AI Dev`（dev Phase 2 增强）。

验收：

1. ✅ Plane 上能看到 proposal 来源和验证命令。
2. ✅ 重复 proposal 不会重复创建多个 Work Item（Engine 内置去重）。
3. ✅ Work Item 能被现有 ownership resolver 正确映射到 Agent。

---

## Phase 3：Low Risk 自动 DevFlow — ✅ 完成

目标：低风险 proposal 可自动进入 DevFlow。

范围：

1. ✅ 只允许 prompt/eval/docs/contract tests。
2. ✅ 自动创建 Plane Work Item 后推进到 `Ready for AI Dev`。
3. ✅ 复用现有 Plane -> GitLab -> Codex runner 链路。
4. ✅ Runner 通过 ProposalToTaskPackConverter 严格执行 `allowed_paths` / `blocked_paths`。

验收：

1. ✅ eval failure 能自动产生 MR（链路已连通）。
2. ✅ MR 中包含 proposal ID、evidence、risk、validation commands。
3. ✅ Codex 不修改 blocked paths（PathGuard scope 映射）。
4. ✅ 新 eval case 被 converter 自动添加到 validation commands。
5. ✅ 人类 review 仍是 merge 前必需步骤。

---

## Phase 3.5：Runner Checkpoint + Command Guard — ⬜ 待实现

目标：在允许低风险自动改代码前补齐安全兜底。

对应 development-plan：Phase 10（9.10.4-9.10.5）。

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

---

## Phase 4：Feedback / Log Pattern -> Proposal — 🔶 基础版完成

目标：从线上反馈和日志模式生成提案。

已实现（基础版）：
1. ✅ FeedbackMiner → EvolutionEvent 适配器（规则驱动）。
2. ✅ 双路去重（避免 FeedbackIntelligence 和 EvolutionEngine 重复创建工单）。
3. ✅ SQL 持久化（SqlProposalRepository）。

待实现（增强版）：
1. ⬜ 用户负反馈聚合（聚类维度：agent/failure_type/tool/channel/tenant）。
2. ⬜ tool timeout/error 聚合。
3. ⬜ routing error 聚合。
4. ⬜ Hermes/Evolution Analyst 做 LLM 摘要和 root cause 分类（对应 development-plan Phase 8）。

验收：

1. ✅ 重复用户反馈聚合为一个 proposal（Engine 内置 24h 去重窗口）。
2. ✅ proposal 能追溯到 evidence。
3. ⬜ Medium/High 自动降级为人工确认或报告（基础版已有风险分类，增强版需聚合分析）。

---

## Phase 5：生产化治理 — 🔶 部分完成

目标：进入可长期运行状态。

对应 development-plan：Phase 5（指标）+ Phase 9（RuntimeMemory）+ Phase 10（治理）。

范围：

1. ✅ ProposalRepository SQL 持久化。
2. ✅ EvolutionMemory model + InMemory repository + API。
3. ✅ SkillRegistry scanner + API。
4. ⬜ Memory/Skill SQL 持久化。
5. ⬜ 去重、限流、暂停策略。
6. ⬜ Admin UI 查看 proposals、evidence、状态。
7. ⬜ 自进化指标和告警。
8. ⬜ Agent 级开关。
9. ⬜ Evolution Insights。
10. ⬜ Trajectory / RepairTrajectory 数据沉淀。

验收：

1. ✅ 可按 agent/status/risk 查询 proposal。
2. ⬜ 可暂停某个 Agent 的自动触发。
3. ⬜ 可查看 proposal 到 Plane/MR/eval/release 的完整链路。
4. ⬜ 可统计自进化 MR 的通过率、回归率、平均修复时间。
5. ⬜ 可统计 proposal 质量、review fork 命中率、memory 使用效果。

---

## 推荐下一步

Phase 1-3 基础版已完成。建议按以下顺序推进：

```text
第一优先：Phase 5 指标 + E2E 验证
  -> 验证现有闭环真正跑通
  -> 建立质量基线

第二优先：Phase 1.6 Candidate Store + Promotion
  -> Hermes-Platform 缓冲层
  -> 后续 Review Fork 和 Analyst 的前置条件

第三优先：Phase 1.5 Background Review Fork
  -> 异步后台分析
  -> 依赖 Candidate Store

第四优先：Phase 3.5 Checkpoint + Command Guard
  -> 安全兜底
  -> 生产化前必须完成

第五优先：Phase 4 增强 + Hermes Analyst
  -> LLM 驱动的分析替代规则
  -> 依赖 Review Fork
```

原因：

1. 先验证现有链路真正可用（E2E），再扩展功能。
2. Candidate Store 是多个后续 Phase 的基础。
3. Review Fork 和 Analyst 是设计文档的核心差异化功能。
4. Checkpoint + Guard 是生产化前的安全门槛。
