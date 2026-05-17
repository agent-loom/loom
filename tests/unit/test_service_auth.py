"""服务间鉴权测试：JWT + Shared Secret 双模式。"""


import pytest

from agent_platform.api.service_auth import (
    ServiceAuthError,
    ServiceAuthProvider,
    ServiceIdentity,
)


@pytest.fixture()
def provider():
    return ServiceAuthProvider(
        jwt_secret="test-secret-key-for-hmac256",
        shared_secrets={"svc-eval": "eval-secret-123"},
        token_ttl_seconds=3600,
    )


class TestJWTMode:
    """JWT (HMAC-SHA256) 鉴权测试。"""

    def test_issue_and_verify(self, provider):
        identity = ServiceIdentity(
            service_id="svc-runner",
            service_name="Coding Runner",
            permissions=["execute", "read"],
        )
        token = provider.issue_token(identity)
        assert isinstance(token, str)
        assert token.count(".") == 2

        result = provider.verify_token(token)
        assert result.service_id == "svc-runner"
        assert result.service_name == "Coding Runner"
        assert "execute" in result.permissions

    def test_expired_token_rejected(self):
        short_ttl = ServiceAuthProvider(jwt_secret="secret", token_ttl_seconds=-1)
        identity = ServiceIdentity(service_id="svc-a")
        token = short_ttl.issue_token(identity)
        with pytest.raises(ServiceAuthError, match="expired"):
            short_ttl.verify_token(token)

    def test_invalid_signature_rejected(self, provider):
        identity = ServiceIdentity(service_id="svc-a")
        token = provider.issue_token(identity)
        parts = token.split(".")
        parts[2] = "invalidsig"
        tampered = ".".join(parts)
        with pytest.raises(ServiceAuthError, match="signature"):
            provider.verify_token(tampered)

    def test_malformed_jwt_rejected(self, provider):
        with pytest.raises(ServiceAuthError, match="malformed"):
            provider.verify_token("only.two")

    def test_no_jwt_secret_raises(self):
        no_jwt = ServiceAuthProvider(jwt_secret=None)
        with pytest.raises(ServiceAuthError, match="not configured"):
            no_jwt.issue_token(ServiceIdentity(service_id="a"))

    def test_tampered_payload_rejected(self, provider):
        identity = ServiceIdentity(service_id="svc-a")
        token = provider.issue_token(identity)
        parts = token.split(".")
        import base64
        fake = b'{"sub":"hacker","exp":9999999999}'
        parts[1] = base64.urlsafe_b64encode(fake).rstrip(b"=").decode()
        tampered = ".".join(parts)
        with pytest.raises(ServiceAuthError, match="signature"):
            provider.verify_token(tampered)


class TestSharedSecretMode:
    """Shared Secret 鉴权测试。"""

    def test_valid_secret(self, provider):
        result = provider.verify_shared_secret("svc-eval", "eval-secret-123")
        assert result.service_id == "svc-eval"
        assert "service" in result.permissions

    def test_wrong_secret_rejected(self, provider):
        with pytest.raises(ServiceAuthError, match="invalid"):
            provider.verify_shared_secret("svc-eval", "wrong-secret")

    def test_unknown_service_rejected(self, provider):
        with pytest.raises(ServiceAuthError, match="unknown"):
            provider.verify_shared_secret("svc-unknown", "any-secret")

    def test_register_service(self, provider):
        provider.register_service("svc-new", "new-secret")
        result = provider.verify_shared_secret("svc-new", "new-secret")
        assert result.service_id == "svc-new"


class TestUnifiedAuthenticate:
    """统一鉴权入口测试。"""

    def test_jwt_preferred(self, provider):
        identity = ServiceIdentity(service_id="svc-a")
        token = provider.issue_token(identity)
        result = provider.authenticate(token=token)
        assert result.service_id == "svc-a"

    def test_shared_secret_fallback(self, provider):
        result = provider.authenticate(
            service_id="svc-eval", secret="eval-secret-123",
        )
        assert result.service_id == "svc-eval"

    def test_no_credentials_raises(self, provider):
        with pytest.raises(ServiceAuthError, match="no credentials"):
            provider.authenticate()

    def test_jwt_over_shared_secret(self, provider):
        """JWT 优先于 Shared Secret。"""
        identity = ServiceIdentity(service_id="svc-jwt")
        token = provider.issue_token(identity)
        result = provider.authenticate(
            token=token, service_id="svc-eval", secret="eval-secret-123",
        )
        assert result.service_id == "svc-jwt"


class TestServiceIdentity:
    """ServiceIdentity 模型测试。"""

    def test_defaults(self):
        identity = ServiceIdentity(service_id="svc-1")
        assert identity.service_name == ""
        assert identity.permissions == []
        assert identity.metadata == {}

    def test_full_construction(self):
        identity = ServiceIdentity(
            service_id="svc-1",
            service_name="Runner Service",
            permissions=["execute", "read", "write"],
            metadata={"env": "production"},
        )
        assert identity.service_id == "svc-1"
        assert len(identity.permissions) == 3
        assert identity.metadata["env"] == "production"
