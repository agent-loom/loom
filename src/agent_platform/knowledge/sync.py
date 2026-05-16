"""数据同步模块，负责将本地或远程数据源同步到 Weaviate 向量数据库。"""

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.models import ManifestKnowledgeSource
from agent_platform.knowledge.service import KnowledgeBackend

logger = logging.getLogger(__name__)


class DataDocument(BaseModel):
    """待同步的数据文档记录。"""
    doc_id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataSynchronization:
    """数据同步器，协调文件信息流向后端写入。"""

    def __init__(self, backend: KnowledgeBackend):
        self.backend = backend

    async def sync_directory(
        self,
        source: ManifestKnowledgeSource,
        directory_path: Path,
        file_extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """批量读取目录下的文件，并调用同步接口写入数据库。"""
        file_extensions = file_extensions or [".txt", ".md"]
        if not directory_path.exists() or not directory_path.is_dir():
            logger.error("Sync failed: Directory not found: %s", directory_path)
            return {"status": "error", "message": f"directory not found: {directory_path}"}

        documents: list[DataDocument] = []
        for ext in file_extensions:
            for filepath in directory_path.rglob(f"*{ext}"):
                try:
                    content = filepath.read_text(encoding="utf-8")
                    documents.append(
                        DataDocument(
                            doc_id=filepath.name,
                            content=content,
                            metadata={"filepath": str(filepath)},
                        )
                    )
                except Exception as e:
                    logger.warning("Failed to read %s: %s", filepath, e)

        logger.info("Found %d documents for %s", len(documents), source.collection)

        doc_dicts = [d.model_dump() for d in documents]
        try:
            result = await self.backend.sync(source, documents=doc_dicts)
        except TypeError:
            result = await self.backend.sync(source)

        result["processed_documents"] = len(documents)
        return result
