"""Agent Skills 目录扫描器。

扫描 agents/<agent_id>/skills/**/manifest.yaml，
将发现的 Skill 注册到 SkillRepository。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .memory_models import MemoryStatus, SkillEntry, SkillProvenance
from .memory_repository import SkillRepository

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        logger.warning("无法解析 skill manifest: %s", path, exc_info=True)
        return None


def _manifest_to_skill_entry(
    agent_id: str,
    manifest_path: Path,
    data: dict[str, Any],
) -> SkillEntry:
    provenance_raw = data.get("created_by", "user_created")
    try:
        provenance = SkillProvenance(provenance_raw)
    except ValueError:
        provenance = SkillProvenance.USER_CREATED

    status_raw = data.get("status", "active")
    try:
        status = MemoryStatus(status_raw)
    except ValueError:
        status = MemoryStatus.ACTIVE

    return SkillEntry(
        agent_id=agent_id,
        name=data.get("skill_id") or data.get("title") or manifest_path.parent.name,
        description=data.get("description", ""),
        path=str(manifest_path.relative_to(manifest_path.parents[3])),
        provenance=provenance,
        status=status,
        tags=data.get("tags", []),
        metadata={
            "version": data.get("version", "0.0.0"),
            "risk_level": data.get("risk_level", "low"),
            "schema_version": data.get("schema_version", 1),
        },
    )


def scan_agent_skills(agents_dir: Path) -> list[SkillEntry]:
    """扫描所有 agent 的 skills 目录，返回 SkillEntry 列表。"""
    entries: list[SkillEntry] = []
    if not agents_dir.is_dir():
        return entries

    for agent_dir in sorted(agents_dir.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name.startswith((".", "_")):
            continue
        skills_dir = agent_dir / "skills"
        if not skills_dir.is_dir():
            continue

        agent_id = agent_dir.name
        for manifest_path in sorted(skills_dir.rglob("manifest.yaml")):
            data = _load_yaml(manifest_path)
            if data is None:
                continue
            entry = _manifest_to_skill_entry(agent_id, manifest_path, data)
            entries.append(entry)
            logger.info("发现 skill: agent=%s name=%s path=%s", agent_id, entry.name, entry.path)

    return entries


async def sync_skills_to_repo(
    agents_dir: Path,
    repo: SkillRepository,
) -> dict[str, int]:
    """扫描并同步 skills 到 repository，返回统计。"""
    scanned = scan_agent_skills(agents_dir)
    existing = await repo.list_all(limit=9999)
    existing_paths = {s.path: s for s in existing}

    created = 0
    updated = 0
    for entry in scanned:
        if entry.path in existing_paths:
            old = existing_paths[entry.path]
            old.name = entry.name
            old.description = entry.description
            old.tags = entry.tags
            old.metadata = entry.metadata
            old.status = entry.status
            await repo.update(old)
            updated += 1
        else:
            await repo.create(entry)
            created += 1

    return {"scanned": len(scanned), "created": created, "updated": updated}
