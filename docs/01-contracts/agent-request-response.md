# Agent Request / Response 契约

本文档定义平台统一对话接口的请求和响应结构。所有前端、业务系统、网关、Agent Runtime 都应该围绕该契约对齐。

## 1. 设计原则

1. 对前端稳定，不暴露具体 Agent 内部实现。
2. 支持多个业务 Agent 共享同一入口。
3. 支持文本、卡片、命令、debug、trace。
4. 支持多租户、地点（location）、渠道、设备和用户上下文。
5. 支持灰度、回放、评测和审计。

## 2. HTTP 入口

```http
POST /api/v1/agent/chat
```

推荐请求头：

```http
Authorization: Bearer <token>
X-Tenant-ID: <tenant_id>
X-Request-ID: <request_id>
```

## 3. AgentRequest

```json
{
  "protocol_version": "agent-chat/v1",
  "request_id": "req_001",
  "agent_id": "myj",
  "session_id": "sess_001",
  "context": {
    "tenant": {
      "tenant_id": "tenant_myj",
      "org_id": "myj"
    },
    "location": {
      "location_id": "V01031",
      "location_name": "美宜佳测试门店"
    },
    "channel": {
      "channel_id": "store_screen",
      "channel_type": "device"
    },
    "device": {
      "device_id": "device_001",
      "device_type": "pos_screen"
    },
    "user": {
      "user_id": "anonymous",
      "member_id": null
    },
    "locale": "en",
    "timezone": "UTC"
  },
  "input": {
    "type": "text",
    "query": "帮我推荐一瓶低糖饮料",
    "messages": [],
    "attachments": [],
    "capabilities": [
      "text",
      "cards",
      "product.recommend",
      "product.locate",
      "cart.add"
    ]
  },
  "options": {
    "stream": false,
    "debug": false,
    "max_latency_ms": 5000,
    "runtime_profile": "prod"
  },
  "metadata": {
    "source": "frontend",
    "traceparent": null
  }
}
```

## 4. 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `protocol_version` | 是 | 当前固定 `agent-chat/v1` |
| `request_id` | 否 | 调用方传入；为空则平台生成 |
| `agent_id` | 否 | 显式指定 Agent；为空则走路由 |
| `session_id` | 否 | 多轮会话 ID；为空则平台生成 |
| `context.tenant` | 是 | 租户和组织（organization）识别。`org_id` 为组织标识（向后兼容别名 `retailer_id`） |
| `context.location` | 否 | 地点上下文（向后兼容别名 `store`）。`location_id`（别名 `store_id`）、`location_name`（别名 `store_name`） |
| `context.channel` | 否 | 调用渠道 |
| `context.device` | 否 | 设备上下文 |
| `context.user` | 否 | 用户或会员上下文 |
| `input.query` | 是 | 当前用户输入 |
| `input.messages` | 否 | 历史消息，优先使用平台 session |
| `input.capabilities` | 否 | 前端支持的输出能力 |
| `options.stream` | 否 | 是否流式返回 |
| `options.debug` | 否 | 是否返回 debug 信息 |
| `metadata` | 否 | 调用来源、trace 等扩展信息 |

请求头处理：

| Header | 当前语义 |
| --- | --- |
| `X-Request-ID` | 当 body `request_id` 为空时，平台使用该 header；响应会回写 `X-Request-ID` |
| `X-Tenant-ID` | 当 body `context.tenant.tenant_id` 为空时，平台写入该字段 |

## 5. AgentResponse

```json
{
  "protocol_version": "agent-chat/v1",
  "request_id": "req_001",
  "session_id": "sess_001",
  "agent": {
    "agent_id": "myj",
    "agent_version": "0.1.0",
    "deployment_id": "dep_myj_dev"
  },
  "output": {
    "status": "completed",
    "text": {
      "display": "推荐元气森林白桃味，低糖，冷藏柜第二层有售。",
      "tts": "推荐元气森林白桃味，低糖，冷藏柜第二层有售。"
    },
    "cards": [
      {
        "type": "product",
        "id": "SKU_10001",
        "title": "元气森林白桃味",
        "subtitle": "低糖饮料",
        "data": {
          "sku_id": "SKU_10001",
          "price": 5.5,
          "stock": 12
        }
      }
    ],
    "commands": [
      {
        "name": "product.locate",
        "data": {
          "sku_id": "SKU_10001",
          "area": "冷藏柜",
          "shelf": "第二层"
        }
      }
    ]
  },
  "debug": {
    "route": "myj.recommendation_worker",
    "tools": [
      "goods_search",
      "goods_location"
    ],
    "latency_ms": 920
  },
  "trace": {
    "run_id": "run_001",
    "route_reason": "agent_id",
    "traffic_bucket": 37,
    "model": "default-chat",
    "tool_calls": [
      {
        "tool_name": "goods_search",
        "latency_ms": 120,
        "status": "success"
      }
    ]
  },
  "error": null
}
```

### 5.1 Trace 字段

| 字段 | 说明 |
| --- | --- |
| `run_id` | 平台运行 ID |
| `route_reason` | 路由命中原因，例如 `agent_id`、`tenant.org_id`、`semantic:*` |
| `traffic_bucket` | 灰度路由 bucket；未进入灰度判断时为空 |
| `model` | 运行时使用的模型标识 |
| `tool_calls` | 工具调用摘要 |
| `latency_ms` | 平台侧运行耗时 |
| `error` | trace 级错误码 |

## 6. 输出状态

| 状态 | 说明 |
| --- | --- |
| `completed` | 正常完成 |
| `clarification_required` | 需要用户补充信息 |
| `handoff_required` | 需要转人工或业务系统 |
| `rejected` | 安全或权限拒绝 |
| `failed` | 平台或工具执行失败 |

## 7. Error 结构

```json
{
  "code": "AGENT_NOT_FOUND",
  "message": "Agent not found: promo",
  "details": {
    "agent_id": "promo"
  },
  "retryable": false
}
```

错误码建议：

| code | HTTP | 说明 |
| --- | --- | --- |
| `INVALID_REQUEST` | 400 | 请求结构错误 |
| `UNAUTHORIZED` | 401 | 未鉴权 |
| `FORBIDDEN` | 403 | 无权限 |
| `AGENT_NOT_FOUND` | 404 | Agent 不存在 |
| `MANIFEST_INVALID` | 422 | Manifest 无效 |
| `TOOL_FORBIDDEN` | 403 | 工具无权限 |
| `TOOL_TIMEOUT` | 504 | 工具超时 |
| `RUNTIME_FAILED` | 500 | Runtime 执行失败 |
| `MODEL_FAILED` | 502 | 模型调用失败 |

## 8. Stream 事件

当 `options.stream=true` 时，建议使用 SSE。

事件类型：

```text
run.started
message.delta
tool.started
tool.completed
message.completed
run.completed
run.failed
```

示例：

```text
event: message.delta
data: {"request_id":"req_001","delta":"推荐"}
```

## 9. 兼容策略

1. 新增字段必须向后兼容。
2. 删除字段必须升级 `protocol_version`。
3. 前端能力通过 `input.capabilities` 声明，Agent 不能返回前端不支持的 command。
4. `trace` 默认返回最小可观测字段；`debug` 默认只在 debug 或内部调用时返回。
5. 生产环境错误信息不能泄露密钥、SQL、内部栈。
