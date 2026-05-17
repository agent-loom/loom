"""S8 Phase 6 — 生产加固测试。

覆盖：
- 产物签名验证（deploy 路径 verify_manifest 调用）
- 审计链 eval_report_id/manifest_sha256 记录
- 多租户隔离（list_deployments/list_sessions tenant_id 过滤）
- 运维脚本可导入性
"""

from __future__ import annotations

import pytest

from agent_platform.artifacts.signer import ArtifactSigner
from agent_platform.domain.models import (
    AgentDeployment,
    AgentDeploymentStatus,
)
from agent_platform.registry.deployment import DeploymentAuditLog, DeploymentEvent


class TestArtifactSignatureVerification:
    """验证产物签名验证的完整性。"""

    def test_sign_and_verify_roundtrip(self, tmp_path):
        """签名后验证应通过。"""
        from agent_platform.registry.loader import ManifestLoader

        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "orchestrator.md").write_text("test")
        (prompt_dir / "reply_style.md").write_text("test")
        eval_dir = tmp_path / "evals"
        eval_dir.mkdir()
        (eval_dir / "golden.yaml").write_text("[]")
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text("""
api_version: agent.platform/v1
kind: AgentPackage
metadata:
  id: sign_test
  name: Signature Test
version:
  package_version: 0.1.0
runtime:
  backend: native
prompts:
  orchestrator: prompts/orchestrator.md
  reply_style: prompts/reply_style.md
output:
  protocol: agent-chat/v1
evals:
  suites:
    - evals/golden.yaml
""")
        spec = ManifestLoader().load_file(manifest_file)
        signer = ArtifactSigner()

        sha = signer.sign_manifest(spec.manifest)
        assert signer.verify_manifest(spec.manifest, sha) is True

    def test_verify_fails_with_wrong_hash(self, tmp_path):
        """错误哈希应验证失败。"""
        from agent_platform.registry.loader import ManifestLoader

        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "orchestrator.md").write_text("test")
        (prompt_dir / "reply_style.md").write_text("test")
        eval_dir = tmp_path / "evals"
        eval_dir.mkdir()
        (eval_dir / "golden.yaml").write_text("[]")
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text("""
api_version: agent.platform/v1
kind: AgentPackage
metadata:
  id: sign_test2
  name: Signature Test 2
version:
  package_version: 0.1.0
runtime:
  backend: native
prompts:
  orchestrator: prompts/orchestrator.md
  reply_style: prompts/reply_style.md
output:
  protocol: agent-chat/v1
evals:
  suites:
    - evals/golden.yaml
""")
        spec = ManifestLoader().load_file(manifest_file)
        signer = ArtifactSigner()

        assert signer.verify_manifest(spec.manifest, "wrong_hash") is False

    def test_sign_is_deterministic(self, tmp_path):
        """同一 manifest 多次签名应产生相同哈希。"""
        from agent_platform.registry.loader import ManifestLoader

        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "orchestrator.md").write_text("test")
        (prompt_dir / "reply_style.md").write_text("test")
        eval_dir = tmp_path / "evals"
        eval_dir.mkdir()
        (eval_dir / "golden.yaml").write_text("[]")
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text("""
api_version: agent.platform/v1
kind: AgentPackage
metadata:
  id: determ_test
  name: Deterministic Test
version:
  package_version: 0.1.0
runtime:
  backend: native
prompts:
  orchestrator: prompts/orchestrator.md
  reply_style: prompts/reply_style.md
output:
  protocol: agent-chat/v1
evals:
  suites:
    - evals/golden.yaml
""")
        spec = ManifestLoader().load_file(manifest_file)
        signer = ArtifactSigner()

        sha1 = signer.sign_manifest(spec.manifest)
        sha2 = signer.sign_manifest(spec.manifest)
        assert sha1 == sha2


class TestAuditEvalReportId:
    """验证审计链记录 eval_report_id 和 manifest_sha256。"""

    @pytest.mark.asyncio
    async def test_record_deploy_with_eval_report_id(self):
        audit_log = DeploymentAuditLog()
        deployment = AgentDeployment(
            deployment_id="dep-1",
            agent_id="test-agent",
            version="1.0.0",
            channel="staging",
            status=AgentDeploymentStatus.STAGING,
        )

        event = await audit_log.record_deploy(
            deployment,
            artifact_id="art-123",
            eval_report_id="eval-456",
            manifest_sha256="abc123def456",
        )

        assert event.eval_report_id == "eval-456"
        assert event.manifest_sha256 == "abc123def456"
        assert event.artifact_id == "art-123"

    @pytest.mark.asyncio
    async def test_record_deploy_without_eval_report_id(self):
        audit_log = DeploymentAuditLog()
        deployment = AgentDeployment(
            deployment_id="dep-2",
            agent_id="test-agent",
            version="1.0.0",
            channel="dev",
            status=AgentDeploymentStatus.REGISTERED,
        )

        event = await audit_log.record_deploy(deployment)

        assert event.eval_report_id is None
        assert event.manifest_sha256 is None

    @pytest.mark.asyncio
    async def test_audit_chain_integrity_with_eval_fields(self):
        audit_log = DeploymentAuditLog()

        dep1 = AgentDeployment(
            deployment_id="dep-3",
            agent_id="a1", version="1.0", channel="staging",
            status=AgentDeploymentStatus.STAGING,
        )
        await audit_log.record_deploy(
            dep1, eval_report_id="eval-1", manifest_sha256="sha-1",
        )

        dep2 = AgentDeployment(
            deployment_id="dep-4",
            agent_id="a1", version="2.0", channel="prod",
            status=AgentDeploymentStatus.PROD,
        )
        await audit_log.record_deploy(
            dep2, previous_version="1.0",
            eval_report_id="eval-2", manifest_sha256="sha-2",
        )

        valid, count = await audit_log.verify_chain()
        assert valid is True
        assert count == 2


class TestMultiTenantIsolation:
    """验证多租户隔离在列表端点中的正确性。"""

    @pytest.mark.asyncio
    async def test_list_sessions_filters_by_tenant(self):
        from agent_platform.domain.models import AgentSession
        from agent_platform.persistence.memory import InMemoryAgentSessionRepository

        store = InMemoryAgentSessionRepository()

        s1 = AgentSession(
            session_id="s1", agent_id="a1", tenant_id="tenant-A",
        )
        s2 = AgentSession(
            session_id="s2", agent_id="a1", tenant_id="tenant-B",
        )
        s3 = AgentSession(
            session_id="s3", agent_id="a2", tenant_id="tenant-A",
        )
        await store.save(s1)
        await store.save(s2)
        await store.save(s3)

        # tenant-A 只能看到自己的会话
        sessions_a = await store.list_sessions(tenant_id="tenant-A")
        assert len(sessions_a) == 2
        assert all(s.tenant_id == "tenant-A" for s in sessions_a)

        # tenant-B 只能看到自己的会话
        sessions_b = await store.list_sessions(tenant_id="tenant-B")
        assert len(sessions_b) == 1
        assert sessions_b[0].session_id == "s2"

        # 不传 tenant_id 应返回全部
        sessions_all = await store.list_sessions()
        assert len(sessions_all) == 3

    @pytest.mark.asyncio
    async def test_runtime_manager_list_sessions_with_tenant(self):
        from agent_platform.domain.models import AgentSession
        from agent_platform.persistence.memory import InMemoryAgentSessionRepository
        from agent_platform.runtime.manager import RuntimeManager

        store = InMemoryAgentSessionRepository()
        s1 = AgentSession(
            session_id="s1", agent_id="a1", tenant_id="t1",
        )
        s2 = AgentSession(
            session_id="s2", agent_id="a1", tenant_id="t2",
        )
        await store.save(s1)
        await store.save(s2)

        manager = RuntimeManager(session_store=store)

        sessions = await manager.list_sessions(tenant_id="t1")
        assert len(sessions) == 1
        assert sessions[0].tenant_id == "t1"


class TestDeploymentEventFields:
    """验证 DeploymentEvent 新增字段的序列化。"""

    def test_event_has_eval_and_sha_fields(self):
        event = DeploymentEvent(
            event_type="deploy",
            agent_id="test",
            version="1.0",
            channel="prod",
            status=AgentDeploymentStatus.PROD,
            eval_report_id="eval-123",
            manifest_sha256="sha256hex",
        )

        data = event.model_dump()
        assert data["eval_report_id"] == "eval-123"
        assert data["manifest_sha256"] == "sha256hex"

    def test_event_defaults_none_for_new_fields(self):
        event = DeploymentEvent(
            event_type="deploy",
            agent_id="test",
            version="1.0",
            channel="dev",
            status=AgentDeploymentStatus.REGISTERED,
        )

        assert event.eval_report_id is None
        assert event.manifest_sha256 is None


class TestOpsScriptsImportable:
    """验证运维脚本可正常导入（无语法错误）。"""

    def test_health_patrol_importable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "health_patrol", "scripts/health_patrol.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")
        assert hasattr(mod, "check_health")

    def test_cleanup_sessions_importable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cleanup_sessions", "scripts/cleanup_sessions.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")
        assert hasattr(mod, "cleanup")
