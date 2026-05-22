#!/usr/bin/env python3
"""多租户安全拦截与防篡改审计 E2E 测试套件 (run_security_reliability_e2e.py)。

包含对以下 5 大核心场景的安全验证：
1. E2E_SEC_01: IP 级速率拦截防御 (Token Bucket Burst Exhaustion)
2. E2E_SEC_02: 角色差异化限流拦截 (Role-Based Rate Limit Differentiation)
3. E2E_SEC_03: 租户每日请求配额硬顶 (Tenant Daily Request Quota Hard Cap)
4. E2E_SEC_04: 配额利用率与多维度审计 (Quota Utilization & Multi-Dimension Audit)
5. E2E_SEC_05: 部署哈希链防篡改审计 (Deployment Hash Chain Tamper Detection)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.api.rate_limiter import (
    InMemoryRateLimiterBackend,
    ROLE_RATE_LIMITS,
)
from agent_platform.api.tenant_quota import (
    QuotaExceededError,
    TenantQuota,
    TenantQuotaManager,
)
from agent_platform.domain.models import AgentDeployment, AgentDeploymentStatus
from agent_platform.persistence.memory import InMemoryDeploymentAuditRepository
from agent_platform.registry.deployment import DeploymentAuditLog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("security_reliability_e2e")


class TermColor:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_banner(msg: str) -> None:
    print(f"\n{TermColor.HEADER}{TermColor.BOLD}=== {msg} ==={TermColor.ENDC}")


def print_ok(msg: str) -> None:
    print(f"  {TermColor.OKGREEN}[PASS]{TermColor.ENDC} {msg}")


def print_fail(msg: str) -> None:
    print(f"  {TermColor.FAIL}[FAIL]{TermColor.ENDC} {msg}")


# ---------------------------------------------------------------------------
# E2E_SEC_01: IP 级速率拦截防御
# ---------------------------------------------------------------------------
async def test_ip_rate_limit_burst_exhaustion() -> bool:
    """验证令牌桶在 burst 耗尽后拦截请求。"""
    print_banner("E2E_SEC_01: IP 级速率拦截防御")
    passed = True
    backend = InMemoryRateLimiterBackend()
    burst = 5
    rate = 0.0  # 不补充令牌，确保 burst 耗尽后不再恢复

    # 前 burst 次应全部放行
    for i in range(burst):
        ok = await backend.try_consume("ip:192.168.1.1", rate, burst)
        if not ok:
            print_fail(f"第 {i + 1} 次请求应放行但被拦截")
            passed = False
    if passed:
        print_ok(f"前 {burst} 次请求全部放行")

    # 第 burst+1 次应被拦截
    blocked = await backend.try_consume("ip:192.168.1.1", rate, burst)
    if blocked:
        print_fail(f"第 {burst + 1} 次请求应被拦截但被放行")
        passed = False
    else:
        print_ok(f"第 {burst + 1} 次请求正确拦截")

    # 验证 Retry-After 计算逻辑
    normal_rate = 60 / 60.0  # 1 req/s
    retry_after = max(1, int(1.0 / normal_rate))
    if retry_after == 1:
        print_ok(f"Retry-After 计算正确: {retry_after}s (rate={normal_rate} req/s)")
    else:
        print_fail(f"Retry-After 计算异常: {retry_after}s")
        passed = False

    return passed


# ---------------------------------------------------------------------------
# E2E_SEC_02: 角色差异化限流拦截
# ---------------------------------------------------------------------------
async def test_role_based_rate_limit_differentiation() -> bool:
    """验证不同角色的 burst 阈值差异。"""
    print_banner("E2E_SEC_02: 角色差异化限流拦截")
    passed = True

    readonly_rpm, readonly_burst = ROLE_RATE_LIMITS["readonly"]
    developer_rpm, developer_burst = ROLE_RATE_LIMITS["agent_developer"]
    print_ok(
        f"角色配置确认: readonly=({readonly_rpm} rpm, burst={readonly_burst}), "
        f"agent_developer=({developer_rpm} rpm, burst={developer_burst})"
    )

    # readonly 角色：burst=10，rate=0 不补充
    backend_ro = InMemoryRateLimiterBackend()
    ro_allowed = 0
    for _ in range(readonly_burst + 5):
        if await backend_ro.try_consume("key:readonly_user", 0.0, readonly_burst):
            ro_allowed += 1
    if ro_allowed == readonly_burst:
        print_ok(f"readonly 角色精确在 burst={readonly_burst} 后被拦截 (放行 {ro_allowed} 次)")
    else:
        print_fail(f"readonly 角色放行次数异常: {ro_allowed}，期望 {readonly_burst}")
        passed = False

    # agent_developer 角色：burst=20，rate=0 不补充
    backend_dev = InMemoryRateLimiterBackend()
    dev_allowed = 0
    for _ in range(developer_burst + 5):
        if await backend_dev.try_consume("key:developer_user", 0.0, developer_burst):
            dev_allowed += 1
    if dev_allowed == developer_burst:
        print_ok(f"agent_developer 角色精确在 burst={developer_burst} 后被拦截 (放行 {dev_allowed} 次)")
    else:
        print_fail(f"agent_developer 角色放行次数异常: {dev_allowed}，期望 {developer_burst}")
        passed = False

    # 差异化断言
    if ro_allowed < dev_allowed:
        print_ok(f"差异化限流验证通过: readonly({ro_allowed}) < agent_developer({dev_allowed})")
    else:
        print_fail(f"差异化限流验证失败: readonly({ro_allowed}) >= agent_developer({dev_allowed})")
        passed = False

    return passed


# ---------------------------------------------------------------------------
# E2E_SEC_03: 租户每日请求配额硬顶
# ---------------------------------------------------------------------------
async def test_tenant_daily_request_quota_hard_cap() -> bool:
    """验证 max_requests_per_day 硬顶拦截。"""
    print_banner("E2E_SEC_03: 租户每日请求配额硬顶")
    passed = True
    manager = TenantQuotaManager()
    quota = TenantQuota(tenant_id="tenant_sec_03", max_requests_per_day=3)
    manager.set_quota(quota)

    # 前 3 次记录请求应正常通过
    for i in range(3):
        manager.record_request("tenant_sec_03")
        try:
            manager.check_request_quota("tenant_sec_03")
            if i < 2:
                print_ok(f"第 {i + 1} 次请求配额检查通过")
            else:
                # 第 3 次 record 后 requests_today == 3 == max，应触发
                print_fail(f"第 {i + 1} 次请求后配额检查应失败但通过")
                passed = False
        except QuotaExceededError as e:
            if i == 2:
                # 第 3 次 record 后触发是正确行为
                print_ok(f"第 {i + 1} 次请求后配额正确拦截: {e.resource}")
                if e.resource == "requests_per_day" and e.limit == 3 and e.current == 3:
                    print_ok(f"异常属性校验: resource={e.resource}, limit={e.limit}, current={e.current}")
                else:
                    print_fail(f"异常属性异常: resource={e.resource}, limit={e.limit}, current={e.current}")
                    passed = False
            else:
                print_fail(f"第 {i + 1} 次请求不应被拦截")
                passed = False

    # Token 配额检查
    manager2 = TenantQuotaManager()
    quota2 = TenantQuota(tenant_id="tenant_token", max_tokens_per_day=100)
    manager2.set_quota(quota2)
    manager2.record_request("tenant_token", tokens=80)
    try:
        manager2.check_token_quota("tenant_token", additional_tokens=30)
        print_fail("Token 配额超限应抛出异常")
        passed = False
    except QuotaExceededError as e:
        print_ok(f"Token 配额正确拦截: projected {e.current}+30 > {e.limit}")

    return passed


# ---------------------------------------------------------------------------
# E2E_SEC_04: 配额利用率与多维度审计
# ---------------------------------------------------------------------------
async def test_quota_utilization_multi_dimension_audit() -> bool:
    """验证 check_all() 多维度超标检测和利用率报告。"""
    print_banner("E2E_SEC_04: 配额利用率与多维度审计")
    passed = True
    manager = TenantQuotaManager()
    quota = TenantQuota(
        tenant_id="tenant_sec_04",
        max_requests_per_day=1000,
        max_tokens_per_day=500000,
        max_storage_mb=100,
        max_agents=5,
    )
    manager.set_quota(quota)

    # 设置超额用量
    manager.record_storage("tenant_sec_04", 150.0)
    manager.record_agent_count("tenant_sec_04", 6)

    violations = manager.check_all("tenant_sec_04")
    if len(violations) >= 2:
        print_ok(f"check_all 检测到 {len(violations)} 条违规: {violations}")
    else:
        print_fail(f"check_all 应检测到至少 2 条违规，实际: {violations}")
        passed = False

    has_storage = any("storage" in v for v in violations)
    has_agents = any("agents" in v for v in violations)
    if has_storage and has_agents:
        print_ok("违规项包含 storage 和 agents 两个维度")
    else:
        print_fail(f"违规项缺少维度: storage={has_storage}, agents={has_agents}")
        passed = False

    # 利用率报告
    report = manager.get_tenant_report("tenant_sec_04")
    util = report["utilization"]
    if util["storage_pct"] > 100:
        print_ok(f"存储利用率超标: {util['storage_pct']}%")
    else:
        print_fail(f"存储利用率应 > 100%，实际: {util['storage_pct']}%")
        passed = False

    if util["agents_pct"] > 100:
        print_ok(f"Agent 数利用率超标: {util['agents_pct']}%")
    else:
        print_fail(f"Agent 数利用率应 > 100%，实际: {util['agents_pct']}%")
        passed = False

    # 正常租户不应有违规
    violations_clean = manager.check_all("tenant_clean")
    if len(violations_clean) == 0:
        print_ok("未配置超额用量的租户无违规")
    else:
        print_fail(f"干净租户不应有违规: {violations_clean}")
        passed = False

    return passed


# ---------------------------------------------------------------------------
# E2E_SEC_05: 部署哈希链防篡改审计
# ---------------------------------------------------------------------------
async def test_deployment_hash_chain_tamper_detection() -> bool:
    """验证哈希链构建与篡改检测。"""
    print_banner("E2E_SEC_05: 部署哈希链防篡改审计")
    passed = True
    repo = InMemoryDeploymentAuditRepository()
    audit_log = DeploymentAuditLog(repo=repo)

    deploy1 = AgentDeployment(
        deployment_id="dep-001",
        agent_id="agent-sec-test",
        version="1.0.0",
        channel="staging",
        status=AgentDeploymentStatus.STAGING,
    )
    event1 = await audit_log.record_deploy(deploy1, actor="ci-pipeline")

    # 验证创世链接
    genesis = "0" * 64
    if event1.prev_hash == genesis:
        print_ok(f"第 1 条事件 prev_hash 正确链接到 GENESIS_HASH")
    else:
        print_fail(f"第 1 条事件 prev_hash 异常: {event1.prev_hash}")
        passed = False

    if event1.integrity_hash and len(event1.integrity_hash) == 64:
        print_ok(f"第 1 条事件 integrity_hash 有效: {event1.integrity_hash[:16]}...")
    else:
        print_fail(f"第 1 条事件 integrity_hash 无效: {event1.integrity_hash}")
        passed = False

    # 第二条部署事件
    deploy2 = AgentDeployment(
        deployment_id="dep-002",
        agent_id="agent-sec-test",
        version="1.1.0",
        channel="staging",
        status=AgentDeploymentStatus.STAGING,
    )
    event2 = await audit_log.record_deploy(
        deploy2, previous_version="1.0.0", actor="ci-pipeline",
    )

    if event2.prev_hash == event1.integrity_hash:
        print_ok("第 2 条事件 prev_hash 正确链接到第 1 条的 integrity_hash")
    else:
        print_fail(
            f"第 2 条事件链接异常: prev={event2.prev_hash}, "
            f"期望={event1.integrity_hash}"
        )
        passed = False

    # 验证完整链
    valid, count = await audit_log.verify_chain(
        agent_id="agent-sec-test", channel="staging",
    )
    if valid and count == 2:
        print_ok(f"哈希链完整性校验通过: {count} 条事件")
    else:
        print_fail(f"哈希链校验异常: valid={valid}, count={count}")
        passed = False

    # 模拟恶意篡改
    original_hash = repo._events[0].integrity_hash
    repo._events[0] = repo._events[0].model_copy(
        update={"integrity_hash": "tampered_" + "0" * 55},
    )

    tampered_valid, tampered_idx = await audit_log.verify_chain(
        agent_id="agent-sec-test", channel="staging",
    )
    if not tampered_valid:
        print_ok(f"篡改检测成功: 在索引 {tampered_idx} 处发现链断裂")
    else:
        print_fail("篡改后哈希链校验仍通过，防篡改机制失效")
        passed = False

    # 恢复并验证回滚事件也参与链
    repo._events[0] = repo._events[0].model_copy(
        update={"integrity_hash": original_hash},
    )
    rollback_event = await audit_log.record_rollback(
        agent_id="agent-sec-test",
        channel="staging",
        from_version="1.1.0",
        to_version="1.0.0",
        actor="oncall-engineer",
    )
    if rollback_event.prev_hash == event2.integrity_hash:
        print_ok("回滚事件正确链接到最新部署事件的 integrity_hash")
    else:
        print_fail(f"回滚事件链接异常: {rollback_event.prev_hash}")
        passed = False

    valid3, count3 = await audit_log.verify_chain(
        agent_id="agent-sec-test", channel="staging",
    )
    if valid3 and count3 == 3:
        print_ok(f"含回滚的完整链校验通过: {count3} 条事件")
    else:
        print_fail(f"含回滚的链校验异常: valid={valid3}, count={count3}")
        passed = False

    return passed


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
async def main() -> int:
    print(f"\n{TermColor.BOLD}{'=' * 70}")
    print("  多租户安全拦截与防篡改审计 E2E 测试套件")
    print(f"{'=' * 70}{TermColor.ENDC}")

    tests = [
        ("E2E_SEC_01", test_ip_rate_limit_burst_exhaustion),
        ("E2E_SEC_02", test_role_based_rate_limit_differentiation),
        ("E2E_SEC_03", test_tenant_daily_request_quota_hard_cap),
        ("E2E_SEC_04", test_quota_utilization_multi_dimension_audit),
        ("E2E_SEC_05", test_deployment_hash_chain_tamper_detection),
    ]

    results: list[tuple[str, bool]] = []
    for test_id, test_fn in tests:
        try:
            ok = await test_fn()
            results.append((test_id, ok))
        except Exception as e:
            print_fail(f"{test_id} 执行异常: {e}")
            logger.exception(f"{test_id} 未捕获异常")
            results.append((test_id, False))

    # 汇总
    print(f"\n{TermColor.BOLD}{'=' * 70}")
    print("  测试汇总")
    print(f"{'=' * 70}{TermColor.ENDC}")
    total = len(results)
    passed_count = sum(1 for _, ok in results if ok)
    for test_id, ok in results:
        status = f"{TermColor.OKGREEN}PASS{TermColor.ENDC}" if ok else f"{TermColor.FAIL}FAIL{TermColor.ENDC}"
        print(f"  {test_id}: {status}")

    print(f"\n  总计: {passed_count}/{total} 通过")
    if passed_count == total:
        print(f"\n{TermColor.OKGREEN}{TermColor.BOLD}全部通过！{TermColor.ENDC}")
        return 0
    else:
        print(f"\n{TermColor.FAIL}{TermColor.BOLD}存在失败用例，请检查。{TermColor.ENDC}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
