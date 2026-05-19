# 开发计划（S9：自进化 Agent 系统）

> Status: ✅ Phase 1-4 全部完成
> Last updated: 2026-05-20

本计划承接 S8（生产交付）。S9 核心目标：让平台具备**受治理的自进化能力**——从运行反馈自动发现问题、生成改进提案、通过 DevFlow 执行修改、经 Eval 验证后由人工 review 合并。

## 设计原则

1. **自驱动，不自治**：自动发现 + 自动提案 + 自动编码，但 review 和合并必须人工。
2. **证据驱动**：每个提案必须绑定 trace/eval/feedback 证据。
3. **低风险先行**：Phase 1 只允许自动改 prompt/eval/docs/contract tests。
4. **复用已有基础设施**：DevFlow + Plane + GitLab + EvalRunner + PathGuard + FeedbackIntelligence。
5. **Platform-Owned, Hermes-Powered**：Platform 拥有事实源和治理权，Hermes 提供分析能力。

## 设计文档

| 文档 | 职责 |
|---|---|
| `07-evolution/self-evolving-agent-system.md` | 总体架构和原则 |
| `07-evolution/evolution-engine-design.md` | Engine 详细设计（远景） |
| `07-evolution/improvement-proposal-contract.md` | ImprovementProposal 契约 |
| `07-evolution/risk-policy.md` | 风险策略和路径管控 |
| `07-evolution/memory-and-skills-design.md` | Memory/Skills 分层（Phase 3+） |
| `07-evolution/rollout-plan.md` | 阶段推进路线 |

## 当前基线

| 指标 | S8 结束 |
|---|---|
| 测试 | 1863 passed（+152 evolution/memory/skills） |
| DevFlow | Plane→GitLab 正向+反向流跑通，Code First MR Later |
| Hermes | 真实模型 E2E 跑通（z-ai/glm-5） |
| FeedbackIntelligence | 日志挖掘 → 候选需求 → Plane 工单（已实现） |

---

## S9 Phase 1：Evolution Engine 核心 — ✅ 完成

**目标**：实现从事件到提案的完整链路，含风险分类、证据绑定、去重、Plane 分发和 API。

### 已实现

| # | 任务 | 文件 | 状态 |
|---|---|---|---|
| 9.1.1 | `ImprovementProposal` 数据模型 | `src/agent_platform/evolution/models.py` | ✅ |
| 9.1.2 | 风险分类器 | `src/agent_platform/evolution/risk_classifier.py` | ✅ |
| 9.1.3 | `EvolutionProposalRepository` Protocol + InMemory | `src/agent_platform/evolution/repository.py` | ✅ |
| 9.1.4 | `EvolutionEngine` 核心逻辑 | `src/agent_platform/evolution/engine.py` | ✅ |

### 待完成

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.1.5 | Evolution API 端点 | proposals CRUD + dispatch-to-plane + dismiss | ✅ |
| 9.1.6 | 接入 app.py | 条件初始化 EvolutionEngine，注册 API 端点 | ✅ |
| 9.1.7 | 单元测试 | 模型/引擎/Repository/风险分类器全覆盖（68 tests） | ✅ |

### 数据模型

```python
# 核心契约
ImprovementProposal(
    proposal_id, title, summary,
    tenant_id, agent_id, task_type, source,
    status: draft → ready → dispatched → closed | dismissed,
    risk: RiskAssessment(level, reason, requires_human_*),
    root_cause: RootCause(category, confidence, explanation),
    evidence: list[Evidence],  # 至少 1 条
    proposed_changes: list[ProposedChange],
    allowed_paths, blocked_paths,
    validation: ValidationSpec(commands, regression_allowed),
    plane_work_item_id, gitlab_mr_iid,
)
```

### 风险策略（已实现）

| 路径 | 风险等级 |
|---|---|
| `agents/<id>/prompts/**`, `agents/<id>/evals/**`, `tests/contract/**`, `docs/**` | Low |
| `agents/<id>/tools/**`, `agents/<id>/adapters/**`, `manifest.yaml` | Medium |
| `src/agent_platform/**`, `deploy/**`, `.env`, `secrets/**` | Blocked |

### API 端点（设计）

```http
POST /api/v1/evolution/analyze          # 提交事件，生成提案
GET  /api/v1/evolution/proposals        # 列出提案
GET  /api/v1/evolution/proposals/{id}   # 查看提案详情
POST /api/v1/evolution/proposals/{id}/dispatch  # 分发到 Plane
POST /api/v1/evolution/proposals/{id}/dismiss   # 驳回
```

---

## S9 Phase 2：DevFlow 闭环 — 🔶 进行中

**目标**：低风险提案自动进入 DevFlow，Runner 修改 prompt/eval 并提交 MR。

**前置条件**：Phase 1 完成。

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.2.1 | Proposal → TaskPack 转换 | ImprovementProposal 映射为 DevelopmentTask | ✅ |
| 9.2.2 | 低风险自动推进 | risk=low 的 Plane Work Item 自动推进 Ready for AI Dev | ✅ |
| 9.2.3 | PathGuard 增强 | Runner 严格执行 proposal 的 allowed_paths/blocked_paths（通过 converter scope 映射） | ✅ |
| 9.2.4 | Eval 回归验证 | MR 必须通过现有 eval + 新增 eval case（converter 自动添加 eval 命令） | ✅ |
| 9.2.5 | E2E 验证 | eval failure → proposal → Plane → DevFlow → MR → eval pass | ⬜ |

---

## S9 Phase 3：FeedbackIntelligence 集成 + 持久化 — 🔶 进行中

**目标**：统一 FeedbackIntelligence 和 EvolutionEngine 的入口；SQL 持久化。

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.3.1 | FeedbackMiner → EvolutionEvent 适配 | RequirementProposal 转换为 EvolutionEvent | ✅ |
| 9.3.2 | 双路去重 | 避免 FeedbackIntelligence 和 EvolutionEngine 重复创建工单（通过 Engine 内置去重） | ✅ |
| 9.3.3 | `SqlProposalRepository` | evolution_proposals 表 + SQL 实现 + app.py 条件切换 | ✅ |
| 9.3.4 | Admin 提案管理 | 按 agent/status/risk 查询；dismiss/dispatch 操作（已通过 Evolution API 实现） | ✅ |
| 9.3.5 | 自进化指标 | 提案生成数/通过率/回归率/平均修复时间 | ⬜ |

---

## S9 Phase 4：Memory / Skills 平台化 — ✅ 完成

**目标**：引入 EvolutionMemory 和 SkillRegistry，但不急于 runtime 注入。

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 9.4.1 | EvolutionMemory model/repo | evidence/confidence/trust_level/status + InMemory 实现 | ✅ |
| 9.4.2 | Memory 写入和查询 API | 按 agent/tenant/type 过滤 + feedback 反馈 | ✅ |
| 9.4.3 | SkillRegistry scanner | `agents/<id>/skills/**` 索引 + manifest.yaml 解析 | ✅ |
| 9.4.4 | Skill 使用记录 | Skill CRUD API + scan 端点 + 使用统计字段 | ✅ |

---

## S8 遗留项（S9 并行处理）

| # | 任务 | 来源 | 状态 |
|---|---|---|---|
| 9.L.1 | Claude Code CLI 端到端 | S8 8.2.1 | ⬜ |
| 9.L.2 | Runner 日志持久化默认接入 | S8 8.2.3 | ⬜ |
| 9.L.3 | Plane bootstrap 脚本 | S8 8.3.1 | ⬜ |

---

## 依赖关系

```text
S9 Phase 1（Engine 核心）
    ↓
S9 Phase 2（DevFlow 闭环）
    ↓
S9 Phase 3（FeedbackIntelligence + 持久化）
    ↓
S9 Phase 4（Memory / Skills）
```

## 里程碑

| 里程碑 | 目标 | 验收 |
|---|---|---|
| M1: 提案引擎 | Phase 1 | eval failure → ImprovementProposal → API 可查询 → Plane 可分发 |
| M2: 自动修复 | Phase 2 | 低风险提案自动通过 DevFlow 生成 MR |
| M3: 统一闭环 | Phase 3 | FeedbackIntelligence + EvolutionEngine 统一 + SQL 持久化 |
| M4: 知识治理 | Phase 4 | EvolutionMemory + SkillRegistry 可查询和审计 |

## 非目标（S9 不做）

1. 自动 merge MR
2. 自动 prod 发布
3. Hermes 直接改业务代码或写正式 Agent Package
4. RL fine-tuning 闭环
5. 容器化安全沙箱
