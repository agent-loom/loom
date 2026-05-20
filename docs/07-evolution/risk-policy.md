# 自进化风险策略

> Status: Implemented (Risk Classifier + PathGuard)
> Stage: S9
> Owner: platform
> Last updated: 2026-05-20

本文档定义自进化 Agent 系统中哪些动作可以自动化、哪些必须人工确认、哪些只能生成报告。

## 1. 风险等级

| 等级 | 定义 | 默认动作 |
| --- | --- | --- |
| Low | 只影响 prompt、eval、docs、contract tests，且不会改变工具行为或平台能力 | 可自动创建 Plane Work Item，可自动触发 DevFlow |
| Medium | 影响 knowledge、routing、tool schema、adapter 边界，但不涉及交易、权限、发布 | 需要人类确认后触发 DevFlow |
| High | 影响业务代码、工具执行、推荐/价格/库存/权限、平台 runtime、安全策略 | 只生成提案，不自动开发 |
| Critical | 涉及安全事故、数据泄露、生产故障、财务/交易风险 | 只告警和生成 incident，不进入自动开发 |

## 2. 路径策略

### 2.1 Low 默认允许

```text
agents/<agent_id>/prompts/**
agents/<agent_id>/evals/**
tests/contract/**
docs/**
```

### 2.2 Medium 需要确认

```text
agents/<agent_id>/knowledge/**
agents/<agent_id>/manifest.yaml
agents/<agent_id>/adapters/**
agents/<agent_id>/tools/**
tests/unit/**
tests/integration/**
```

### 2.3 默认禁止

```text
src/agent_platform/**
deploy/**
infra/**
scripts/deploy/**
.env
.env.*
secrets/**
**/*secret*
**/*token*
```

如需允许平台核心修改，必须由人类创建普通 DevFlow 任务，不走自进化自动触发。

## 3. 自动化矩阵

| 动作 | Low | Medium | High | Critical |
| --- | --- | --- | --- | --- |
| 生成 proposal | 自动 | 自动 | 自动 | 自动 |
| 创建 Plane Work Item | 自动 | 自动但标记待确认 | 可创建 discovery item | 可创建 incident |
| 推进到 Ready for AI Dev | 自动 | 人工确认 | 禁止 | 禁止 |
| 派发 Codex/Claude runner | 自动 | 人工确认 | 禁止 | 禁止 |
| 创建 MR | 自动 | 人工确认后自动 | 禁止 | 禁止 |
| merge MR | 人工 | 人工 | 人工专项 review | 禁止 |
| staging 发布 | 可手动 | 手动 | 手动专项审批 | 禁止 |
| prod 发布 | 禁止自动 | 禁止自动 | 禁止自动 | 禁止 |

## 3.1 Toolset 分层

借鉴 Hermes 后台 review fork 的 scoped toolsets，自进化系统按执行阶段划分工具权限。

| 阶段 | 允许工具 | 禁止工具 |
| --- | --- | --- |
| Review Fork | evidence read、proposal write、memory write、eval draft write | shell、web unrestricted、git push、deploy、secret read |
| Proposal Dispatch | Plane create/comment、proposal state update | runner execute、merge、prod deploy |
| Low-risk DevFlow | git branch、workspace edit、pytest、eval、MR create | blocked paths、prod deploy、secret read |
| Medium-risk DevFlow | Low-risk 工具 + 人工确认后的指定工具 | 未审批 tool implementation / migration |
| High/Critical | report only、incident create | runner execute、git write、deploy |

## 3.2 Command Guard

Coding Runner 和自进化相关工具必须有命令级防护。

Hard Block 示例：

```text
rm -rf /
mkfs.*
dd of=/dev/*
shutdown / reboot / poweroff
sudo -S
cat .env
cat secrets/*
git push origin master
git push origin main
kubectl apply -f deploy/prod
```

Requires Approval 示例：

```text
dependency upgrade
database migration
tool implementation change
agent routing rule change
manifest runtime backend change
```

Auto Allow 示例：

```text
pytest
ruff
python scripts/validate_manifest.py
python scripts/run_agent_eval.py
git diff
git status
```

## 3.3 Workspace Checkpoint

借鉴 Hermes checkpoint manager，runner 执行低风险自动修改时也应生成 checkpoint。

建议 checkpoint 点：

```text
before_runner
before_validation
before_commit
after_commit
```

要求：

1. validation 失败时保留失败 checkpoint。
2. blocked path 命中时停止并保留 checkpoint。
3. MR report 中写入 checkpoint id 和 diff 摘要。
4. checkpoint 不应包含 `.env`、secret、large binary、dependency cache。

## 4. 证据要求

| 风险等级 | 最低 evidence |
| --- | --- |
| Low | 1 条 eval failure 或 user feedback |
| Medium | 至少 2 条 evidence，且包含 trace/eval/log 之一 |
| High | 至少 3 条 evidence，必须包含可复现步骤或失败样本 |
| Critical | incident 级证据，必须保留审计链路 |

## 5. 审批要求

| 场景 | 必需审批 |
| --- | --- |
| 修改 prompt/eval/docs | 普通 code review |
| 修改 knowledge | 业务 owner 或产品 owner |
| 修改 tool schema | Agent owner + backend owner |
| 修改 tool implementation | Agent owner + backend owner + QA |
| 修改 routing/ownership | Platform owner |
| 修改安全策略/secret/auth | Security owner |
| 修改发布流程 | Platform owner + release owner |

## 6. 防止自我强化错误

自进化系统必须防止“错误反馈 -> 错误修改 -> 更多错误”的循环。

规则：

1. 用户负反馈不是事实，只是信号。
2. Hermes memory 不是事实，只是上下文。
3. 自动生成的 eval case 必须可读、可审查。
4. 修改 prompt 时必须保留旧 case 回归。
5. 对同一 root cause 的重复 proposal 应聚合，而不是持续创建新任务。
6. 如果连续两次 MR 被人类拒绝，同类 proposal 自动降级为人工确认。
7. 如果某个 Agent 的自进化 MR 连续引入回归，暂停该 Agent 的自动触发。
8. Background review fork 连续生成低质量 proposal 时，暂停该 agent 的自动 review fork。
9. memory 写入不得直接改变当前运行 prompt，必须在下一轮经过 ContextBuilder/Policy 注入。

## 7. Runtime 侧防护

自进化提案不得直接影响线上 runtime，必须经过：

```text
MR
  -> CI
  -> eval
  -> review
  -> staging
  -> canary
  -> prod
```

生产 runtime 只读取已发布的 agent package/artifact，不读取未 review 的 Plane Work Item 或 proposal。
