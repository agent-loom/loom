# 开发计划（S9：自进化 Agent 系统）

> Status: Draft
> Last updated: 2026-05-20

S9 目标：把运行反馈、eval failure、用户反馈、日志模式转化为可审计、可验证、可晋升的候选资产，并逐步打通 `Candidate -> Proposal -> Plane -> DevFlow -> MR` 的低风险自进化闭环。

核心原则：

```text
Platform-Owned Evolution
Hermes-Powered Intelligence

Hermes writes candidates, Platform promotes assets.
```

## 当前设计入口

| 文档 | 用途 |
| --- | --- |
| `07-evolution/README.md` | 自进化文档入口 |
| `07-evolution/self-evolving-agent-system.md` | 总体架构和边界 |
| `07-evolution/candidate-contract.md` | Candidate 契约 |
| `07-evolution/evolution-engine-design.md` | Evolution Engine |
| `07-evolution/memory-and-skills-design.md` | Memory / Skills |
| `07-evolution/improvement-proposal-contract.md` | ImprovementProposal |
| `07-evolution/risk-policy.md` | 风险策略 |
| `07-evolution/rollout-plan.md` | 分阶段路线 |

## Phase 0：文档冻结与契约确认

目标：收口设计，确认实现边界。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.0.1 | Candidate 契约冻结 | schema、状态机、promotion target、API 草案明确 | ✅ 文档完成 |
| 9.0.2 | Memory / Skills 边界冻结 | RuntimeMemory、EvolutionMemory、AgentSkills、Candidate Store 边界明确 | ✅ 文档完成 |
| 9.0.3 | Hermes / Platform 自进化边界冻结 | Hermes self-improvement 与 Platform evolution 区分明确 | ✅ 文档完成 |
| 9.0.4 | S9 差距进入 implementation-gap | 未实现能力进入事实源 | 🔶 待完成 |

## Phase 1：Candidate Store

目标：实现 Hermes/Platform 写入候选资产的最小持久化能力。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.1.1 | `EvolutionCandidate` domain model | 支持 candidate_id/type/status/risk/payload/evidence/promotion | ⬜ |
| 9.1.2 | Repository Protocol | `create/get/list/update_status/mark_promoted` | ⬜ |
| 9.1.3 | InMemory repository | 单测覆盖 create/list/status/promote | ⬜ |
| 9.1.4 | SQL repository + migration | tenant/agent/status/type 索引；payload JSON | ⬜ |
| 9.1.5 | Audit event | created/validated/promoted/rejected 事件可记录 | ⬜ |

## Phase 2：Candidate Validation / Promotion

目标：Candidate 不能直接生效，必须由 Platform 校验和晋升。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.2.1 | CandidateValidator | schema/evidence/scope/PII/secret/injection 校验 | ⬜ |
| 9.2.2 | Duplicate detection | 相同 agent/symptom/time window 不重复创建 | ⬜ |
| 9.2.3 | Risk consistency check | candidate risk 与 policy 匹配 | ⬜ |
| 9.2.4 | Promotion workflow | candidate -> EvolutionMemory / ImprovementProposal | ⬜ |
| 9.2.5 | Admin API | validate/approve/promote/reject/supersede | ⬜ |

## Phase 3：Eval Failure -> Candidate -> Proposal

目标：从 eval failure 生成候选资产，再晋升为正式 ImprovementProposal。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.3.1 | EvalFailure collector | 从 EvalRun/EvalReport 提取失败 case | ⬜ |
| 9.3.2 | HermesAnalyzer adapter | 输入脱敏 evidence，输出 `ProposalDraft` / `EvalCaseDraft` | ⬜ |
| 9.3.3 | Candidate 写入 | Hermes 输出写入 Candidate Store | ⬜ |
| 9.3.4 | Proposal promotion | Low-risk proposal_draft 可晋升 ImprovementProposal | ⬜ |
| 9.3.5 | 单元/集成测试 | eval failure 可生成 proposal，high risk 不进入 DevFlow | ⬜ |

## Phase 4：Candidate -> Plane Work Item

目标：正式 proposal 可以创建 Plane Work Item，但默认先人工确认。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.4.1 | Proposal -> Plane mapper | Markdown 描述、custom properties、labels | ⬜ |
| 9.4.2 | dispatch-to-plane API | 幂等创建 Work Item | ⬜ |
| 9.4.3 | Plane ownership 兼容 | Work Item 可被 ownership resolver 解析到 Agent | ⬜ |
| 9.4.4 | 审计链路 | proposal_id/candidate_id/work_item_id 可追踪 | ⬜ |

## Phase 5：Low-risk 自动 DevFlow

目标：低风险 prompt/eval/docs/contract tests 可以自动走 DevFlow MR。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.5.1 | Proposal -> TaskPack | allowed_paths/blocked_paths/validation/evidence 完整 | ⬜ |
| 9.5.2 | low-risk auto trigger | policy 允许后自动推进 Ready for AI Dev | ⬜ |
| 9.5.3 | Runner 安全检查增强 | blocked path 和 dangerous command 阻断 | ⬜ |
| 9.5.4 | MR report 增强 | 包含 candidate/proposal/evidence/risk/validation | ⬜ |
| 9.5.5 | E2E | Eval failure -> Candidate -> Proposal -> Plane -> MR | ⬜ |

## Phase 6：Memory / Skill Registry

目标：先做 EvolutionMemory 和 SkillRegistry，不急于 runtime 注入。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.6.1 | EvolutionMemory model/repo | evidence/confidence/trust_level/status | ⬜ |
| 9.6.2 | MemoryCandidate promotion | candidate -> EvolutionMemory | ⬜ |
| 9.6.3 | Skill manifest schema | `agents/<agent_id>/skills/**/manifest.yaml` | ⬜ |
| 9.6.4 | SkillRegistry scanner | list/get/version/status | ⬜ |
| 9.6.5 | Skill usage metrics 草案 | 先记录 proposal/MR 层 usage | ⬜ |

## Phase 7：Hermes Reviewer / Release Risk

目标：让 Hermes 在 MR 和发布前做第二审查员，但不拥有 merge/release 权。

| # | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| 9.7.1 | MR ReviewReport candidate | 分析是否修复 proposal、是否越权、eval 是否充分 | ⬜ |
| 9.7.2 | ReleaseRiskReport candidate | 分析变更影响 agent/tenant/channel 和历史失败模式 | ⬜ |
| 9.7.3 | Review feedback loop | accepted/rejected 反馈进入 Hermes self-improvement evidence | ⬜ |
| 9.7.4 | Admin UI 展示 | 查看 candidate/proposal/review/risk 链路 | ⬜ |

## 里程碑

| 里程碑 | 目标 | 验收 |
| --- | --- | --- |
| M1 | Candidate Store 可用 | 可创建/查询/校验/拒绝 candidate |
| M2 | Candidate Promotion 可用 | `ProposalDraft -> ImprovementProposal` 跑通 |
| M3 | Eval Failure 闭环 | eval failure 可生成 proposal |
| M4 | Plane 闭环 | proposal 可创建 Plane Work Item |
| M5 | Low-risk DevFlow 闭环 | 低风险 proposal 可生成 MR |
| M6 | Memory/Skill 治理 | EvolutionMemory 和 SkillRegistry 可查询和审计 |

## 非目标

S9 不做：

1. Hermes 直接改业务代码。
2. Hermes 直接写正式 Platform Memory。
3. Hermes 直接写 Agent Package。
4. 自动 merge MR。
5. 自动 prod 发布。
6. 复杂 RL 训练闭环。

