# 开发计划（S7：多维评测与运营深化 / S8：生产交付）

> Status: S7 ✅ Completed / S8 🔶 In Progress
> Last updated: 2026-05-17

本计划承接 S5（平台生产化）和 S6（生产运营加固）的成果，将 S7 和 S8 阶段拆为可执行的 Phase。

## 当前基线

| 指标 | S6 结束 | S7 结束 |
|---|---|---|
| 测试 | 988 passed | 1075 passed, 1 skipped, ruff clean |
| 新增测试 | — | +87 tests (S7) |
| 成熟度均值 | ~75% | ~83% |

---

## S7：多维评测与运营深化 — ✅ 全部完成

### S7 Phase 1：ModelGateway 多 Provider + 工具审计 — ✅ 完成

**目标**：ModelGateway 支持多 provider 容错路由；工具调用全链路可审计。

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 7.1.1 | multi-provider ModelGateway | fallback chain + RoutingStrategy (priority/round_robin/cost_optimized) + provider 注册 | ✅ |
| 7.1.2 | CircuitBreaker 三态 | closed→open→half_open 自动恢复；失败计数窗口+恢复探针 | ✅ |
| 7.1.3 | AnthropicProvider | Messages API 适配 + 12 模型定价表 + estimate_cost() | ✅ |
| 7.1.4 | ToolAuditRepository | InMemory/SQL 双实现 + ToolExecutor 自动记录 + Admin /tool-audit 端点 | ✅ |
| 7.1.5 | 统一 AgentStreamEvent | 14 事件类型 + SSE/WebSocket 双输出 + 12 factory helpers | ✅ |
| 7.1.6 | KnowledgeSyncScheduler | asyncio.Task 定期轮询 + DataSynchronization 桥接 + add_source/sync_all | ✅ |

### S7 Phase 2：多维评测引擎 + 租户配额 — ✅ 完成

**目标**：EvalRunner 支持多维度评分和外部数据集；多租户配额管理就位。

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 7.2.1 | EvalCaseScores 多维评分 | accuracy/latency/cost/tool_accuracy 四维 | ✅ |
| 7.2.2 | EvalSummaryStats 汇总统计 | avg_accuracy + P50/P95/P99 百分位延迟 + by-tag 分组 + total_cost | ✅ |
| 7.2.3 | load_dataset() 外部数据集 | YAML/JSON 加载 + `{"cases": [...]}` 包装 + FileNotFoundError/ValueError | ✅ |
| 7.2.4 | run_dataset() 独立入口 | 支持单独跑外部数据集，返回完整 EvalReport | ✅ |
| 7.2.5 | TenantQuotaManager | requests/tokens/storage/agents 四维配额 + 日重置 + 违规检查 | ✅ |
| 7.2.6 | Admin quota CRUD 端点 | GET/PUT /quotas/{tenant_id} + GET /quotas/{tenant_id}/usage | ✅ |
| 7.2.7 | ArtifactStore admin 端点 | list/metadata/SHA-256 verify | ✅ |

### S7 Phase 3：Hermes 流式事件映射 — ✅ 完成

**目标**：Hermes SDK 事件与平台统一 AgentStreamEvent 双向打通。

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 7.3.1 | HermesStreamMapper.from_result | 从同步 Hermes 结果重建完整事件流（run_started→tools→model→delta→completed→run_completed） | ✅ |
| 7.3.2 | map_hermes_event() | 10 种原生事件映射（conversation_start/end, tool_call_start/end, llm_response, text_chunk, chunk, error, tool_start/end） | ✅ |
| 7.3.3 | wrap_streaming_run() | 流式迭代器包装 + run_started/run_completed 书端 + 异常捕获 → ERROR 事件 | ✅ |
| 7.3.4 | 序列号单调递增 | _next_seq() 保证事件全局有序 | ✅ |

### S7 成果总结

- **87 个新增测试**：ModelGateway routing (18) + ToolAudit (7) + AgentStreamEvent (16) + KnowledgeSyncScheduler (6) + ToolExecutor audit (3) + EvalRunner 多维评分 (24) + HermesStreamMapper (16)
- **核心能力**：multi-provider 容错路由、工具调用审计链路、统一流式事件模型、知识库后台同步、多维评测引擎、多租户配额、Hermes 事件映射
- **成熟度提升**：Runtime 80%→85%, Tool 80%→85%, Eval 75%→85%, Knowledge 80%→85%

---

## S8：生产交付 — 🔶 进行中

### S8 Phase 1：可观测性与持久化增强 — 🔶 进行中

**目标**：补齐 Prometheus 指标导出、Session 持久化、Admin eval 报告增强端点。三条线并行。

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 8.1.1 | Prometheus /metrics 端点 | implementation-gap §1.1 Observability | `GET /api/v1/admin/metrics` 返回 `text/plain; version=0.0.4`；HELP/TYPE 注释；所有预定义指标覆盖 | ✅ |
| 8.1.2 | MetricsCollector.to_prometheus() | — | 与 format_prometheus() 输出一致；_HELP_DESCRIPTIONS 预定义描述 | ✅ |
| 8.1.3 | record_error()/record_tool_duration() | — | 便捷记录方法；agent_request_errors_total + tool_call_duration_seconds | ✅ |
| 8.1.4 | SqlAgentSessionRepository | implementation-gap §4.1 | Session ORM Row + SQL 实现 + contract tests；跨实例共享 | 🔶 |
| 8.1.5 | Admin eval 增强端点 | implementation-gap §P1 Eval | POST /evals/{agent_id}/run 触发执行；GET /evals/compare 跨版本对比；/status 增强 | 🔶 |
| 8.1.6 | 测试 + ruff + 提交 + 文档更新 | — | 全量测试通过；ruff clean；implementation-gap 校准 | ⬜ |

### S8 Phase 2：真实 Runner 端到端联调

**目标**：使用 Claude Code CLI 或 Codex CLI 在真实 workspace 中完成 prompt→code→commit→MR 全链路。

**前置条件**：Phase 1 完成；Claude Code CLI 或 Codex CLI 可执行环境就绪。

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 8.2.1 | Claude Code CLI 端到端 | devflow-runner-workspace §验收 | 从 task pack 创建 workspace → Claude Code 执行 → diff 验证 → commit → push → MR 创建 | ⬜ |
| 8.2.2 | Codex CLI 端到端 | devflow-runner-workspace §验收 | 同上，使用 Codex adapter | ⬜ |
| 8.2.3 | Runner 执行日志持久化 | implementation-gap §4.3 | runner stdout/stderr 写入 DB 或文件；Admin 可回放查看 | ⬜ |
| 8.2.4 | 安全沙箱 PoC | implementation-gap §4.3 | Docker 容器隔离执行环境原型；PathGuard 在容器内 enforce | ⬜ |

### S8 Phase 3：Plane + GitLab 端到端联调

**目标**：使用真实 Plane/GitLab 环境验证完整 DevFlow 管线。

**前置条件**：Phase 2 基础能力就绪；Plane/GitLab 实例可访问。

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 8.3.1 | Plane bootstrap 脚本 | next-stage-design-plan §P0-5 | 创建标准 states (8 个)、labels、custom properties；可重复执行 | ⬜ |
| 8.3.2 | Plane→GitLab 正向流 | devflow-state-sync | Work Item 状态变更 → webhook → parse requirement → generate issues → create branch/MR → assign runner | ⬜ |
| 8.3.3 | GitLab→Plane 反向流 | devflow-state-sync | pipeline pass/fail → Plane state 更新 + comment；MR merged → Done 状态 | ⬜ |
| 8.3.4 | Dead Letter Queue | implementation-gap §3.3 | webhook 投递失败进入 DB-backed retry queue；可查询/重试/清理 | ⬜ |
| 8.3.5 | Plane 强状态机 | next-stage-design-plan §P0-5 | 8 状态严格流转；非法跳转拒绝并告警 | ⬜ |

### S8 Phase 4：Hermes 深度集成

**目标**：Hermes memory 持久化 + 错误/重试/中断事件映射。

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 8.4.1 | Hermes memory 持久化 | implementation-gap §2.4 | Hermes memory provider 接入平台 SessionStore；跨 run 记忆连续 | ⬜ |
| 8.4.2 | Hermes 错误/重试映射 | implementation-gap §2.4 | Hermes 超时/限流/中断事件映射为 AgentStreamEvent.ERROR + 自动重试策略 | ⬜ |
| 8.4.3 | Hermes HITL 事件映射 | implementation-gap §2.4 | Hermes human-in-the-loop 回调映射为平台 ApprovalGate 审批流 | ⬜ |
| 8.4.4 | Hermes 集成测试 | hermes-backend-spike | manifest → Hermes config → tool call → memory recall → trace 全链路 | ⬜ |

### S8 Phase 5：Admin UI + 运维工具

**目标**：Web-based 管理界面 + 生产运维工具链。

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 8.5.1 | Admin UI 技术选型 | — | React/Vue/Svelte 选型 + 脚手架 + 登录/鉴权 | ⬜ |
| 8.5.2 | Agent 管理面板 | — | 列表/详情/版本/部署状态/灰度控制；使用 Admin API | ⬜ |
| 8.5.3 | Eval 报告面板 | — | 多维评分可视化；版本间对比；by-tag 视图；历史趋势 | ⬜ |
| 8.5.4 | DevFlow 看板 | — | Job 列表/日志/状态；Plane 状态同步视图 | ⬜ |
| 8.5.5 | Observability 仪表盘 | — | Grafana dashboard 配置模板；或内嵌 metrics 面板 | ⬜ |
| 8.5.6 | 运维脚本工具链 | — | agent 健康巡检、配额报警、eval 回归通知、定期清理过期 session | ⬜ |

### S8 Phase 6：生产加固

**目标**：SLO 门禁、产物签名、服务间鉴权，达到生产交付标准。

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 8.6.1 | SLO 门禁 | implementation-gap §6 阶段 4 | deploy gate 绑定 P99 延迟/错误率 SLO；违反时阻断发布 | ⬜ |
| 8.6.2 | 产物签名验证 | implementation-gap §2.3 | artifact 签名 + manifest_sha256 绑定；部署前验证签名完整性 | ⬜ |
| 8.6.3 | 发布审计不可变记录 | implementation-gap §2.5 | 审计记录写入后不可修改；prod deploy 必须记录 eval_report_id + mr_id + 审批人 | ⬜ |
| 8.6.4 | 服务间鉴权 | implementation-gap §4.4 | 内部服务调用使用 mutual TLS 或 service token；区分人类用户和服务 | ⬜ |
| 8.6.5 | S3/远程 ArtifactStore | implementation-gap §2.3 | ArtifactStore Protocol 的 S3 实现；支持跨环境产物分发 | ⬜ |
| 8.6.6 | 多租户强隔离 | implementation-gap §4.4 | 数据查询全部携带 tenant_id 过滤；跨租户访问测试覆盖 | ⬜ |
| 8.6.7 | 压力测试与容量规划 | — | 模拟 100 并发 agent 请求；找出瓶颈并优化 | ⬜ |

---

## 各 Phase 依赖关系

```text
S8 Phase 1（可观测性+持久化）
    ↓
S8 Phase 2（Runner 端到端）──→ S8 Phase 3（Plane+GitLab 端到端）
    ↓                              ↓
S8 Phase 4（Hermes 深度集成）    S8 Phase 5（Admin UI）
    ↓                              ↓
    └──────── S8 Phase 6（生产加固）───────┘
```

## 里程碑

| 里程碑 | 目标 | 验收 |
|---|---|---|
| M1: 可观测性就绪 | Phase 1 完成 | Prometheus /metrics 可抓取；session 持久化；eval 增强端点可用 |
| M2: DevFlow E2E | Phase 2-3 完成 | 从 Plane 工单到 MR 合并的全自动闭环 |
| M3: Runtime 成熟 | Phase 4 完成 | Hermes memory 跨 run 连续；错误映射完整 |
| M4: 可交付 | Phase 5-6 完成 | Admin UI 可操作；SLO 门禁 + 签名验证 + 压力测试通过 |
