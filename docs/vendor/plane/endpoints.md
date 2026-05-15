# Plane API Endpoint 摘要

> Last verified: 2026-05-15

本文档从 `openapi.json` 提取 Agent Platform DevFlow 近期最需要的 Plane API 分组。完整 API 以 `openapi.yaml` / `openapi.json` 为准。

## Projects

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/` | `list_projects` |
| `POST` | `/api/v1/workspaces/{slug}/projects/` | `create_project` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{pk}/` | `retrieve_project` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{pk}/` | `update_project` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{pk}/` | `delete_project` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/archive/` | `archive_project` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/archive/` | `unarchive_project` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/features/` | `get_project_features` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/features/` | `update_project_features` |

## Work Items

优先使用 `/work-items/` endpoint，不使用旧 `/issues/` endpoint。

> 注意：Operation 名称中的 `_2`、`_3` 后缀是 OpenAPI 自动去重产物（旧 `/issues/` 占用了无后缀名）。实现时只使用 `_2` 系列（即 `/work-items/` 路径）。`create_work_item_3`（`/work-items/create/`）是批量创建入口，普通创建使用 `create_work_item_2`（POST `/work-items/`）。

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/` | `list_work_items_2` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/` | `create_work_item_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/` | `retrieve_work_item_2` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/` | `update_work_item_2` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/` | `delete_work_item_2` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/create/` | `create_work_item_3` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/properties/` | `get_work_item_properties` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/properties/` | `update_work_item_properties` |
| `GET` | `/api/v1/workspaces/{slug}/work-items/{project_identifier}-{issue_identifier}/` | `get_workspace_work_item_2` |
| `GET` | `/api/v1/workspaces/{slug}/work-items/search/` | `search_work_items_2` |

## States

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/states/` | `list_states` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/states/` | `create_state` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/states/{state_id}/` | `retrieve_state` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/states/{state_id}/` | `update_state` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/states/{state_id}/` | `delete_state` |

## Labels

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/labels/` | `list_labels` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/labels/` | `create_label` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/labels/{pk}/` | `get_labels` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/labels/{pk}/` | `update_label` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/labels/{pk}/` | `delete_label` |

## Work Item Comments

优先使用 `/work-items/{issue_id}/comments/` endpoint。

> 注意：路径中的参数名仍为 `issue_id`，这是 Plane API 的命名遗留。实际传入的是 work item 的 UUID。

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/comments/` | `list_work_item_comments_2` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/comments/` | `create_work_item_comment_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/comments/{pk}/` | `retrieve_work_item_comment_2` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/comments/{pk}/` | `update_work_item_comment_2` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/comments/{pk}/` | `delete_work_item_comment_2` |

## Work Item Types

优先使用 `/work-item-types/` endpoint。

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/` | `list_issue_types_2` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/` | `create_issue_type_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/` | `retrieve_issue_type_2` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/` | `update_issue_type_2` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/` | `delete_issue_type_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/schema/` | `get_work_item_type_schema` |

## Work Item Properties

优先使用 `/work-item-properties/` endpoint。

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/work-item-properties/` | `list_issue_properties_2` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/work-item-properties/` | `create_issue_property_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/work-item-properties/{property_id}/` | `retrieve_issue_property_2` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/work-item-properties/{property_id}/` | `update_issue_property_2` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/work-item-properties/{property_id}/` | `delete_issue_property_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-properties/{property_id}/options/` | `list_issue_property_options_2` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-properties/{property_id}/options/` | `create_issue_property_option_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/work-item-properties/values/` | `list_issue_property_values_for_a_workitem_2` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{work_item_id}/work-item-properties/{property_id}/values/` | `get_work_item_property_value` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{work_item_id}/work-item-properties/{property_id}/values/` | `create_or_update_work_item_property_value` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{work_item_id}/work-item-properties/{property_id}/values/` | `update_work_item_property_value` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/work-items/{work_item_id}/work-item-properties/{property_id}/values/` | `delete_work_item_property_value` |

## Intake

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/intake-issues/` | `get_intake_work_items_list` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/intake-issues/` | `create_intake_work_item` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/intake-issues/{issue_id}/` | `retrieve_intake_work_item` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/intake-issues/{issue_id}/` | `update_intake_work_item` |
| `DELETE` | `/api/v1/workspaces/{slug}/projects/{project_id}/intake-issues/{issue_id}/` | `delete_intake_work_item` |

## Cycles

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/cycles/` | `list_cycles` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/cycles/` | `create_cycle` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/cycles/{cycle_id}/cycle-issues/` | `list_cycle_work_items` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/cycles/{cycle_id}/cycle-issues/` | `add_cycle_work_items` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/cycles/{pk}/` | `update_cycle` |

## Modules

| Method | Path | Operation |
| --- | --- | --- |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/modules/` | `list_modules` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/modules/` | `create_module` |
| `GET` | `/api/v1/workspaces/{slug}/projects/{project_id}/modules/{module_id}/module-issues/` | `list_module_work_items` |
| `POST` | `/api/v1/workspaces/{slug}/projects/{project_id}/modules/{module_id}/module-issues/` | `add_module_work_items` |
| `PATCH` | `/api/v1/workspaces/{slug}/projects/{project_id}/modules/{pk}/` | `update_module` |
