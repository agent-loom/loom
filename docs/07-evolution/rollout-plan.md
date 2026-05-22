# 自进化 Agent 系统落地路线

> Status: Phase 0-5, 9, 10 平台基础能力已实现，真实模型 Review Fork 候选生成闭环仍处于联调/不稳定状态（语义聚类、外围看板待后续增强）
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

实现说明：现已完整支持 Candidate Store 缓冲层；可以通过后台异步评审生成相应的候选资产草案，再经由 Promotion 流程自动或手动晋升为正式提案。


范围：

1. ✅ 从 eval failure 生成 `ImprovementProposal`。
2. ✅ 风险等级支持 Low/Medium/High。
3. ✅ 输出 API response。

验收：

1. ✅ 运行 eval 失败后可生成 proposal。
2. ✅ proposal 包含 `agent_id`、`risk`、`evidence`、`allowed_paths`、`validation`。
3. ✅ High risk 不会进入 DevFlow。

---

## Phase 1.5：Background Review Fork — ✅ 完成

目标：借鉴 Hermes self-improvement loop，引入受限后台 review fork，但只输出 candidate。

前置条件：Phase 1.6（Candidate Store）完成。

对应 development-plan：Phase 7。

范围：

1. ✅ AgentRun / EvalRun 完成后异步触发 review fork（见 `review_fork.py` 中的 `BackgroundReviewFork`）。
2. ✅ review fork 只读取脱敏 evidence。
3. ✅ review fork 只允许写 `ProposalDraft`、`MemoryCandidate`、`SkillDraft`、`EvalCaseDraft`、`ReviewReport`（使用 Scoped Toolset 严格限制）。
4. ✅ 禁止 shell、web unrestricted、git、deploy、secret 工具。
5. ✅ 记录 review_fork 审计事件（持久化至 `SqlReviewForkAuditRepository`）。
6. ✅ 引入基于被拒率的质量熔断电路（Quality Circuit Breaker）。

验收：

1. ✅ 普通 chat 请求不被 review fork 阻塞。
2. ✅ review fork 输出结构化 candidate。
3. ✅ review fork 无法调用 runner 或 deploy。
4. ✅ 生成的 candidate 都带 source evidence。

---

## Phase 1.6：Candidate Store + Promotion Workflow — ✅ 完成

目标：让 Hermes 能充分输出候选资产，同时确保 Platform 保留最终治理权。

对应 development-plan：Phase 6。

范围：

1. ✅ 实现 Candidate schema（7 种候选类型）。
2. ✅ 支持 candidate validate/promote/reject。
3. ✅ 支持 `MemoryCandidate -> EvolutionMemory`。
4. ✅ 支持 `ProposalDraft -> ImprovementProposal`。
5. ✅ 记录 promotion audit event。

验收：

1. ✅ Hermes 可以写 candidate。
2. ✅ candidate 不会直接影响 runtime。
3. ✅ candidate 晋升必须通过 Platform validation。
4. ✅ rejected candidate 能记录原因，用于 Hermes 自我改进。

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

## Phase 3.5：Runner Checkpoint + Command Guard — ✅ 完成

目标：在允许低风险自动改代码前补齐安全兜底。

对应 development-plan：Phase 5（9.5.1-9.5.2）。

范围：

1. ✅ runner 执行前创建 workspace checkpoint。
2. ✅ validation 前创建 checkpoint。
3. ✅ commit 前创建 checkpoint。
4. ✅ command guard 拦截 hard block 命令。
5. ✅ path guard 命中 blocked paths 时停止并保留现场。

验收：

1. ✅ blocked path 变更不会被 commit。
2. ✅ 危险命令不会执行。
3. ✅ validation 失败时能查看 diff、checkpoint 和 runner log。
4. ✅ MR report 包含 checkpoint id 和安全检查结果。

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

## Phase 5：生产化治理 — ✅ 核心机制完成

目标：进入可长期运行状态并打通治理通路。

对应 development-plan：Phase 5（指标）+ Phase 9（RuntimeMemory + Skill 注入）+ Phase 10（治理）。

范围：

1. ✅ ProposalRepository SQL 持久化。
2. ✅ EvolutionMemory model + InMemory/SQL repository + API（SqlEvolutionMemoryRepository）。
3. ✅ SkillRegistry scanner + API（SqlSkillRepository）。
4. ✅ RuntimeMemory 注入与 4 层作用域隔离 (S9 Phase 9)
5. ✅ Skill selector 与 runtime 注入及使用率审计统计 (S9 Phase 9)
6. ✅ Memory/Skill SQL 真实持久化存储（`persistence/sql.py` 完整支持）。
7. ✅ 滑动去重、质量熔断器（Circuit Breaker）、Agent 级自进化降级与手动控制。
8. ⬜ Admin UI 可视化面板（目前通过 REST API 交互）。
9. ✅ 自进化指标指标上报（`evolution/metrics.py` 统计成功率/被拒率）。
10. ✅ Agent 级暂停/挂起开关（`is_agent_suspended` 及 REST APIs）。
11. ⬜ Evolution Insights 图谱呈现。
12. ✅ Trajectory 历史演进路径及状态追溯（由 Git 与 Checkpoint 共建）。

验收：

1. ✅ 可按 agent/status/risk 查询 proposal 与 candidate。
2. ✅ 可暂停/重启某个特定 Agent 的自动进化触发。
3. ✅ 可统计自进化 MR 的通过率、回归熔断历史（`metrics.py` 和 `CircuitBreaker` 支持）。
4. ⬜ 全链路可视化大盘（Dashboard UI，外围配套功能）。

---

## 推荐下一步

Phase 1-3 基础版已完成。建议按以下顺序推进：

```text
🔴 已完成：Phase 3.5 Checkpoint + Guard + Phase 5 指标/E2E
  -> Phase 3 自动 DevFlow 已开启但无安全网，需立即补齐（已于 2026-05-20 完成）
  -> 同时验证全链路 E2E 跑通，建立质量基线（已完成）
  -> 对应 development-plan Phase 5

🔴 已完成：Phase 1.6 Candidate Store + Promotion
  -> Hermes-Platform 结构化缓冲层，为 Review Fork / Analyst 提供了基础层（已完成）
  -> 对应 development-plan Phase 6

🔴 已完成：RuntimeMemory + Skill 注入
  -> 让 Memory/Skill 产生实际 runtime 价值
  -> 依赖 Candidate Store，已于 2026-05-20 完成
  -> 对应 development-plan Phase 9

🔴 最高优先：Phase 1.5 Background Review Fork
  -> 异步后台分析 + 候选资产自动生成
  -> 依赖 Candidate Store
  -> 对应 development-plan Phase 7

🟠 第二优先：Phase 4 增强 + Hermes Analyst
  -> LLM 驱动 of 的分析替代规则
  -> 依赖 Review Fork
  -> 对应 development-plan Phase 8

🟠 第二优先：Phase 5 生产化治理
  -> SQL 持久化 + 限流 + Insights
  -> 对应 development-plan Phase 10
```

原因：

1. 自动 DevFlow 已经通过 Checkpoint + Guard 补齐了最紧急的安全守卫，并在 E2E 测试中全链路跑通。
2. E2E 验证证明现有自进化链路真正可用，建立了可量化的质量基线。
3. Candidate Store 缓冲层已经圆满建成，这为后续 Background Review Fork 和 RuntimeMemory 奠定了基础。
4. Review Fork 和 Analyst 是设计文档的核心差异化功能。
5. RuntimeMemory 让 Phase 4 的 Memory/Skill 投入产生实际价值。
