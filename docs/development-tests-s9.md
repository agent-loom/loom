# S9 自进化系统端到端测试规范与方案

> Status: Draft  
> Owner: platform  
> Last updated: 2026-05-21

本文档规范了 S9 阶段“受治理自进化 Agent 系统”的端到端测试与集成验证要求。为了在不污染本地物理 Git 仓库和外部系统的前提下，高保真地重现所有进化分支与数据流，测试体系采用了**全链路内存网关注入**与**可重编程 Stub 执行器**方案。

---

## 1. 测试范围与用例拓扑

测试以黑盒/集成形式，覆盖自进化系统（Evolution System）与运行时记忆/技能（Runtime Memory & Skill）的 9 个细分模块：

```text
  [ 缺陷事件 / 负反馈 ]
         │
         ▼
 ┌───────────────┐
 │ Evolution     │ ──(去重过滤)──> [ 已存在的相似提案 ] (过滤拦截)
 │ Engine        │
 └───────┬───────┘
         │ (触发)
         ▼
 ┌───────────────┐
 │ Background    │ ──(质量熔断检查)──> [ Rejection Rate > 0.5 ] (熔断暂停)
 │ Review Fork   │
 └───────┬───────┘
         │ (LLM / Stub)
         ▼
 ┌───────────────┐
 │ Scoped Tools  │ ──(敏感词/PII扫描)──> [ 密钥/注入敏感词 ] (Validator 拦截)
 │ Candidate     │
 └───────┬───────┘
         │ (Approved 晋升)
         ▼
 ┌───────────────┐
 │ Promotion     │ ──(Low Risk)───> [ Plane/DevFlow ] ──> [ MR / 代码改写 ]
 │ Executor      │ ──(Memory/Skill)─> [ EvolutionMemory ]
 └───────────────┘
```

### 核心验证用例

| 用例 ID | 模块名称 | 描述与验证目标 | 验证方法 |
|---|---|---|---|
| **E2E_01** | 全链路正常流 | eval_failure -> 异步评审生成 Candidate -> 状态机 validate/approve -> promote 晋升正式提案 -> 触发低风险自动 DevFlow 分发。 | 链式 FlowVerifier 断言状态与属性对齐。 |
| **E2E_02** | 质量熔断拦截 | 当 Candidate 的累计拒绝率 (Rejected Rate) 超过 50% 阈值时，后续的 Background Review Fork 自动触发旁路熔断，拦截 Candidate 产生，并记录审计日志。 | 连续 approve 3 个、reject 3 个候选资产，注入第 7 个事件，断言状态为 `skipped_circuit_breaker`。 |
| **E2E_03** | 安全合规扫描 | Candidate Validator 拦截包含明文 API Keys 密钥或 SQL/System Prompt 命令注入的 Payload 资产。 | 注入带有 `api_key: sk-` 和 `ignore previous instructions` 的资产，断言 validate 返回安全失败。 |
| **E2E_04** | 多租户强隔离 | 验证 ContextBuilder 组装 system prompt 时，跨租户 (Tenant A / Tenant B) 的运行时内存 (RuntimeMemory) 绝对不可相互穿透与交叉读取。 | 分别写入不同 tenant_id 的 Scope 内存，用不同的 Spec 请求 build，断言 prompt 仅包含本租户内容。 |
| **E2E_05** | Token 限额注入 | 验证当注入的记忆 (RuntimeMemory) 和技能 (Skill) 字符数分别超标 (2000 / 6000 限制) 时，ContextBuilder 能够进行截断与降级处理，防止溢出。 | 写入 10 条超长内存，执行 build，断言 system_prompt 截断且未产生程序崩溃。 |
| **E2E_06** | 连续被驳回降级 | 同一 Agent 提案连续 2 次被人类 Dismiss（驳回拒绝）后，同类新提案即使为 Low Risk，也自动强制标记为 `requires_human_confirmation_before_devflow` 降级策略。 | 连续 dismiss 2 个提案，生成第 3 个，断言该提案的 requires_human_confirmation 标记为 True。 |

---

## 2. 环境与高保真 Stub 工具设计

为了免除网络依赖以及外部 Plane/GitLab 服务的权限绑定，测试脚本使用以下高保真仿真件：
* **`StubModelGateway`**：本地可编程的对话网关，模拟真实 LLM 生成 Scoped Tool 调用（如 `evidence_read`、`memory_write`）。
* **`MockTransport`**：通过 `httpx.MockTransport` 拦截 DevFlow 的 GitLab / Plane 所有外部请求，直接返回符合 API 契约的虚拟 JSON。
* **内存仓储实例**：`InMemoryCandidateRepository`、`InMemoryProposalRepository`、`InMemoryEvolutionMemoryRepository`。

---

## 3. 端到端测试执行指南

测试脚本位于 `scripts/run_evolution_e2e_suite.py`。

### 运行全套测试

```bash
uv run python scripts/run_evolution_e2e_suite.py
```

### 关键控制台输出说明

* **`PASS`**：表示链路状态流转、数据内容、安全边界以及租户隔离等断言全部如期符合设计契约。
* **`FAIL`**：触发了非预期的 Bug 或数据泄漏，需要检查相关的 core 模块。
