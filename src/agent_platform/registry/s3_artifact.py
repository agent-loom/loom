"""S3 制品存储：基于 S3 / MinIO 的远程制品管理实现。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from agent_platform.registry.artifact import (
    ArtifactMetadata,
    _package_directory,
)

logger = logging.getLogger(__name__)

# 标记 boto3 / aiobotocore 是否可用
_BOTO3_AVAILABLE = False
try:
    import boto3  # noqa: F401
    from botocore.exceptions import ClientError  # noqa: F401

    _BOTO3_AVAILABLE = True
except ImportError:
    pass


def _require_boto3() -> None:
    """检查 boto3 是否已安装，未安装时抛出 ImportError。"""
    if not _BOTO3_AVAILABLE:
        raise ImportError(
            "boto3 未安装。请通过 'pip install boto3' 安装后再使用 S3ArtifactStore。"
        )


class S3ArtifactStore:
    """基于 S3 的制品存储，兼容 AWS S3 和 MinIO。

    使用 boto3 同步客户端实现所有存储操作。
    当 boto3 不可用时，构造函数会优雅地记录警告；
    方法调用时才会抛出 ImportError。
    """

    def __init__(
        self,
        *,
        bucket_name: str,
        prefix: str = "artifacts",
        region: str = "us-east-1",
        endpoint_url: str | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._prefix = prefix.strip("/")
        self._region = region
        self._endpoint_url = endpoint_url
        self._client = None

        if not _BOTO3_AVAILABLE:
            logger.warning(
                "boto3 未安装，S3ArtifactStore 将在方法调用时抛出 ImportError。"
            )

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _get_client(self):
        """懒初始化 S3 客户端。"""
        _require_boto3()
        if self._client is None:
            import boto3 as _boto3

            kwargs: dict = {"region_name": self._region}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            self._client = _boto3.client("s3", **kwargs)
        return self._client

    def _object_key(self, artifact_id: str, suffix: str) -> str:
        """生成 S3 对象键。"""
        return f"{self._prefix}/{artifact_id}/{suffix}"

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def create_artifact(
        self, agent_id: str, version: str, package_path: Path
    ) -> ArtifactMetadata:
        """打包目录并上传到 S3，返回元数据。"""
        client = self._get_client()
        artifact_id = f"{agent_id}@{version}"

        # 打包并计算校验和
        data, files, manifest_sha = _package_directory(package_path)
        checksum = hashlib.sha256(data).hexdigest()

        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            agent_id=agent_id,
            version=version,
            checksum_sha256=checksum,
            size_bytes=len(data),
            files=files,
            manifest_sha256=manifest_sha,
        )

        # 上传制品数据
        data_key = self._object_key(artifact_id, "artifact.tar.gz")
        client.put_object(
            Bucket=self._bucket_name,
            Key=data_key,
            Body=data,
            ContentType="application/gzip",
            Metadata={"sha256": checksum},
        )

        # 上传元数据 JSON
        meta_key = self._object_key(artifact_id, "metadata.json")
        client.put_object(
            Bucket=self._bucket_name,
            Key=meta_key,
            Body=metadata.model_dump_json(indent=2).encode("utf-8"),
            ContentType="application/json",
        )

        logger.info(
            "已上传制品 %s 到 s3://%s/%s (%d 字节, %d 文件, sha256=%s...)",
            artifact_id,
            self._bucket_name,
            data_key,
            len(data),
            len(files),
            checksum[:16],
        )
        return metadata

    def get_metadata(self, artifact_id: str) -> ArtifactMetadata | None:
        """从 S3 获取制品元数据。"""
        client = self._get_client()
        meta_key = self._object_key(artifact_id, "metadata.json")
        try:
            resp = client.get_object(Bucket=self._bucket_name, Key=meta_key)
            body = resp["Body"].read()
            return ArtifactMetadata.model_validate_json(body)
        except client.exceptions.NoSuchKey:
            return None
        except Exception:
            logger.warning("获取制品元数据失败: %s", artifact_id, exc_info=True)
            return None

    def get_data(self, artifact_id: str) -> bytes | None:
        """从 S3 下载制品数据。"""
        client = self._get_client()
        data_key = self._object_key(artifact_id, "artifact.tar.gz")
        try:
            resp = client.get_object(Bucket=self._bucket_name, Key=data_key)
            return resp["Body"].read()
        except client.exceptions.NoSuchKey:
            return None
        except Exception:
            logger.warning("获取制品数据失败: %s", artifact_id, exc_info=True)
            return None

    def list_artifacts(self, agent_id: str | None = None) -> list[ArtifactMetadata]:
        """列出 S3 中的所有制品（可按 agent_id 过滤）。"""
        client = self._get_client()
        # 构建搜索前缀
        if agent_id:
            search_prefix = f"{self._prefix}/{agent_id}@"
        else:
            search_prefix = f"{self._prefix}/"

        results: list[ArtifactMetadata] = []
        paginator = client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(
                Bucket=self._bucket_name, Prefix=search_prefix
            ):
                for obj in page.get("Contents", []):
                    key: str = obj["Key"]
                    if key.endswith("/metadata.json"):
                        try:
                            resp = client.get_object(
                                Bucket=self._bucket_name, Key=key
                            )
                            body = resp["Body"].read()
                            meta = ArtifactMetadata.model_validate_json(body)
                            results.append(meta)
                        except Exception:
                            logger.warning("解析元数据失败: %s", key)
        except Exception:
            logger.warning("列出制品失败", exc_info=True)

        return results

    def verify_checksum(self, artifact_id: str) -> bool:
        """下载制品数据并验证 SHA-256 是否与元数据一致。"""
        metadata = self.get_metadata(artifact_id)
        data = self.get_data(artifact_id)
        if not metadata or not data:
            return False
        return hashlib.sha256(data).hexdigest() == metadata.checksum_sha256

    def list_versions(self, agent_id: str) -> list[str]:
        """列出指定 agent 的所有版本（已排序）。"""
        artifacts = self.list_artifacts(agent_id=agent_id)
        versions = [a.version for a in artifacts]
        return sorted(versions)

    def get_previous_version(
        self, agent_id: str, current_version: str
    ) -> str | None:
        """获取指定版本的前一个版本。"""
        versions = self.list_versions(agent_id)
        try:
            idx = versions.index(current_version)
            if idx > 0:
                return versions[idx - 1]
        except ValueError:
            pass
        return None

    def delete_artifact(self, artifact_id: str) -> bool:
        """从 S3 删除制品及其元数据。"""
        client = self._get_client()
        data_key = self._object_key(artifact_id, "artifact.tar.gz")
        meta_key = self._object_key(artifact_id, "metadata.json")
        try:
            client.delete_object(Bucket=self._bucket_name, Key=data_key)
            client.delete_object(Bucket=self._bucket_name, Key=meta_key)
            logger.info("已删除制品: %s", artifact_id)
            return True
        except Exception:
            logger.warning("删除制品失败: %s", artifact_id, exc_info=True)
            return False

    def artifact_exists(self, artifact_id: str) -> bool:
        """检查制品是否存在于 S3。"""
        client = self._get_client()
        meta_key = self._object_key(artifact_id, "metadata.json")
        try:
            client.head_object(Bucket=self._bucket_name, Key=meta_key)
            return True
        except Exception:
            return False
