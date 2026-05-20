# Hermes 自进化能力调研与借鉴

> Status: Completed
> Stage: S9
> Owner: platform
> Last updated: 2026-05-20

本文档记录对本地 Hermes Agent 源码的调研结论，并说明哪些能力适合借鉴到 Agent Platform 的自进化体系。

本地源码路径：

```text
/Users/errocks/py-workspace/hermes-agent
```

## 1. 总体判断

Hermes 不是单纯 runtime，而是一个个人/多渠道 Agent OS：

1. Agent loop。
2. tool registry / toolsets。
3. memory / skills。
4. gateway / cron / webhook automation。
5. kanban 多 worker 协作。
6. approval / path security / checkpoint。
7. trajectory / RL training data。
8. usage insights。

它的自进化不是“业务 Agent 自动发布生产”，而是更偏：

```text
会话后后台 review
  -> 保存/更新 memory
  -> 保存/更新 skills
  -> 形成 routine / automation / kanban worker
  -> 沉淀 trajectory / RL data
```

对 Agent Platform 的启发是：**自进化必须先做成受限的后台分析与提案系统，而不是直接自改代码和发布。**

更合适的总体模式：

```text
Platform-Owned Evolution
Hermes-Powered Intelligence
```

Platform 拥有事实源、权限、审计、版本和发布；Hermes 提供分析、记忆候选、技能草案、任务规划、MR/release review 和自己的分析能力自进化。

## 2. 值得借鉴的能力

### 2.1 Background Review Fork

Hermes release 中明确提到 self-improvement loop：每轮之后后台 review fork 判断要不要保存或更新 memory/skills，并且限制 toolset，只允许 memory + skills，避免 shell/web 扩散。

可借鉴到 Agent Platform：

```text
AgentRun completed
  -> Evolution Review Fork
  -> 只读 trace/eval/feedback
  -> 只能写 Memory / SkillDraft / ImprovementProposal
  -> 禁止 shell / deploy / prod tools
```

平台实现原则：

1. review fork 是异步 sidecar，不阻塞用户请求。
2. review fork 必须有专用 scoped toolset。
3. review fork 输出必须是结构化 proposal，不允许直接改代码。
4. review fork 需要记录 prompt、输入证据、输出 proposal 和风险等级。
5. review fork 写入 Candidate Store，而不是直接写正式 Platform Memory 或 Agent Package。

参考文件：

```text
RELEASE_v0.12.0.md
tools/memory_tool.py
```

### 2.2 Curated Memory

Hermes memory 的关键点：

1. `MEMORY.md` 和 `USER.md` 分离。
2. session start 注入 frozen snapshot。
3. session 中写文件，但不改变当前 system prompt。
4. memory 有字符限制。
5. 写入前扫描 prompt injection、secret exfiltration 和 invisible unicode。
6. 使用文件锁和 atomic write。

Agent Platform 不能直接用个人文件 memory，但可以借鉴模型：

```text
EvolutionMemory
  tenant_id
  agent_id
  environment
  memory_type: issue_pattern | tool_quirk | user_feedback | routing_hint
  source_evidence
  trust_level
  ttl / retention
```

关键约束：

1. tenant/agent/environment 隔离。
2. memory 进入 prompt 前必须脱敏和 injection scan。
3. memory 不作为事实，只作为上下文。
4. memory 更新必须保留 evidence。

参考文件：

```text
tools/memory_tool.py
agent/memory_manager.py
agent/memory_provider.py
```

### 2.3 Skills 自我改进

Hermes 的 closed learning loop 强调 skill creation / skill self-improve。对 Agent Platform 来说，skill 可以映射为 Agent Package 的可演进资产：

```text
agents/<agent_id>/skills/
agents/<agent_id>/playbooks/
agents/<agent_id>/evals/
agents/<agent_id>/prompts/
```

建议：

1. 第一阶段不要自动生成复杂代码 skill。
2. 先生成 playbook / prompt guideline / eval case。
3. skill 修改必须走 MR 和 eval。
4. skill 使用效果要进入 feedback/eval 指标。

### 2.4 Kanban Worker 生命周期

Hermes v0.13 的 multi-agent kanban 有几个值得借鉴的工程点：

1. durable board。
2. worker heartbeat。
3. reclaim / zombie detection。
4. retry budget。
5. hallucination gate。
6. worker scoped ownership。
7. orchestrator-only tools。

Agent Platform 已经选择 Plane 作为外部业务看板，不应该用 Hermes Kanban 替代 Plane。但 DevFlow runner/job 层应借鉴这些机制：

```text
Plane Work Item
  -> DevFlow Job
  -> Runner heartbeat
  -> Retry budget
  -> Stuck/zombie detection
  -> DLQ / reclaim
  -> Plane/GitLab 回写
```

参考文件：

```text
tools/kanban_tools.py
RELEASE_v0.13.0.md
```

### 2.5 Checkpoint

Hermes 的 checkpoint manager 在文件修改前通过 shadow git store 创建透明快照，并支持 rollback。

Agent Platform 的 Coding Runner 可以借鉴：

```text
before_runner_checkpoint
before_validation_checkpoint
before_commit_checkpoint
after_commit_checkpoint
```

用途：

1. runner 修改失败后快速回滚 workspace。
2. validation 失败时保留失败现场。
3. MR 前比较 checkpoint diff。
4. 生成更可读的 runner report。

参考文件：

```text
tools/checkpoint_manager.py
```

### 2.6 Approval 与 Path Security

Hermes 的 approval 体系包含：

1. hardline blocklist。
2. dangerous pattern detection。
3. sudo stdin guard。
4. per-session approval state。
5. gateway approval context。
6. plugin hooks。
7. sensitive path detection。

Agent Platform 应拆成三层：

```text
Hard Block
  永远禁止：secrets、.env、prod deploy、root 删除、敏感路径、危险 shell

Requires Approval
  需要人类确认：依赖升级、migration、tool 行为变更、routing 变更

Auto Allow
  默认允许：pytest、ruff、manifest validate、agent eval、prompt/eval/docs 改动
```

参考文件：

```text
tools/approval.py
tools/path_security.py
```

### 2.7 Trajectory / RL Data

Hermes 支持 trajectory 保存、压缩和 RL training tool。Agent Platform 第一阶段不需要训练模型，但应该提前把数据形态留好：

```text
AgentRun -> RuntimeTrajectory
EvalFailure -> FailureSample
DevFlowJob -> RepairTrajectory
HumanReview -> PreferenceSignal
RejectedMR -> NegativeSample
MergedMR + improved eval -> PositiveSample
```

这些数据后续可以用于：

1. prompt/eval 优化。
2. tool-use 可靠性评测。
3. coding runner 行为分析。
4. 自研小模型或 reward model 训练。

参考文件：

```text
agent/trajectory.py
trajectory_compressor.py
tools/rl_training_tool.py
```

### 2.8 Insights

Hermes 的 InsightsEngine 会统计 session、usage、cost、tool usage、skill usage、activity、top sessions。

Agent Platform 需要 Evolution Insights：

```text
agent failure rate
tool timeout/error rate
eval regression trend
proposal accepted/rejected rate
auto MR success rate
review rejection reason
mean time to proposal
mean time to merged fix
```

参考文件：

```text
agent/insights.py
```

## 3. 不建议照搬的点

1. **不要用 Hermes Kanban 替代 Plane**：Plane 是业务协作事实源。
2. **不要让 Hermes 直接调 Codex 改代码**：必须经过 Agent Platform DevFlow、PathGuard、Eval、GitLab MR。
3. **不要把 Hermes personal memory 原样用于多租户业务 Agent**：需要 tenant/agent/environment 隔离和脱敏。
4. **不要让 self-improvement 自动发布生产**：必须保留 MR、review、staging/canary。
5. **不要把 Hermes 的 gateway/routine 模型直接作为平台入口**：Agent Platform 的 API、Plane webhook 和 DevFlow job 是更合适的平台入口。

## 3.1 不是削弱 Hermes，而是分层授权

限制 Hermes 直接写生产事实源，并不等于不让 Hermes 自进化。更准确的设计是：

```text
Hermes 自进化自己
Platform 自进化业务 Agent
```

Hermes 可以自进化：

1. 分析策略。
2. root cause 分类。
3. proposal 生成 rubric。
4. eval draft 生成能力。
5. MR review skill。
6. release risk analysis skill。
7. 自己的 analyst memory。

Hermes 不直接自进化：

1. 业务 Agent 的生产 prompt。
2. 正式 Platform Memory。
3. Agent Package skills。
4. EvalRegistry。
5. Plane/GitLab 发布状态。

如果 Hermes 的自进化结果要影响业务 Agent，必须进入 Candidate Store，再由 Platform 晋升。

## 4. 映射到 Agent Platform 的设计调整

| Hermes 能力 | Agent Platform 借鉴形态 |
| --- | --- |
| Background review fork | Evolution Review Fork |
| MemoryStore | EvolutionMemoryRepository |
| Skills self-improve | Agent package playbooks/skills/evals |
| Kanban worker heartbeat | DevFlow runner heartbeat/job state |
| Kanban reclaim/zombie detection | Runner reclaim + DLQ |
| Checkpoint manager | Runner workspace checkpoint |
| Approval hardline blocklist | PathGuard + CommandGuard + RiskPolicy |
| Trajectory compressor | RuntimeTrajectory / RepairTrajectory |
| InsightsEngine | EvolutionInsights |
| Cron/webhook routines | Plane/GitLab/API triggers managed by Agent Platform |

## 4.1 Candidate Store 与 Promotion

Hermes 不应只输出一段建议文本。为了充分利用 Hermes 能力，Platform 应允许 Hermes 写入结构化 Candidate Store：

```text
MemoryCandidate
SkillDraft
EvalCaseDraft
ProposalDraft
TaskPackDraft
ReviewReport
ReleaseRiskReport
```

Candidate Store 的价值：

1. Hermes 可以深度参与，不只是“聊天建议”。
2. Platform 仍然保留最终治理权。
3. Candidate 可以被验证、去重、审批、晋升或拒绝。
4. Candidate 可以积累质量反馈，用于 Hermes 自身改进。

Promotion workflow：

```text
Hermes Candidate
  -> Platform schema/evidence/scope validation
  -> risk classification
  -> duplicate detection
  -> approval
  -> promote to Platform asset
```

原则：

```text
Hermes writes candidates, Platform promotes assets.
```

## 5. 对 S9 的影响

S9 不应直接做“全自动自我修复”。更合适的顺序：

1. `EvalFailure -> ImprovementProposal`
2. Background Review Fork，只允许 proposal/memory/eval draft。
3. Proposal 手动 dispatch 到 Plane。
4. Low-risk proposal 自动 DevFlow。
5. Runner checkpoint + command/path guard。
6. Evolution insights 和 proposal outcome 统计。
7. 再考虑 feedback/log pattern 自动 proposal。
