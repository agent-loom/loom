# Plane API / MCP 文档获取方案

本文档说明如何获取 Plane 的完整 REST API 文档和 MCP 文档，并将其用于 `agent-platform` 的 `PlaneAdapter`、DevFlow、Codex / Claude Code 集成。

当前 Plane 实例：

```text
Plane Web: http://10.193.0.147:3333/
Plane API Base URL: http://10.193.0.147:3333/api
```

## 1. 结论

可以获取完整文档，但分两类：

1. **完整 REST API 文档**：推荐从自部署 Plane 实例导出 OpenAPI schema。
2. **完整 MCP 文档**：推荐使用官方 MCP 文档 + `plane-mcp-server` 包 + 本地配置验证。

当前状态：

```text
GET http://10.193.0.147:3333/api/schema/ -> 200
GET http://10.193.0.147:3333/api/schema/swagger-ui/ -> 200
GET http://10.193.0.147:3333/api/schema/?format=openapi-json -> 404
```

已成功导出：

```text
docs/vendor/plane/openapi.yaml
docs/vendor/plane/openapi.json
docs/vendor/plane/endpoints.md
```

其中 JSON 通过 `Accept: application/json` header 从 `/api/schema/` 获取。当前实例不支持 `?format=openapi-json` 方式，后续更新 JSON 时必须继续使用 header。

## 2. 获取完整 REST API 文档

Plane 官方说明：Plane 使用 `drf-spectacular` 生成 OpenAPI 3.0 schema。该功能默认关闭，需要显式开启。

### 2.1 在 Plane API 服务开启 OpenAPI

在 Plane API 服务的环境变量中加入：

```env
ENABLE_DRF_SPECTACULAR=1
```

然后重启 Plane API 服务。

如果是 Docker / Docker Compose 部署，通常需要在 Plane API 服务对应的 `.env` 或 compose 环境变量中加入该配置。

### 2.2 可用 endpoint

开启后应该可以访问：

```text
GET http://10.193.0.147:3333/api/schema/
GET http://10.193.0.147:3333/api/schema/swagger-ui/
GET http://10.193.0.147:3333/api/schema/redoc/
```

含义：

| Endpoint | 说明 |
| --- | --- |
| `/api/schema/` | YAML 格式 OpenAPI schema |
| `/api/schema/` + `Accept: application/json` | JSON 格式 OpenAPI schema |
| `/api/schema/swagger-ui/` | Swagger UI |
| `/api/schema/redoc/` | ReDoc 文档 |

### 2.3 下载命令

YAML：

```bash
curl -sS -o docs/vendor/plane/openapi.yaml \
  http://10.193.0.147:3333/api/schema/
```

JSON：

```bash
curl -sS -H 'Accept: application/json' \
  -o docs/vendor/plane/openapi.json \
  http://10.193.0.147:3333/api/schema/
```

建议把下载结果放到：

```text
docs/vendor/plane/openapi.yaml
docs/vendor/plane/openapi.json
```

如果文件较大，也可以不入库，只在本地或 CI artifact 保存。

## 3. 如果不能启用 schema endpoint

如果没有权限修改 Plane 服务环境变量，可以退而求其次：

1. 使用官方在线 API Reference。
2. 使用官方 developer docs 仓库。
3. 手工整理 `PlaneAdapter` 所需的最小 endpoint。

MVP 并不需要复制 Plane 全量 API，只需要这些资源：

| 资源 | 用途 |
| --- | --- |
| Project | 获取项目 |
| Work Item | 创建、查询、更新任务 |
| Work Item States | 状态流转 |
| Work Item Labels | 标签 |
| Work Item Types | 任务类型 |
| Custom Properties | 自定义字段 |
| Work Item Comments | 回写 AI / MR / CI / Eval 结果 |
| Intake | 需求入口 |
| Webhooks | 事件触发 DevFlow |

## 4. 获取 MCP 文档

Plane 官方提供 MCP Server，适合让 Claude Code、Cursor、VSCode、Zed 或其他 coding agent 操作 Plane。

### 4.1 自部署 Plane 的 MCP 配置

官方推荐自部署实例使用 local stdio transport：

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio"],
      "env": {
        "PLANE_API_KEY": "<YOUR_API_KEY>",
        "PLANE_WORKSPACE_SLUG": "<YOUR_WORKSPACE_SLUG>",
        "PLANE_BASE_URL": "http://10.193.0.147:3333/api"
      }
    }
  }
}
```

### 4.2 MCP 文档来源

需要关注这些官方页面：

| 文档 | 用途 |
| --- | --- |
| MCP Server | MCP transport、环境变量、各编辑器配置 |
| For Claude Code | Claude Code 使用 Plane MCP 的配置 |
| Agents | Plane agent 扩展模型 |
| Building an agent | 如何在 Plane 上构建 agent |
| Signals and content payload | Agent / webhook 事件 payload |
| Webhooks | 服务端事件回调和签名校验 |

### 4.3 本地验证命令

先确认工具存在：

```bash
python --version
uvx --version
```

再通过 MCP client 配置启动：

```bash
uvx plane-mcp-server stdio
```

实际运行时需要环境变量：

```env
PLANE_API_KEY=<YOUR_API_KEY>
PLANE_WORKSPACE_SLUG=<YOUR_WORKSPACE_SLUG>
PLANE_BASE_URL=http://10.193.0.147:3333/api
```

## 5. 建议沉淀到项目中的文档结构

推荐：

```text
docs/
  vendor/
    plane/
      openapi.yaml        # 从自部署实例导出的完整 OpenAPI，视大小决定是否入库
      openapi.json        # 可选
      mcp.md              # 整理后的 MCP 使用说明
      endpoints.md        # Agent Platform 实际使用的 endpoint 摘要
```

同时生成：

```text
src/agent_platform/integrations/plane/
  client.py               # REST client
  models.py               # Pydantic models，可从 OpenAPI 生成或手写
  adapter.py              # DevFlow 使用的 PlaneAdapter
  webhook.py              # Webhook signature + event handling
```

## 6. 使用 OpenAPI 生成客户端

拿到 `openapi.json` 后，可以选择：

1. 手写最小 client。
2. 用 OpenAPI generator 生成 Python client。
3. 用 schema 生成 typed models，再手写 adapter。

MVP 建议：

```text
不要一开始生成全量 client。
先手写 PlaneAdapter 所需的 8-10 个 endpoint。
OpenAPI 作为校验和查阅来源。
```

原因：

1. Plane API 很大，生成全量 client 会引入大量无用代码。
2. DevFlow 实际只需要 work item、comment、state、custom property、webhook。
3. 手写 adapter 更容易控制错误处理、幂等和日志。

## 7. 下一步

需要先完成：

1. 在 Plane API 服务开启 `ENABLE_DRF_SPECTACULAR=1`。
2. 重启 Plane API 服务。
3. 再执行：

```bash
curl -sS -o docs/vendor/plane/openapi.yaml \
  http://10.193.0.147:3333/api/schema/
```

4. 导出 JSON：

```bash
curl -sS -H 'Accept: application/json' \
  -o docs/vendor/plane/openapi.json \
  http://10.193.0.147:3333/api/schema/
```

5. 生成或手写 `PlaneAdapter`。
6. 配置 MCP：

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio"],
      "env": {
        "PLANE_API_KEY": "<YOUR_API_KEY>",
        "PLANE_WORKSPACE_SLUG": "<YOUR_WORKSPACE_SLUG>",
        "PLANE_BASE_URL": "http://10.193.0.147:3333/api"
      }
    }
  }
}
```

## 8. 官方参考

- Plane Developer Docs: https://developers.plane.so/
- Plane API Reference: https://developers.plane.so/api-reference/introduction
- Plane OpenAPI Specification: https://developers.plane.so/dev-tools/openapi-specification
- Plane Webhooks: https://developers.plane.so/dev-tools/intro-webhooks
- Plane MCP Server: https://developers.plane.so/dev-tools/mcp-server
