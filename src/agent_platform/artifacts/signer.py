"""产物签名验证器：对 Agent manifest 和 package 进行 SHA-256 签名与校验。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path

from agent_platform.domain.models import AgentManifest

logger = logging.getLogger(__name__)


class ArtifactSigner:
    """产物签名工具：基于 SHA-256 对 manifest 和 package 进行签名和验证。"""

    def sign_manifest(self, manifest: AgentManifest) -> str:
        """计算 manifest 的 SHA-256 哈希值。

        将 manifest 序列化为规范 JSON（按键排序、无多余空格）后计算哈希。
        """
        canonical = json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def verify_manifest(self, manifest: AgentManifest, expected_hash: str) -> bool:
        """验证 manifest 的 SHA-256 哈希值是否与预期一致。"""
        actual = self.sign_manifest(manifest)
        return hmac.compare_digest(actual, expected_hash)

    def sign_package(self, package_path: Path) -> str:
        """计算 package 目录或文件的 SHA-256 哈希值。

        如果是目录，则遍历所有文件按路径排序后逐个更新哈希。
        如果是单个文件（如 tar.gz），则直接计算哈希。
        """
        hasher = hashlib.sha256()

        if package_path.is_file():
            hasher.update(package_path.read_bytes())
        elif package_path.is_dir():
            for file_path in sorted(package_path.rglob("*")):
                if file_path.is_file():
                    rel = str(file_path.relative_to(package_path))
                    # 将相对路径也纳入哈希以确保目录结构完整性
                    hasher.update(rel.encode("utf-8"))
                    hasher.update(file_path.read_bytes())
        else:
            msg = f"路径不存在: {package_path}"
            raise FileNotFoundError(msg)

        return hasher.hexdigest()

    def verify_package(self, package_path: Path, expected_hash: str) -> bool:
        """验证 package 的 SHA-256 哈希值是否与预期一致。"""
        actual = self.sign_package(package_path)
        return hmac.compare_digest(actual, expected_hash)
