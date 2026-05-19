from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AgentOwnership:
    agent_id: str
    task_type: str
    source: str
    confidence: float
    reason: str


class AgentOwnershipResolver:
    """Resolve which business agent owns a Plane work item.

    Resolution is intentionally deterministic. Free-form semantic inference can
    be added later, but DevFlow should not generate executable task packs with
    placeholder values such as ``<agent_id>``.
    """

    def __init__(
        self,
        *,
        project_mappings: list[dict[str, Any]] | None = None,
        label_mappings: list[dict[str, Any]] | None = None,
        keyword_mappings: list[dict[str, Any]] | None = None,
        fallback_mode: str = "require_manual",
        fallback_agent_id: str | None = None,
        default_task_type: str = "agent:change",
    ):
        self.project_mappings = project_mappings or []
        self.label_mappings = label_mappings or []
        self.keyword_mappings = keyword_mappings or []
        self.fallback_mode = fallback_mode
        self.fallback_agent_id = fallback_agent_id
        self.default_task_type = default_task_type

    @classmethod
    def from_file(cls, path: str | Path | None) -> AgentOwnershipResolver:
        if not path:
            return cls()
        config_path = Path(path)
        if not config_path.exists():
            return cls()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        fallback = data.get("fallback") or {}
        return cls(
            project_mappings=data.get("project_mappings") or [],
            label_mappings=data.get("label_mappings") or [],
            keyword_mappings=data.get("keyword_mappings") or [],
            fallback_mode=fallback.get("mode", "require_manual"),
            fallback_agent_id=fallback.get("agent_id"),
            default_task_type=data.get("default_task_type", "agent:change"),
        )

    def resolve(
        self,
        *,
        work_item: dict[str, Any],
        work_item_detail: dict[str, Any] | None = None,
    ) -> AgentOwnership | None:
        detail = work_item_detail or {}
        explicit = self._resolve_explicit(detail) or self._resolve_explicit(work_item)
        if explicit:
            return explicit

        project = self._project_value(detail) or self._project_value(work_item)
        project_name = self._project_name(detail) or self._project_name(work_item)
        by_project = self._resolve_project(project_id=project, project_name=project_name)
        if by_project:
            return by_project

        labels = self._labels(detail) or self._labels(work_item)
        by_label = self._resolve_label(labels)
        if by_label:
            return by_label

        text = " ".join(
            str(value)
            for value in (
                detail.get("name"),
                detail.get("title"),
                detail.get("description_stripped"),
                detail.get("description"),
                work_item.get("name"),
                work_item.get("title"),
                work_item.get("description_stripped"),
                work_item.get("description"),
            )
            if value
        )
        by_keyword = self._resolve_keyword(text)
        if by_keyword:
            return by_keyword

        if self.fallback_mode == "default_agent" and self.fallback_agent_id:
            return AgentOwnership(
                agent_id=self.fallback_agent_id,
                task_type=self.default_task_type,
                source="fallback",
                confidence=0.2,
                reason=f"Fallback default agent {self.fallback_agent_id}",
            )
        return None

    def _resolve_explicit(self, item: dict[str, Any]) -> AgentOwnership | None:
        props = self._properties(item)
        agent_id = props.get("agent_id")
        if not agent_id:
            return None
        task_type = props.get("task_type") or self.default_task_type
        return AgentOwnership(
            agent_id=str(agent_id),
            task_type=str(task_type),
            source="custom_property",
            confidence=1.0,
            reason="Work item custom property agent_id",
        )

    def _resolve_project(
        self,
        *,
        project_id: str | None,
        project_name: str | None,
    ) -> AgentOwnership | None:
        normalized_name = _norm(project_name)
        for mapping in self.project_mappings:
            if project_id and str(mapping.get("plane_project_id") or "") == project_id:
                return self._ownership_from_mapping(
                    mapping,
                    source="plane_project_id",
                    reason=f"Plane project id {project_id} mapped to agent",
                )
            mapping_name = mapping.get("plane_project_name")
            if normalized_name and mapping_name and _norm(str(mapping_name)) == normalized_name:
                return self._ownership_from_mapping(
                    mapping,
                    source="plane_project_name",
                    reason=f"Plane project name {project_name} mapped to agent",
                )
        return None

    def _resolve_label(self, labels: list[str]) -> AgentOwnership | None:
        normalized_labels = {_norm(label) for label in labels}
        for mapping in self.label_mappings:
            label = mapping.get("label")
            if label and _norm(str(label)) in normalized_labels:
                return self._ownership_from_mapping(
                    mapping,
                    source="label",
                    reason=f"Label {label} mapped to agent",
                    confidence=0.9,
                )
        return None

    def _resolve_keyword(self, text: str) -> AgentOwnership | None:
        normalized_text = _norm(text)
        if not normalized_text:
            return None
        for mapping in self.keyword_mappings:
            keywords = mapping.get("keywords") or []
            if any(_norm(str(keyword)) in normalized_text for keyword in keywords):
                return self._ownership_from_mapping(
                    mapping,
                    source="keyword",
                    reason="Keyword mapping matched",
                    confidence=0.6,
                )
        return None

    def _ownership_from_mapping(
        self,
        mapping: dict[str, Any],
        *,
        source: str,
        reason: str,
        confidence: float = 1.0,
    ) -> AgentOwnership | None:
        agent_id = mapping.get("agent_id")
        if not agent_id:
            return None
        return AgentOwnership(
            agent_id=str(agent_id),
            task_type=str(mapping.get("task_type") or self.default_task_type),
            source=source,
            confidence=confidence,
            reason=reason,
        )

    @staticmethod
    def _properties(item: dict[str, Any]) -> dict[str, Any]:
        props = item.get("properties") or item.get("custom_properties") or {}
        return props if isinstance(props, dict) else {}

    @staticmethod
    def _project_value(item: dict[str, Any]) -> str | None:
        project = item.get("project") or item.get("project_id")
        if isinstance(project, dict):
            return str(project.get("id")) if project.get("id") else None
        return str(project) if project else None

    @staticmethod
    def _project_name(item: dict[str, Any]) -> str | None:
        project = item.get("project")
        if isinstance(project, dict):
            return (
                str(project.get("name"))
                if project.get("name")
                else str(project.get("identifier"))
                if project.get("identifier")
                else None
            )
        project_detail = item.get("project_detail") or item.get("project_data") or {}
        if isinstance(project_detail, dict):
            name = project_detail.get("name") or project_detail.get("identifier")
            return str(name) if name else None
        return None

    @staticmethod
    def _labels(item: dict[str, Any]) -> list[str]:
        labels = item.get("labels") or item.get("label_details") or []
        result: list[str] = []
        if not isinstance(labels, list):
            return result
        for label in labels:
            if isinstance(label, str):
                result.append(label)
            elif isinstance(label, dict):
                name = label.get("name") or label.get("identifier")
                if name:
                    result.append(str(name))
        return result


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()
