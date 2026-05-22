# 多租户安全拦截与防篡改审计 E2E 测试规范与方案

> Status: Draft  
> Owner: platform  
> Last updated: 2026-05-21

本文档规范了 Agent Platform 平台中 **多租户 API 速率限制、配额管控与部署审计哈希链防篡改** 的端到端测试与集成验证要求。测试体系采用纯内存后端与高速仿真环境，无需外部数据库或网络依赖。

---

## 1. 测试范围与用例拓扑

测试以黑盒/集成形式，覆盖三大安全防御模块：

```text
         [ 外部客户端请求 ]
                 │
                 ▼
     ┌───────────────────────┐
     │ RateLimiterMiddleware │ ──(超出速率限制)──> HTTP 429 / Retry-After
     └───────────┬───────────┘
                 │ (放行)
                 ▼
     ┌───────────────────────┐
     │   TenantQuotaManager  │ ──(每日次数/Token超限)──> QUOTA_EXCEEDED (429)
     └───────────┬───────────┘
                 │ (记录成功)
                 ▼
     ┌───────────────────────┐
     │  DeploymentAuditLog   │ ──(密码学完整性)─> verify_chain() 检测历史篡改
     └───────────────────────┘
```

### 核心验证用例

| 用例 ID | 模块名称 | 描述与验证目标 | 验证方法 |
|---|---|---|---|
| **E2E_SEC_01** | IP 级速率拦截防御 | 验证单 IP 在突发请求超过令牌桶容量时触发拦截，前 N 次成功、第 N+1 次失败，并验证 `Retry-After` 计算逻辑。 | 直接调用 `InMemoryRateLimiterBackend.try_consume()`，以 `rate=0.0` / `burst=5` 验证第 6 次返回 False。 |
| **E2E_SEC_02** | 角色差异化限流拦截 | 验证 `readonly` (burst=10) 与 `agent_developer` (burst=20) 角色被施加不同的限流阈值，前者先触发拦截。 | 分别以不同 burst 值消耗令牌桶，验证各自拦截临界点符合 `ROLE_RATE_LIMITS` 配置。 |
| **E2E_SEC_03** | 租户每日请求配额硬顶 | 验证租户 `max_requests_per_day=3` 时第 4 次请求抛出 `QuotaExceededError`，异常包含正确的 resource/limit/current 属性。 | 调用 `record_request()` 3 次后 `check_request_quota()` 断言异常。 |
| **E2E_SEC_04** | 配额利用率与多维度审计 | 验证 `check_all()` 对 storage 和 agent 超标的综合检测，以及 `get_tenant_report()` 利用率百分比计算。 | 设置超额 usage，断言 violations 非空且 utilization 百分比 > 100%。 |
| **E2E_SEC_05** | 部署哈希链防篡改审计 | 验证 Genesis -> Deploy1 -> Deploy2 链式哈希构成；模拟恶意篡改后 `verify_chain()` 精准检测失败。 | 录入 2 条部署事件验证链完整；直接篡改存储层 integrity_hash 后断言校验失败。 |

---

## 2. 环境与高保真仿真件设计

测试使用纯内存后端，无需任何外部服务：
* **`InMemoryRateLimiterBackend`**：进程内令牌桶后端，支持 rate/burst 参数化控制。
* **`InMemoryQuotaBackend`**：内存配额后端，支持配额设置与用量追踪。
* **`InMemoryDeploymentAuditRepository`**：内存审计事件存储，支持哈希链校验与直接篡改模拟。

---

## 3. 端到端测试执行指南

测试脚本位于 `scripts/run_security_reliability_e2e.py`。

### 运行测试

```bash
uv run python scripts/run_security_reliability_e2e.py
```

### 关键控制台输出说明

* **`PASS`**：表示速率拦截、配额管控、哈希链防篡改等断言全部符合系统安全设计。
* **`FAIL`**：触发了非预期的 Bug，限流逃逸或审计链被绕过，需要检查相关 core 模块。
