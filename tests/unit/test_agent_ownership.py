from pathlib import Path

from agent_platform.devflow.ownership import AgentOwnershipResolver


def test_resolves_explicit_custom_property() -> None:
    resolver = AgentOwnershipResolver(
        project_mappings=[
            {"plane_project_id": "proj-1", "agent_id": "project_agent"},
        ]
    )

    ownership = resolver.resolve(
        work_item={
            "project": "proj-1",
            "properties": {"agent_id": "echo", "task_type": "agent:change"},
        }
    )

    assert ownership is not None
    assert ownership.agent_id == "echo"
    assert ownership.task_type == "agent:change"
    assert ownership.source == "custom_property"


def test_resolves_plane_project_id_mapping() -> None:
    resolver = AgentOwnershipResolver(
        project_mappings=[
            {
                "plane_project_id": "proj-1",
                "agent_id": "echo",
                "task_type": "agent:change",
            },
        ]
    )

    ownership = resolver.resolve(work_item={"project": "proj-1"})

    assert ownership is not None
    assert ownership.agent_id == "echo"
    assert ownership.task_type == "agent:change"
    assert ownership.source == "plane_project_id"


def test_resolves_plane_project_name_mapping() -> None:
    resolver = AgentOwnershipResolver(
        project_mappings=[
            {
                "plane_project_name": "agent-platform",
                "agent_id": "echo",
            },
        ]
    )

    ownership = resolver.resolve(
        work_item={"project": {"id": "proj-1", "name": "Agent-Platform"}}
    )

    assert ownership is not None
    assert ownership.agent_id == "echo"
    assert ownership.source == "plane_project_name"


def test_resolves_label_mapping() -> None:
    resolver = AgentOwnershipResolver(
        label_mappings=[
            {
                "label": "agent:myj",
                "agent_id": "myj",
            },
        ]
    )

    ownership = resolver.resolve(
        work_item={"labels": [{"name": "agent:myj"}]},
    )

    assert ownership is not None
    assert ownership.agent_id == "myj"
    assert ownership.source == "label"


def test_resolves_keyword_mapping() -> None:
    resolver = AgentOwnershipResolver(
        keyword_mappings=[
            {
                "keywords": ["促销", "库存"],
                "agent_id": "myj",
            },
        ]
    )

    ownership = resolver.resolve(
        work_item={"name": "修复门店促销库存查询"},
    )

    assert ownership is not None
    assert ownership.agent_id == "myj"
    assert ownership.source == "keyword"


def test_require_manual_returns_none_when_unresolved() -> None:
    resolver = AgentOwnershipResolver()

    assert resolver.resolve(work_item={"name": "unknown"}) is None


def test_properties_handles_list_format() -> None:
    """Plane API 有时返回 custom_properties 为列表格式 [{key, value}]。"""
    resolver = AgentOwnershipResolver()

    ownership = resolver.resolve(
        work_item={
            "custom_properties": [
                {"key": "agent_id", "value": "echo"},
                {"key": "task_type", "value": "agent:change"},
            ]
        }
    )

    assert ownership is not None
    assert ownership.agent_id == "echo"
    assert ownership.task_type == "agent:change"
    assert ownership.source == "custom_property"


def test_labels_merges_labels_and_label_details() -> None:
    """labels 和 label_details 两个字段都应纳入标签解析，不能只取其一。"""
    resolver = AgentOwnershipResolver(
        label_mappings=[
            {"label": "agent:echo", "agent_id": "echo"},
            {"label": "agent:myj", "agent_id": "myj"},
        ]
    )

    ownership = resolver.resolve(
        work_item={
            "labels": [{"name": "agent:echo"}],
            "label_details": [{"name": "agent:myj"}],
        }
    )

    # label_details 中的 agent:myj 优先级低于 labels 中的 agent:echo
    # 但两者都应被扫描；这里 label_mappings 顺序决定 agent:echo 先匹配
    assert ownership is not None
    assert ownership.agent_id == "echo"


def test_labels_merges_when_only_label_details_present() -> None:
    """只有 label_details 时也能正确解析。"""
    resolver = AgentOwnershipResolver(
        label_mappings=[
            {"label": "agent:myj", "agent_id": "myj"},
        ]
    )

    ownership = resolver.resolve(
        work_item={
            "label_details": [{"name": "agent:myj"}],
        }
    )

    assert ownership is not None
    assert ownership.agent_id == "myj"


def test_loads_mapping_from_yaml(tmp_path: Path) -> None:
    config = tmp_path / "agent_ownership.yaml"
    config.write_text(
        """
version: 1
project_mappings:
  - plane_project_id: proj-1
    agent_id: echo
fallback:
  mode: require_manual
""",
        encoding="utf-8",
    )

    resolver = AgentOwnershipResolver.from_file(config)
    ownership = resolver.resolve(work_item={"project": "proj-1"})

    assert ownership is not None
    assert ownership.agent_id == "echo"
