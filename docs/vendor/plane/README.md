# Plane OpenAPI Snapshot

本目录保存从当前自部署 Plane 实例导出的 OpenAPI 文档。

```text
Plane Web: http://10.193.0.147:3333/
Schema YAML: http://10.193.0.147:3333/api/schema/
Swagger UI: http://10.193.0.147:3333/api/schema/swagger-ui/
ReDoc: http://10.193.0.147:3333/api/schema/redoc/
```

## 文件

| 文件 | 说明 |
| --- | --- |
| `openapi.yaml` | OpenAPI 3.0.3 YAML，来自 `/api/schema/` |
| `openapi.json` | OpenAPI 3.0.3 JSON，通过 `Accept: application/json` 获取 |

## 当前快照

| 项 | 值 |
| --- | --- |
| OpenAPI version | `3.0.3` |
| API title | `The Plane REST API` |
| API version | `0.0.2` |
| paths | `131` |
| operations | `262` |
| tags | `31` |

## 重要注意

当前 schema 同时包含旧 `/issues/` 和新 `/work-items/` endpoint。

后续 `PlaneAdapter` 应优先使用：

```text
/api/v1/workspaces/{slug}/projects/{project_id}/work-items/
```

而不是：

```text
/api/v1/workspaces/{slug}/projects/{project_id}/issues/
```

原因：Plane 官方 API 文档提示 `/issues/` endpoint 正在向 `/work-items/` 迁移。

## DevFlow 优先关注的 API Tag

| Tag | Operations | 用途 |
| --- | ---: | --- |
| `Projects` | 9 | 项目查询和管理 |
| `Work Items` | 23 | 创建、查询、更新任务 |
| `States` | 5 | 看板状态 |
| `Labels` | 5 | 项目内标签 |
| `Work Item Comments` | 10 | 回写 AI / MR / CI / Eval 结果 |
| `Work Item Types` | 11 | 任务类型 |
| `Work Item Properties` | 28 | 自定义字段 |
| `Intake` | 5 | 需求入口 |
| `Cycles` | 13 | 迭代 |
| `Modules` | 11 | 功能模块 |

## 更新方式

```bash
curl -sS -o docs/vendor/plane/openapi.yaml \
  http://10.193.0.147:3333/api/schema/

curl -sS -H 'Accept: application/json' \
  -o docs/vendor/plane/openapi.json \
  http://10.193.0.147:3333/api/schema/
```
