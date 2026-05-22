# 自进化能力验证指南

> Status: 平台基础能力已实现，真实模型 Review Fork 候选生成闭环仍处于联调/不稳定状态
>
> 目标：先验证 Agent Platform 已经具备“自进化可能性”，再逐步验证完整生产闭环。本文档只描述验证方案，不把当前能力误判为生产完成。

## 1. 验证目标

自进化不是先验证“能不能自动改代码”，而是按风险从低到高验证以下断言：

1. Platform 可以把运行经验写成 RuntimeMemory。
2. RuntimeMemory 能进入 agent runtime context。
3. SkillRegistry 能索引 Agent Package 中的 skills。
4. Skill 能进入 agent runtime context。
5. Agent run 结束后，Background Review Fork 能生成 Candidate。
6. Candidate 能经过 validate / approve / promote 晋升为正式资产。
7. Proposal 能进入 Plane，并通过 DevFlow 触发 Codex / Claude runner。
8. Runner 能在 PathGuard / validation 限制下修改 Agent Package，提交 GitLab MR。

第一轮验证只要求证明第 1 到第 6 步成立。第 7 到第 8 步属于真实 DevFlow E2E，应在自进化低风险闭环稳定后再跑。



## 1.5 验证模式说明

自进化及评审分支的验证分为两种模式：
- **Stub 模式**：通过代码规则或预设的模拟逻辑执行 Scoped Tools 动作。这是测试和 CI 离线状态下的默认校验模式，能够 100% 成功。
- **真实 Provider 模式**：调用真实的 LLM 接口，依据 evidence 进行智能分析并触发受限工具。目前已通过 `ModelGateway` 新增的 `tool_choice` 参数（对 OpenAI-compatible 传 `tool_choice`，对 Anthropic 映射 `tool_choice`）实现强制调用 Scoped Tools，但由于长文本理解、幻觉或者是模型本身的不确定性，真实模型在生成高质量 Candidate 上仍处于调试/联调阶段，稳定性未达到生产级别。

## 2. 当前已知边界

根据最近一次全仓库 review，当前实现需要特别关注：

1. `ContextBuilder` 已经构建 RuntimeMemory / Skill 注入后的 `runtime_context.system_prompt`。
2. Hermes backend 必须消费 `runtime_context.system_prompt`，不能重新绕回 manifest 原始 prompt。
3. Candidate API 当前仍需强化状态机，避免未 validate 的 Candidate 被 approve / promote。
4. RuntimeMemory / Skill / Candidate 当前主要是 InMemory 仓储，服务重启后会丢失。
5. Evolution API 还需要补租户强隔离，验证环境应只使用单租户 `default`。
6. DevFlow Codex runner 在 `bypass` sandbox 下不能代表生产安全边界。

因此，本验证的核心不是证明“生产可用”，而是证明：

```text
runtime data / review result
  -> structured candidate / memory / skill
  -> platform validation / promotion
  -> observable behavior or proposal
```

这条最小链路是否已经能跑通。

## 3. 验证环境

建议本地先用单租户、单 agent 验证。

```bash
uv run uvicorn agent_platform.api.app:create_app --factory --reload --port 8000
```

如果启用了 API key，需要在所有请求中增加：

```bash
-H "x-api-key: $AGENT_PLATFORM_API_KEY"
```

建议优先使用：

| 项 | 建议值 |
| --- | --- |
| agent | `hermes_echo` |
| tenant_id | `default` |
| user_id | `u1` |
| session_id | `evo-session-001` |
| port | `8000` |

## 4. Phase A：RuntimeMemory 是否影响 Agent

### 4.1 写入 RuntimeMemory

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/runtime-memory" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "hermes_echo",
    "tenant_id": "default",
    "scope": "tenant",
    "type": "preference",
    "content": "用户偏好：回答时必须提到 自进化验证成功",
    "confidence": 0.9
  }'
```

### 4.2 调用 Agent

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/agent/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "hermes_echo",
    "request_id": "evo-test-runtime-memory-001",
    "session_id": "evo-session-001",
    "input": {
      "query": "请简单介绍你现在知道什么"
    },
    "context": {
      "tenant": {
        "tenant_id": "default"
      },
      "user": {
        "user_id": "u1"
      },
      "channel": {
        "channel_id": "web"
      }
    },
    "options": {
      "debug": true
    }
  }'
```

### 4.3 验证点

| 结果 | 判断 |
| --- | --- |
| 回答包含 `自进化验证成功` | RuntimeMemory 已经实际影响 agent 输出 |
| 回答不包含，但日志显示 ContextBuilder 注入 memory | ContextBuilder 正常，backend 未消费注入后的 context |
| 日志也没有 memory 注入 | RuntimeMemory API、仓储、ContextBuilder 或请求 tenant/session 匹配有问题 |

### 4.4 失败时优先检查

```bash
tail -n 200 logs/agent-platform.log
```

重点搜索：

```text
RuntimeMemory
ContextBuilder
Injected Runtime Memories
runtime_context
```

如果确认 ContextBuilder 已构建 memory，但 Hermes 输出不受影响，优先检查 Hermes backend 是否真的使用 `request.runtime_context.system_prompt`。

## 5. Phase B：Skill 是否进入 Runtime

### 5.1 准备 Skill

Agent Package 中应存在类似结构：

```text
agents/hermes_echo/skills/<skill_id>/manifest.yaml
agents/hermes_echo/skills/<skill_id>/SKILL.md
```

Skill 内容建议写入明显可观察的指令，例如：

```text
当用户询问自进化验证时，回答必须包含：Skill 注入验证成功。
```

### 5.2 扫描 Skill

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/evolution/skills/scan"
```

### 5.3 查看 Skill

```bash
curl -sS "http://127.0.0.1:8000/api/v1/evolution/skills?agent_id=hermes_echo"
```

### 5.4 调用 Agent

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/agent/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "hermes_echo",
    "request_id": "evo-test-skill-001",
    "session_id": "evo-session-002",
    "input": {
      "query": "请说明当前自进化验证状态"
    },
    "context": {
      "tenant": {
        "tenant_id": "default"
      },
      "user": {
        "user_id": "u1"
      },
      "channel": {
        "channel_id": "web"
      }
    },
    "options": {
      "debug": true
    }
  }'
```

### 5.5 验证点

| 结果 | 判断 |
| --- | --- |
| 回答包含 `Skill 注入验证成功` | Skill 已经实际影响 agent 输出 |
| skill list 有数据，但回答不受影响 | SkillRegistry 正常，backend 未消费注入后的 context |
| skill list 为空 | skill 目录结构、manifest 或 scanner 有问题 |

## 6. Phase C：Agent Run 是否生成 Candidate

### 6.1 触发一次 Agent Run

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/agent/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "hermes_echo",
    "request_id": "evo-test-review-fork-001",
    "session_id": "evo-session-003",
    "input": {
      "query": "这是一次用于触发 Background Review Fork 的运行"
    },
    "context": {
      "tenant": {
        "tenant_id": "default"
      },
      "user": {
        "user_id": "u1"
      },
      "channel": {
        "channel_id": "web"
      }
    },
    "options": {
      "debug": true
    }
  }'
```

### 6.2 查询 Candidate

```bash
curl -sS "http://127.0.0.1:8000/api/v1/evolution/candidates?agent_id=hermes_echo"
```

### 6.3 验证点

| 结果 | 判断 |
| --- | --- |
| 出现 `memory_candidate`、`proposal_draft` 或 `eval_case_draft` | Review Fork 已能生成结构化候选资产 |
| Candidate `status=draft` | 符合 Candidate Store 缓冲层设计 |
| Candidate 为空 | post_run hook、review fork 或 candidate repo 没接通 |

## 7. Phase D：Candidate 晋升

先从查询结果中取一个 `candidate_id`。

### 7.1 Validate

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/evolution/candidates/<candidate_id>/validate"
```

预期：

```json
{
  "status": "validated",
  "validation_passed": true,
  "errors": []
}
```

### 7.2 Approve

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/evolution/candidates/<candidate_id>/approve"
```

预期：

```json
{
  "status": "approved"
}
```

### 7.3 Promote

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/evolution/candidates/<candidate_id>/promote"
```

### 7.4 验证晋升结果

如果是 `memory_candidate`：

```bash
curl -sS "http://127.0.0.1:8000/api/v1/evolution/memories?agent_id=hermes_echo"
```

如果是 `proposal_draft`：

```bash
curl -sS "http://127.0.0.1:8000/api/v1/evolution/proposals?agent_id=hermes_echo"
```

验证点：

| 结果 | 判断 |
| --- | --- |
| Candidate 变为 `promoted` | Promotion workflow 可用 |
| 出现 EvolutionMemory | MemoryCandidate 晋升成功 |
| 出现 ImprovementProposal | ProposalDraft 晋升成功 |
| promote 报 validation / state 错误 | 状态机或 Candidate payload 不满足 contract |

## 8. Phase E：Proposal 到 DevFlow

该阶段需要 Plane、GitLab、runner 环境，不建议作为第一轮自进化验证的阻塞项。

目标链路：

```text
ProposalDraft Candidate
  -> validate
  -> approve
  -> promote
  -> ImprovementProposal
  -> dispatch_to_plane
  -> Plane item
  -> Ready for AI Dev
  -> DevFlow Orchestrator
  -> Codex / Claude runner
  -> branch / commit / MR
  -> Plane / GitLab 回写
```

验证时必须确认：

1. Plane item 能看到 proposal 摘要、证据、风险、验证命令。
2. DevFlow ownership 能解析到正确 agent。
3. runner 只修改 `agents/<agent_id>/**`、`tests/**`、`docs/**` 等允许路径。
4. validation 命令通过。
5. GitLab MR 有 commit、diff、pipeline、回写 comment。
6. Plane 状态能进入 Human Review 或 Testing。

## 9. 最小通过标准

第一轮“自进化可能性”验证通过标准：

| 编号 | 标准 | 必须 |
| --- | --- | --- |
| A1 | 可以创建 RuntimeMemory | 是 |
| A2 | RuntimeMemory 能被 ContextBuilder 检索 | 是 |
| A3 | RuntimeMemory 能实际影响至少一个 Hermes/native agent 输出 | 是 |
| B1 | Skill scanner 能发现 Agent Package skill | 是 |
| B2 | Skill 能被 ContextBuilder 选择 | 是 |
| B3 | Skill 能实际影响至少一个 agent 输出 | 是 |
| C1 | Agent run 后能生成 Candidate | 是 |
| D1 | Candidate 能 validate | 是 |
| D2 | Candidate 能 approve | 是 |
| D3 | Candidate 能 promote 为 EvolutionMemory 或 ImprovementProposal | 是 |
| E1 | Proposal 能进入 Plane | 否，第二轮验证 |
| E2 | DevFlow 能开 MR | 否，第二轮验证 |

如果 A3 或 B3 不通过，优先修 runtime context 消费问题，而不是继续跑 DevFlow。

## 10. 自动化冒烟脚本

已提供最小闭环脚本：

```text
scripts/evolution_smoke_test.py
```

脚本职责：

1. 创建 RuntimeMemory。
2. 创建并查询 Skill。
3. 可选调用 `hermes_echo`，验证 RuntimeMemory / Skill 是否实际影响输出。
4. 调用稳定 agent 触发 Background Review Fork。
5. 查询 Candidate。
6. 对 Candidate 执行 validate / approve / promote。
7. 查询晋升后的 EvolutionMemory。
8. 输出 `PASS` / `WARN` / `FAIL`。

默认命令：

```bash
uv run --extra dev python scripts/evolution_smoke_test.py
```

默认行为：

1. 使用进程内 FastAPI `TestClient`，不需要单独启动服务。
2. 禁用 `DATABASE_URL`、Plane、GitLab、API key，避免污染真实环境。
3. 强制 Review Fork 使用 `stub` provider，避免依赖真实 LLM key。
4. 默认禁用 OpenTelemetry instrumentation 输出，避免 span 日志刷屏。
5. 不触发真实 DevFlow，不创建 Plane item，不创建 GitLab branch/MR。

如果要使用当前 `.env` 里的真实配置：

```bash
uv run --extra dev python scripts/evolution_smoke_test.py --use-env
```

如果要验证 Hermes runtime 是否真正消费 RuntimeMemory / Skill 注入：

```bash
uv run --extra dev python scripts/evolution_smoke_test.py --with-hermes
```

该命令在默认 `local-clean` 模式下会启用本地 `probe` provider，不依赖真实 Claude / Anthropic / OpenAI 网络调用。它验证的是平台链路：

```text
RuntimeMemory / Skill
-> ContextBuilder.system_prompt
-> HermesRuntimeBackend
-> 模型调用入参
-> 输出可观察 marker
```

如果要让 Review Fork 使用真实模型 provider，而不是 stub：

```bash
uv run --extra dev python scripts/evolution_smoke_test.py --real-review-fork
```

如果要输出机器可读结果：

```bash
uv run --extra dev python scripts/evolution_smoke_test.py --json
```

### 10.1 结果解释

| 输出 | 含义 | 处理 |
| --- | --- | --- |
| `PASS` | 核心验证点通过 | 可以继续下一阶段 |
| `WARN` | 已知设计差距或非阻塞问题 | 记录到 gap，不阻塞最小闭环 |
| `FAIL` | 核心链路失败 | 需要先修复再继续 |

脚本不应自动触发真实 DevFlow。真实 DevFlow 验证继续使用专门的 Plane/GitLab E2E 脚本，避免把低风险自进化验证和真实代码修改流程混在一起。

## 11. Phase F：全链路物理自进化冒烟验证（自动 Prompt 与 Evals 优化）

这是验证 Agent Platform 自进化可能性的终极方案。它不依赖外部的 GitLab 和真实 Plane 物理环境，而是通过一个高度工程化的端到端自动化脚本（`scripts/demo_self_evolution.py`）模拟闭环：

### 11.1 演进的场景设计

1. **缺陷输入**：向 `hermes_echo` 发送 query `"计算并返回：2的10次方，请用 JSON 格式输出结果"`。由于目前的 Prompt (orchestrator.md) 过于基础，它只会自然地以文本格式回复，无法遵循规范生成合法的 JSON 回复（如 `{"result": 1024}`）。
2. **触发自进化**：模拟用户给予负反馈（Negative Feedback），携带批注 “要求返回标准的 JSON 格式：{"result": 1024}”。
3. **评审生成候选**：通过 `BackgroundReviewFork` 执行受限工具，分析负反馈并在 `CandidateStore` 中提报一个类型为 `proposal_draft` 的候选改进提案：
   - 建议追加 JSON 计算回复规范到 `agents/hermes_echo/prompts/orchestrator.md`。
   - 建议在 `agents/hermes_echo/evals/golden.yaml` 中追加评测边界用例。
4. **DevFlow 本地自动化执行**：
   - 模拟提案晋升，调用 `DemoCodingRunner` 接管。
   - **物理修改**本地 Prompt 文本文件，追加计算回复指令。
   - **物理修改**本地 Eval 评测文件，追加验证用例。
   - 运行本地测试断言无退化，打印出物理文件的真实 `git diff`，向团队展示真实的改动。
5. **进化后效果验证**：
   - 重新加载 Agent，向其发送同一 query `"计算并返回：2的10次方"`，验证返回完美的 JSON `{"result": 1024}`！
6. **文件还原清洁**：
   - 演示脚本在退出前自动执行 `git checkout` 撤销对 Prompt 和 Eval 的修改，保持仓库干净整洁。

### 11.2 执行方案与命令

```bash
# 运行全链路物理自进化可行性冒烟测试
.venv/bin/python scripts/demo_self_evolution.py
```

### 11.3 直观验证点

| 步骤 | 观察指标 | 预期输出 |
| --- | --- | --- |
| 1. 缺陷阶段 | 初始 Agent 回复内容 | 纯文本或 Echo 询问，非严格的 `{"result": 1024}` 格式。 |
| 2. 评审阶段 | 审计 `ReviewForkAudit` 记录 | 产生 status="success"，output_type="proposal_draft" 的审计。 |
| 3. 候选生成 | Candidate 仓储记录 | 产生一条对应的 Candidate DRAFT，payload 包含 proposed_changes 修改方案。 |
| 4. 物理修改 | 本地文件 `git diff` 变化 | 打印出 orchestrator.md 追加了规则，golden.yaml 新增了用例。 |
| 5. 进化验证 | 最终 Agent 再次回复内容 | 输出完美的 `{"result": 1024}`！ |

---

## 12. 验证结论模板

每次验证后建议记录：

```text
日期：
commit：
环境：
agent：
tenant：

RuntimeMemory:
  create:
  context build:
  output influenced:

Skill:
  scan:
  selected:
  output influenced:

Candidate:
  generated:
  validated:
  approved:
  promoted:

DevFlow:
  dispatched:
  branch:
  commit:
  MR:

阻塞问题：
下一步：
```
