# 开发计划（S9：自进化 Agent 系统）

> Status: Phase 7 待开始
> Last updated: 2026-05-20

本计划承接 S8（生产交付）。S9 核心目标：让平台具备**受治理的自进化能力**——从运行反馈自动发现问题、生成改进提案、通过 DevFlow 执行修改、经 Eval 验证后由人工 review 合并。

## 设计原则

1. **自驱动，不自治**：自动发现 + 自动提案 + 自动编码，但 review 和合并必须人工。
2. **证据驱动**：每个提案必须绑定 trace/eval/feedback 证据。
3. **低风险先行**：先只允许自动改 prompt/eval/docs/contract tests。
4. **复用已有基础设施**：DevFlow + Plane + GitLab + EvalRunner + PathGuard + FeedbackIntelligence。
5. **Platform-Owned, Hermes-Powered**：Platform 拥有事实源和治理权，Hermes 提供分析能力。
6. **Hermes writes candidates, Platform promotes assets**：Hermes 写候选资产，Platform 校验和晋升。

## 设计文档

| 文档 | 职责 |
|---|---|
| `07-evolution/self-evolving-agent-system.md` | 总体架构和原则 |
| `07-evolution/evolution-engine-design.md` | Engine 详细设计（含 Candidate Store、Review Fork） |
| `07-evolution/improvement-proposal-contract.md` | ImprovementProposal 契约 |
| `07-evolution/risk-policy.md` | 风险策略、路径管控、Toolset 分层、Command Guard |
| `07-evolution/memory-and-skills-design.md` | Memory/Skills 四层设计（Runtime/Evolution/Skills/Candidate） |
| `07-evolution/rollout-plan.md` | 阶段推进路线（与本文件对应） |
| `07-evolution/hermes-lessons-for-self-evolution.md` | Hermes 源码调研与借鉴 |

## 当前基线

| 指标 | 值 |
|---|---|
| 测试 | 1863 passed（+152 evolution/memory/skills） |
| DevFlow | Plane→GitLab 正向+反向流跑通，Code First MR Later |
| Hermes | 真实模型 E2E 跑通（z-ai/glm-5），作为 **Runtime Backend** 已集成 |
| FeedbackIntelligence | 日志挖掘 → 候选需求 → Plane 工单（已实现） |

## Hermes 两个角色

| 角色 | 说明 | 状态 |
|---|---|---|
| **Runtime Backend** | 运行业务 Agent（HermesRuntimeBackend + hermes_echo） | ✅ 已集成 |
| **Evolution Analyst** | 分析 trace/feedback，生成候选资产和提案 | ⬜ 仅设计，零代码 |

当前 EvolutionEngine 是纯规则驱动（event_type → root_cause 映射，path → risk 分类），不涉及 LLM 分析。Hermes 作为 Analyst 接入后，才能实现真正的智能归因和候选生成。

---

## Phase 1：Evolution Engine 核心 — ✅ 完成

**目标**：实现从事件到提案的完整链路，含风险分类、证据绑定、去重、Plane 分发和 API。

**对应 rollout-plan**：Phase 1 + Phase 2。

| # | 任务 | 文件 | 状态 |
|---|---|---|---|
| 9.1.1 | `ImprovementProposal` 数据模型 | `evolution/models.py` | ✅ |
| 9.1.2 | 风险分类器 | `evolution/risk_classifier.py` | ✅ |
| 9.1.3 | `EvolutionProposalRepository` Protocol + InMemory | `evolution/repository.py` | ✅ |
| 9.1.4 | `EvolutionEngine` 核心逻辑 | `evolution/engine.py` | ✅ |
| 9.1.5 | Evolution API 端点 | proposals CRUD + dispatch + dismiss | ✅ |
| 9.1.6 | 接入 app.py | 条件初始化 + 注册端点 | ✅ |
| 9.1.7 | 单元测试 | 68 tests 全覆盖 | ✅ |

---

## Phase 2：DevFlow 闭环 — ✅ 完成

**目标**：低风险提案自动进入 DevFlow，Runner 修改 prompt/eval 并提交 MR。

**对应 rollout-plan**：Phase 3。

| # | 任务 | 状态 |
|---|---|---|
| 9.2.1 | Proposal → TaskPack 转换（ProposalToTaskPackConverter） | ✅ |
| 9.2.2 | 低风险自动推进（auto_dispatch_if_low_risk → Ready for AI Dev） | ✅ |
| 9.2.3 | PathGuard 增强（Runner 执行 proposal 的 allowed/blocked paths） | ✅ |
| 9.2.4 | Eval 回归验证（converter 自动添加 eval 命令） | ✅ |

---

## Phase 3：FeedbackIntelligence 集成 + 持久化 — ✅ 完成

**目标**：统一 FeedbackIntelligence 和 EvolutionEngine 的入口；SQL 持久化。

**对应 rollout-plan**：Phase 4（基础版）。

| # | 任务 | 状态 |
|---|---|---|
| 9.3.1 | FeedbackMiner → EvolutionEvent 适配（feedback_adapter） | ✅ |
| 9.3.2 | 双路去重（Engine 内置 dedup，24h 窗口） | ✅ |
| 9.3.3 | `SqlProposalRepository`（evolution_proposals 表 + SQL） | ✅ |
| 9.3.4 | Admin 提案管理（按 agent/status/risk 查询、dismiss/dispatch） | ✅ |

---

## Phase 4：Memory / Skills 平台化（最小版） — ✅ 完成

**目标**：引入 EvolutionMemory 和 SkillRegistry 模型、InMemory Repository 和 API。

**对应 memory-and-skills-design.md**：Phase 1 + Phase 2。

| # | 任务 | 状态 |
|---|---|---|
| 9.4.1 | EvolutionMemory model/repo（trust_score + feedback 机制） | ✅ |
| 9.4.2 | Memory 写入和查询 API（按 agent/tenant/type 过滤 + feedback） | ✅ |
| 9.4.3 | SkillRegistry scanner（`agents/<id>/skills/**` 索引） | ✅ |
| 9.4.4 | Skill CRUD API + scan 端点 + 使用统计 | ✅ |

---

## Phase 5：安全加固 + E2E 验证 — ✅ 完成

**目标**：Phase 2 已开启自动 DevFlow，但安全兜底尚未到位。本 Phase 补齐 Checkpoint + Command Guard，并验证全链路 E2E。

**对应 rollout-plan**：Phase 3.5 + Phase 5（指标部分）。

**工作量**：M（3-5 天）

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.5.1 | Runner Checkpoint | before_runner / before_validation / before_commit / after_commit 四个检查点；validation 失败时保留现场 | ✅ |
| 9.5.2 | Command Guard | Hard Block 命令拦截（rm -rf / mkfs / sudo 等）；blocked path 变更拒绝 commit | ✅ |
| 9.5.3 | E2E 验证 | eval failure → proposal → Plane → DevFlow → MR → eval pass 全链路跑通 | ✅ |
| 9.5.4 | 自进化指标 | 提案生成数 / auto-dispatch 成功率 / proposal outcome（merged/rejected/abandoned） | ✅ |

**为什么提前**：Phase 2 让系统可以自动改代码并提 MR，没有 Checkpoint + Guard 就是在没有安全网的情况下运行。这是风险缺口，必须优先补齐。

---

## Phase 6：Candidate Store + Promotion Workflow — ✅ 完成

**目标**：实现 Hermes-Platform 之间的结构化缓冲层。这是 Phase 7-8 的基础。

**对应 rollout-plan**：Phase 1.6。
**设计出处**：`memory-and-skills-design.md` §2.4 + §6.4，`evolution-engine-design.md` §9.1。

**工作量**：L（5-7 天）

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.6.1 | Candidate 数据模型 | 7 种候选类型（MemoryCandidate / SkillDraft / EvalCaseDraft / ProposalDraft / ReviewReport / ReleaseRiskReport / TaskPackDraft）；统一 base 模型 + payload 扩展 | ✅ |
| 9.6.2 | CandidateRepository Protocol + InMemory | create / get / list（按 type/agent/status 过滤）/ update_status | ✅ |
| 9.6.3 | Promotion 状态机 | draft → validated → approved → promoted / rejected / superseded | ✅ |
| 9.6.4 | Candidate 验证管道 | schema 校验 + evidence 校验 + scope 校验 + PII/injection scan + 去重 | ✅ |
| 9.6.5 | Candidate API 端点 | GET list / GET detail / POST validate / POST promote / POST reject | ✅ |
| 9.6.6 | 晋升执行器 | MemoryCandidate → EvolutionMemory；ProposalDraft → ImprovementProposal；SkillDraft/EvalCaseDraft → DevFlow MR | ✅ |

**核心约束**：
- Candidate 不是事实源，不直接影响 runtime
- Low risk 可 policy 自动晋升；Medium 需 owner 确认；High 不自动晋升
- 现有 EvolutionEngine.process_event 可选择性经 Candidate Store 中转（渐进迁移）

---

## Phase 7：Background Review Fork — ⬜ 待实现

**目标**：在 AgentRun/EvalRun 完成后异步触发受限后台 review，输出候选资产。

**对应 rollout-plan**：Phase 1.5。
**设计出处**：`evolution-engine-design.md` §2.1。

**前置条件**：Phase 6。
**工作量**：M（3-5 天）

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.7.1 | ReviewFork 执行器 | 异步 sidecar，不阻塞用户请求；超时/异常不影响主链路 | ⬜ |
| 9.7.2 | Scoped Toolset | 只允许 proposal.write / memory.write / eval_draft.write / evidence.read；禁止 shell / web / git / deploy / secret | ⬜ |
| 9.7.3 | 触发器 | AgentRun / EvalRun / UserFeedback / DevFlowJob / MR review 完成时触发 | ⬜ |
| 9.7.4 | 输出 → Candidate Store | 结构化 Candidate 写入 CandidateRepository | ⬜ |
| 9.7.5 | 审计 | review_fork_id / source_event / input_evidence / output_type / candidate_id / model | ⬜ |
| 9.7.6 | 质量熔断 | 连续 N 次低质量输出（rejected candidate 占比 > 阈值）时暂停该 agent 的 fork | ⬜ |

---

## Phase 8：Hermes Evolution Analyst — ⬜ 待实现

**目标**：用 LLM 替代纯规则的 root cause 分类和 evidence 摘要，实现真正的智能分析。同时覆盖 rollout Phase 4 增强版（feedback/log 聚合分析）。

**对应 rollout-plan**：Phase 4 增强版。
**设计出处**：`self-evolving-agent-system.md` §4，`hermes-lessons-for-self-evolution.md` §2.1-2.2。

**前置条件**：Phase 7。
**工作量**：L（5-7 天）

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.8.1 | HermesEvolutionAnalyst adapter | 调用 Hermes Runtime 做 LLM root cause 分析（替代 engine.py 的规则映射） | ⬜ |
| 9.8.2 | LLM evidence 摘要 | 对 trace/feedback cluster 做结构化摘要（替代简单拼接） | ⬜ |
| 9.8.3 | Feedback/Log 聚合分析 | 用户负反馈聚合 + tool timeout/error 聚合 + routing error 聚合 | ⬜ |
| 9.8.4 | LLM proposal 生成 | 生成更精准的 proposed_changes + validation commands | ⬜ |
| 9.8.5 | Analyst 质量反馈闭环 | proposal 被接受/拒绝时反馈给 Analyst，优化后续分析策略 | ⬜ |

**设计要点**：
- Hermes 自进化"分析者能力"，Platform 自进化"业务 Agent 能力"
- Analyst 输出必须经 Candidate Store → Promotion Workflow
- 规则引擎保留为 fallback，LLM 不可用时降级

---

## Phase 9：RuntimeMemory + Skill Runtime 注入 — ⬜ 待实现

**目标**：让 Memory/Skills 真正影响线上行为——通过 ContextBuilder 注入 runtime context。

**对应 memory-and-skills-design.md**：Phase 3 + Phase 4。

**前置条件**：Phase 6（Candidate Store 作为 memory 来源之一）。可并行于 Phase 7-8。
**工作量**：L（5-7 天）

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.9.1 | RuntimeMemory 模型 | scope（session/user/tenant/agent）+ TTL + privacy_level + status | ⬜ |
| 9.9.2 | RuntimeMemoryRepository | Protocol + InMemory（SQL 在 Phase 10 补齐） | ⬜ |
| 9.9.3 | ContextBuilder memory 注入 | 按 scope / TTL / policy / token budget 注入；标注 "context, not truth" | ⬜ |
| 9.9.4 | SkillSelector | 根据 agent / task_type / channel 选择 active skill；限制注入数量 | ⬜ |
| 9.9.5 | Skill runtime 注入 | ContextBuilder 注入 skill 摘要 + skill.used 审计事件 | ⬜ |
| 9.9.6 | 隔离测试 | 多租户/多用户 memory 不串读；TTL 过期后不注入 | ⬜ |

---

## Phase 10：生产化治理 — ⬜ 待实现

**目标**：全面 SQL 持久化 + 运营治理能力，进入可长期运行的生产状态。

**对应 rollout-plan**：Phase 5。
**工作量**：L（5-7 天）

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.10.1 | Memory/Skill/Candidate SQL 持久化 | 三组 SQL Repository + migration | ⬜ |
| 9.10.2 | 去重 / 限流 / 暂停策略 | 同类 proposal 连续被拒降级；Agent 级自动触发开关 | ⬜ |
| 9.10.3 | Admin UI 数据接口 | 按 agent/tenant/risk/status 查询 proposal/candidate/memory/skill | ⬜ |
| 9.10.4 | Evolution Insights | agent failure rate / eval regression trend / proposal quality / mean time to fix | ⬜ |
| 9.10.5 | Trajectory 数据沉淀 | AgentRun → RuntimeTrajectory / DevFlowJob → RepairTrajectory / HumanReview → PreferenceSignal | ⬜ |

---

## S8 遗留项（并行处理）

| # | 任务 | 来源 | 状态 |
|---|---|---|---|
| 9.L.1 | Claude Code CLI 端到端 | S8 8.2.1 | ⬜ |
| 9.L.2 | Runner 日志持久化默认接入 | S8 8.2.3 | ⬜ |
| 9.L.3 | Plane bootstrap 脚本 | S8 8.3.1 | ⬜ |

---

## 依赖关系与执行顺序

```text
Phase 1-4（已完成）
    │
    ▼
Phase 5（安全加固 + E2E）     ← 🔴 最高优先级：补齐安全网
    │
    ▼
Phase 6（Candidate Store）     ← 后续功能的基础节点
    │
    ├──▶ Phase 7（Review Fork）     ← 依赖 6
    │        │
    │        ▼
    │    Phase 8（Hermes Analyst）   ← 依赖 7
    │
    └──▶ Phase 9（RuntimeMemory）   ← 依赖 6，可并行于 7-8
    
Phase 7-9 完成后 ──▶ Phase 10（生产化治理）
```

## 推荐执行顺序

| 优先级 | Phase | 理由 | 工作量 |
|---|---|---|---|
| 🔴 P0 | 5 安全加固 + E2E | 自动 DevFlow 已开启但无安全网，风险缺口 | M |
| 🟠 P1 | 6 Candidate Store | Phase 7/8/9 的前置条件；核心架构组件 | L |
| 🟡 P2 | 7 Review Fork | 自动化候选生成，减少人工触发 | M |
| 🟡 P2 | 9 RuntimeMemory | 可与 7 并行；让 Memory/Skill 产生实际价值 | L |
| 🔵 P3 | 8 Hermes Analyst | LLM 智能分析，依赖 Review Fork 就位 | L |
| 🔵 P3 | 10 生产化治理 | 全面持久化 + 运营工具 | L |

## 与 rollout-plan.md 对照

| rollout-plan Phase | development-plan Phase | 状态 |
|---|---|---|
| Phase 0: 文档和契约 | — | ✅ |
| Phase 1: Eval Failure → Proposal | Phase 1 | ✅ |
| Phase 2: Proposal → Plane Work Item | Phase 1 | ✅ |
| Phase 3: Low Risk 自动 DevFlow | Phase 2 | ✅ |
| Phase 3.5: Runner Checkpoint + Command Guard | **Phase 5**（9.5.1-9.5.2） | ✅ |
| Phase 4: Feedback / Log Pattern → Proposal | Phase 3（基础版）+ **Phase 8**（增强版） | 🔶 |
| Phase 1.6: Candidate Store + Promotion | **Phase 6** | ✅ |
| Phase 1.5: Background Review Fork | **Phase 7** | ⬜ |
| Phase 5: 生产化治理 | **Phase 10** | ⬜ |

## 里程碑

| 里程碑 | Phase | 验收 |
|---|---|---|
| M1: 提案引擎 | 1 | ✅ eval failure → Proposal → API → Plane |
| M2: 自动修复 | 2 | ✅ 低风险提案自动 DevFlow 生成 MR |
| M3: 统一闭环 | 3 | ✅ FeedbackIntelligence + EvolutionEngine + SQL |
| M4: 知识治理 | 4 | ✅ EvolutionMemory + SkillRegistry 可查询 |
| M5: 安全可验证 | 5 | ✅ Checkpoint + Guard 就位 + E2E 全链路跑通 |
| M6: 候选缓冲 | 6 | ✅ Candidate Store + Promotion Workflow 运行 |
| M7: 后台 Review | 7 | AgentRun 后自动 Review Fork → Candidate |
| M8: 智能分析 | 8 | Hermes Analyst 输出高质量候选资产 |
| M9: Runtime 注入 | 9 | Memory/Skill 通过 ContextBuilder 注入线上 |
| M10: 生产就绪 | 10 | SQL + 限流 + Insights + Trajectory |

## 非目标（S9 不做）

1. 自动 merge MR
2. 自动 prod 发布
3. Hermes 直接改业务代码或写正式 Agent Package
4. RL fine-tuning 闭环
5. 容器化安全沙箱
