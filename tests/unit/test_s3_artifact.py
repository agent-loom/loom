"""S3ArtifactStore 单元测试：使用 mock boto3 验证所有 S3 交互。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_platform.registry.artifact import ArtifactMetadata

# ---------------------------------------------------------------------------
# 公用 fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def agent_dir(tmp_path: Path) -> Path:
    """创建一个包含多文件的 agent 目录用于打包。"""
    pkg = tmp_path / "my_agent"
    pkg.mkdir()
    (pkg / "main.py").write_text("print('hello')")
    (pkg / "manifest.yaml").write_text("name: my_agent\nversion: 1.0.0\n")
    sub = pkg / "utils"
    sub.mkdir()
    (sub / "helper.py").write_text("def helper(): pass")
    return pkg


@pytest.fixture()
def mock_s3_client():
    """创建一个 mock S3 客户端。"""
    client = MagicMock()
    # 模拟 NoSuchKey 异常类
    client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    return client


@pytest.fixture()
def s3_store(mock_s3_client):
    """创建 S3ArtifactStore 实例，注入 mock 客户端。"""
    with patch("agent_platform.registry.s3_artifact._BOTO3_AVAILABLE", True):
        from agent_platform.registry.s3_artifact import S3ArtifactStore

        store = S3ArtifactStore(
            bucket_name="test-bucket",
            prefix="artifacts",
            region="us-east-1",
            endpoint_url="http://localhost:9000",
        )
        # 直接注入 mock 客户端，绕过 _get_client 中的 boto3 import
        store._client = mock_s3_client
        yield store


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


class TestS3ArtifactStoreCreate:
    """测试制品创建和上传。"""

    def test_create_artifact_uploads_data_and_metadata(
        self, s3_store, mock_s3_client, agent_dir
    ):
        """验证 create_artifact 会上传两个对象（数据 + 元数据）。"""
        meta = s3_store.create_artifact("agent-a", "1.0.0", agent_dir)

        assert meta.artifact_id == "agent-a@1.0.0"
        assert meta.agent_id == "agent-a"
        assert meta.version == "1.0.0"
        assert meta.size_bytes > 0
        assert len(meta.files) == 3
        assert meta.checksum_sha256

        # 验证调用了两次 put_object（数据 + 元数据）
        put_calls = mock_s3_client.put_object.call_args_list
        assert len(put_calls) == 2

        # 第一次是制品数据
        data_call = put_calls[0]
        assert data_call.kwargs["Key"] == "artifacts/agent-a@1.0.0/artifact.tar.gz"
        assert data_call.kwargs["Bucket"] == "test-bucket"
        assert data_call.kwargs["ContentType"] == "application/gzip"

        # 第二次是元数据
        meta_call = put_calls[1]
        assert meta_call.kwargs["Key"] == "artifacts/agent-a@1.0.0/metadata.json"

    def test_create_artifact_sha256_in_s3_metadata(
        self, s3_store, mock_s3_client, agent_dir
    ):
        """验证上传时 S3 对象的自定义元数据包含 sha256。"""
        meta = s3_store.create_artifact("agent-a", "1.0.0", agent_dir)

        data_call = mock_s3_client.put_object.call_args_list[0]
        s3_metadata = data_call.kwargs["Metadata"]
        assert s3_metadata["sha256"] == meta.checksum_sha256

    def test_create_artifact_checksum_correct(
        self, s3_store, mock_s3_client, agent_dir
    ):
        """验证元数据中的 SHA-256 与实际上传数据一致。"""
        meta = s3_store.create_artifact("agent-a", "1.0.0", agent_dir)

        data_call = mock_s3_client.put_object.call_args_list[0]
        uploaded_data = data_call.kwargs["Body"]
        expected_sha = hashlib.sha256(uploaded_data).hexdigest()
        assert meta.checksum_sha256 == expected_sha


class TestS3ArtifactStoreGet:
    """测试制品读取。"""

    def test_get_metadata_success(self, s3_store, mock_s3_client):
        """验证成功获取元数据。"""
        sample_meta = ArtifactMetadata(
            artifact_id="agent-a@1.0.0",
            agent_id="agent-a",
            version="1.0.0",
            checksum_sha256="abc123",
            size_bytes=1024,
            files=["main.py"],
        )
        body_mock = MagicMock()
        body_mock.read.return_value = sample_meta.model_dump_json().encode("utf-8")
        mock_s3_client.get_object.return_value = {"Body": body_mock}

        result = s3_store.get_metadata("agent-a@1.0.0")
        assert result is not None
        assert result.artifact_id == "agent-a@1.0.0"
        assert result.checksum_sha256 == "abc123"

    def test_get_metadata_not_found(self, s3_store, mock_s3_client):
        """验证制品不存在时返回 None。"""
        mock_s3_client.get_object.side_effect = mock_s3_client.exceptions.NoSuchKey()

        result = s3_store.get_metadata("missing@0.0.0")
        assert result is None

    def test_get_data_success(self, s3_store, mock_s3_client):
        """验证成功下载制品数据。"""
        body_mock = MagicMock()
        body_mock.read.return_value = b"fake-tar-gz-data"
        mock_s3_client.get_object.return_value = {"Body": body_mock}

        data = s3_store.get_data("agent-a@1.0.0")
        assert data == b"fake-tar-gz-data"

    def test_get_data_not_found(self, s3_store, mock_s3_client):
        """验证制品数据不存在时返回 None。"""
        mock_s3_client.get_object.side_effect = mock_s3_client.exceptions.NoSuchKey()

        result = s3_store.get_data("missing@0.0.0")
        assert result is None


class TestS3ArtifactStoreVerify:
    """测试 SHA-256 校验。"""

    def test_verify_checksum_pass(self, s3_store, mock_s3_client):
        """验证校验和匹配时返回 True。"""
        data = b"consistent-data"
        sha = hashlib.sha256(data).hexdigest()

        meta = ArtifactMetadata(
            artifact_id="agent-a@1.0.0",
            agent_id="agent-a",
            version="1.0.0",
            checksum_sha256=sha,
            size_bytes=len(data),
        )

        def get_object_side_effect(**kwargs):
            key = kwargs["Key"]
            body_mock = MagicMock()
            if key.endswith("metadata.json"):
                body_mock.read.return_value = meta.model_dump_json().encode("utf-8")
            else:
                body_mock.read.return_value = data
            return {"Body": body_mock}

        mock_s3_client.get_object.side_effect = get_object_side_effect

        assert s3_store.verify_checksum("agent-a@1.0.0") is True

    def test_verify_checksum_fail(self, s3_store, mock_s3_client):
        """验证校验和不匹配时返回 False。"""
        meta = ArtifactMetadata(
            artifact_id="agent-a@1.0.0",
            agent_id="agent-a",
            version="1.0.0",
            checksum_sha256="wrong-hash",
            size_bytes=100,
        )

        def get_object_side_effect(**kwargs):
            key = kwargs["Key"]
            body_mock = MagicMock()
            if key.endswith("metadata.json"):
                body_mock.read.return_value = meta.model_dump_json().encode("utf-8")
            else:
                body_mock.read.return_value = b"actual-data"
            return {"Body": body_mock}

        mock_s3_client.get_object.side_effect = get_object_side_effect

        assert s3_store.verify_checksum("agent-a@1.0.0") is False

    def test_verify_checksum_missing_artifact(self, s3_store, mock_s3_client):
        """验证制品不存在时返回 False。"""
        mock_s3_client.get_object.side_effect = mock_s3_client.exceptions.NoSuchKey()
        assert s3_store.verify_checksum("missing@0.0.0") is False


class TestS3ArtifactStoreDegradation:
    """测试 boto3 不可用时的降级行为。"""

    def test_import_error_when_boto3_unavailable(self):
        """验证 boto3 不可用时，方法调用抛出 ImportError。"""
        with patch("agent_platform.registry.s3_artifact._BOTO3_AVAILABLE", False):
            from agent_platform.registry.s3_artifact import S3ArtifactStore

            store = S3ArtifactStore(bucket_name="test-bucket")
            with pytest.raises(ImportError, match="boto3 未安装"):
                store.get_metadata("agent-a@1.0.0")

    def test_constructor_does_not_crash_without_boto3(self):
        """验证构造函数在 boto3 不可用时不会崩溃。"""
        with patch("agent_platform.registry.s3_artifact._BOTO3_AVAILABLE", False):
            from agent_platform.registry.s3_artifact import S3ArtifactStore

            # 不应抛出异常
            store = S3ArtifactStore(
                bucket_name="test-bucket",
                prefix="art",
                region="ap-east-1",
            )
            assert store._bucket_name == "test-bucket"

    def test_create_artifact_raises_without_boto3(self, tmp_path):
        """验证 create_artifact 在 boto3 不可用时抛出 ImportError。"""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "main.py").write_text("pass")

        with patch("agent_platform.registry.s3_artifact._BOTO3_AVAILABLE", False):
            from agent_platform.registry.s3_artifact import S3ArtifactStore

            store = S3ArtifactStore(bucket_name="test-bucket")
            with pytest.raises(ImportError):
                store.create_artifact("agent-a", "1.0.0", pkg)


class TestS3ArtifactStoreList:
    """测试列表和版本管理。"""

    def test_list_artifacts_with_agent_filter(self, s3_store, mock_s3_client):
        """验证按 agent_id 过滤列出制品。"""
        meta_a = ArtifactMetadata(
            artifact_id="agent-a@1.0.0",
            agent_id="agent-a",
            version="1.0.0",
            checksum_sha256="sha1",
            size_bytes=100,
        )

        paginator = MagicMock()
        mock_s3_client.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "artifacts/agent-a@1.0.0/metadata.json"},
                ]
            }
        ]

        body_mock = MagicMock()
        body_mock.read.return_value = meta_a.model_dump_json().encode("utf-8")
        mock_s3_client.get_object.return_value = {"Body": body_mock}

        results = s3_store.list_artifacts(agent_id="agent-a")
        assert len(results) == 1
        assert results[0].agent_id == "agent-a"

    def test_list_artifacts_empty(self, s3_store, mock_s3_client):
        """验证无制品时返回空列表。"""
        paginator = MagicMock()
        mock_s3_client.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{"Contents": []}]

        results = s3_store.list_artifacts()
        assert results == []

    def test_object_key_format(self, s3_store):
        """验证 S3 对象键的格式正确。"""
        key = s3_store._object_key("agent-a@1.0.0", "artifact.tar.gz")
        assert key == "artifacts/agent-a@1.0.0/artifact.tar.gz"

    def test_custom_prefix(self, mock_s3_client):
        """验证自定义前缀生效。"""
        with patch("agent_platform.registry.s3_artifact._BOTO3_AVAILABLE", True):
            from agent_platform.registry.s3_artifact import S3ArtifactStore

            store = S3ArtifactStore(
                bucket_name="bucket",
                prefix="custom/path/",
            )
            store._client = mock_s3_client
            key = store._object_key("test@1.0", "metadata.json")
            assert key == "custom/path/test@1.0/metadata.json"

    def test_endpoint_url_for_minio(self, mock_s3_client):
        """验证 MinIO endpoint_url 配置被保存。"""
        with patch("agent_platform.registry.s3_artifact._BOTO3_AVAILABLE", True):
            from agent_platform.registry.s3_artifact import S3ArtifactStore

            store = S3ArtifactStore(
                bucket_name="minio-bucket",
                endpoint_url="http://minio:9000",
            )
            assert store._endpoint_url == "http://minio:9000"
