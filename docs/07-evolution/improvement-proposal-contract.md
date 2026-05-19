# Improvement Proposal 契约

> Status: Draft
> Stage: S9 Proposal
> Owner: platform
> Last updated: 2026-05-19

`ImprovementProposal` 是自进化闭环里的核心契约。Hermes/分析 Agent 只能生成提案；Agent Platform 根据提案做风险判断、创建 Plane Work Item、生成 DevFlow TaskPack。

## 1. 设计目标

1. 让每次自动改进都有证据。
2. 让 Plane/GitLab/MR 能追溯提案来源。
3. 让 Codex/Claude Code runner 明确允许修改和禁止修改的范围。
4. 让 eval gate 知道应该验证什么。
5. 让人类 reviewer 能快速判断是否接受。

## 2. YAML 示例

```yaml
schema_version: 1
proposal_id: evo_20260519_0001
title: "补充 echo agent 对重复输入的回归用例"
summary: "线上反馈显示 echo agent 对重复输入的说明不够稳定，建议增加 eval case 并微调 prompt。"

tenant_id: default
agent_id: echo
task_type: agent:prompt_eval_improvement
source: evolution_engine

risk:
  level: low
  reason: "仅允许修改 prompt、eval、docs 和 contract tests，不涉及工具、平台核心或发布配置。"
  requires_human_confirmation_before_devflow: false
  requires_human_review_before_merge: true

root_cause:
  category: prompt_gap
  confidence: 0.78
  explanation: "现有 prompt 只描述原样复述，没有约束多轮重复输入的回答格式。"

evidence:
  - type: eval_failure
    id: eval_123
    url: "http://agent-platform.local/api/v1/evals/eval_123"
    summary: "golden case echo_repeat failed"
  - type: agent_run
    id: run_456
    trace_id: trace_456
    summary: "用户重复输入时 response.output.text 不稳定"

proposed_changes:
  - type: prompt_update
    path: agents/echo/prompts/orchestrator.md
    description: "补充重复输入时保持原样复述的规则。"
  - type: eval_case_add
    path: agents/echo/evals/golden.yaml
    description: "增加重复输入 case。"

allowed_paths:
  - agents/echo/prompts/**
  - agents/echo/evals/**
  - tests/contract/**
  - docs/**

blocked_paths:
  - src/agent_platform/**
  - deploy/**
  - infra/**
  - .env
  - secrets/**

validation:
  commands:
    - pytest tests/unit
    - pytest tests/contract
    - python scripts/validate_manifest.py agents/echo/manifest.yaml
    - python scripts/run_agent_eval.py --agent echo --report eval-report.json
  expected:
    eval_cases_added_min: 1
    existing_eval_regression_allowed: false

plane:
  project_mapping: echo
  state: Ready for AI Dev
  labels:
    - evolution
    - low-risk
    - echo

gitlab:
  target_branch: master
  branch_prefix: feat/evolution
  mr_labels:
    - evolution
    - agent:echo
```

## 3. 字段定义

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `schema_version` | 是 | 当前为 `1` |
| `proposal_id` | 是 | 全局唯一 ID |
| `title` | 是 | Plane Work Item / MR 标题来源 |
| `summary` | 是 | 面向人类的简短说明 |
| `tenant_id` | 是 | 租户隔离 |
| `agent_id` | 是 | 目标业务 Agent |
| `task_type` | 是 | DevFlow 任务类型 |
| `source` | 是 | `evolution_engine` / `manual` / `eval_runner` 等 |
| `risk` | 是 | 风险等级和审批策略 |
| `root_cause` | 是 | 根因分类 |
| `evidence` | 是 | 至少一条证据 |
| `proposed_changes` | 是 | 建议变更 |
| `allowed_paths` | 是 | runner 允许修改范围 |
| `blocked_paths` | 是 | runner 禁止修改范围 |
| `validation` | 是 | 验证命令和预期 |
| `plane` | 否 | Plane 创建参数 |
| `gitlab` | 否 | GitLab 分支/MR 参数 |

## 4. Evidence 类型

| type | 必填字段 | 说明 |
| --- | --- | --- |
| `agent_run` | `id`, `summary` | 单次运行记录 |
| `trace` | `trace_id`, `summary` | trace/span 摘要 |
| `eval_failure` | `id`, `summary` | eval 失败 |
| `user_feedback` | `id`, `summary` | 用户反馈 |
| `tool_error` | `id`, `tool_name`, `summary` | 工具错误 |
| `log_pattern` | `id`, `summary` | 日志聚合模式 |
| `plane_item` | `id`, `url`, `summary` | 现有需求或 bug |
| `gitlab_issue` | `id`, `url`, `summary` | GitLab issue |

规则：

1. 没有 evidence 的 proposal 不能进入 DevFlow。
2. evidence 不能包含未脱敏的 secret、token、手机号、身份证等敏感信息。
3. evidence summary 应保留足够上下文，但不应粘贴完整用户隐私内容。

## 5. Risk 字段

```yaml
risk:
  level: low | medium | high | critical
  reason: "..."
  requires_human_confirmation_before_devflow: true
  requires_human_review_before_merge: true
  requires_security_review: false
  requires_product_review: false
```

默认规则：

| level | DevFlow | MR merge | Release |
| --- | --- | --- | --- |
| low | 可自动触发 | 需 review | 不自动 prod |
| medium | 需确认后触发 | 需 review | 需 staging 验证 |
| high | 不自动触发 | 需专项 review | 不自动发布 |
| critical | 只告警/报告 | 不适用 | 不适用 |

## 6. TaskPack 映射

`ImprovementProposal` 到 `DevelopmentTask` 的映射：

| Proposal 字段 | TaskPack 字段 |
| --- | --- |
| `title` | `task.title` |
| `summary` | `task.requirement_summary` |
| `agent_id` | `agent.agent_id` |
| `task_type` | `task.task_type` |
| `allowed_paths` | `scope.allowed_paths` |
| `blocked_paths` | `scope.blocked_paths` |
| `validation.commands` | `validation.commands` |
| `evidence` | `context.evidence` |
| `proposed_changes` | `changes` |

## 7. Plane Work Item 映射

Plane Work Item 建议内容：

```markdown
# Evolution Proposal

Proposal ID: evo_20260519_0001
Agent: echo
Risk: low
Root Cause: prompt_gap

## Summary
...

## Evidence
- eval_123: ...
- run_456: ...

## Proposed Changes
- agents/echo/prompts/orchestrator.md
- agents/echo/evals/golden.yaml

## Validation
- pytest tests/unit
- pytest tests/contract
- python scripts/run_agent_eval.py --agent echo --report eval-report.json
```

Plane custom properties：

```yaml
agent_id: echo
task_type: agent:prompt_eval_improvement
proposal_id: evo_20260519_0001
risk_level: low
evolution_source: eval_failure
```

