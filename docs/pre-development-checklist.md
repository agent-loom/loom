# 正式开工检查清单

本文档是项目正式编码前的基线检查清单。当前项目已经进入实现阶段，后续推进时应优先参考 `docs/implementation-gap.md`；本清单保留为“开工前应确认事项”和治理项回溯。

## 1. P0 必须完成

### 1.1 项目边界

- [ ] MVP 范围已确认。
- [ ] 明确第一阶段不做完整 Web 管理后台。
- [ ] 明确第一阶段不自动生产全量发布。
- [ ] 明确第一阶段不深 fork Hermes。
- [ ] 明确第一个业务 Agent 是 `myj` demo package。

### 1.2 核心契约

- [ ] `AgentRequest / AgentResponse` 已确认。
- [ ] `AgentManifest v1` 已确认。
- [ ] `DevFlow Task Pack` 已确认。
- [ ] 错误码和输出状态已确认。
- [ ] Manifest 校验规则已确认。

### 1.3 技术基线

- [ ] 后端技术栈已确认。
- [ ] 数据库选择已确认。
- [ ] 测试框架已确认。
- [ ] CI 工具已确认。
- [ ] RuntimeBackend 插件边界已确认。

### 1.4 工程仓库

- [ ] 初始化 git 仓库。
- [ ] 创建 `README.md`。
- [ ] 创建 `pyproject.toml`。
- [ ] 创建 `.gitignore`。
- [ ] 创建 `.env.example`。
- [ ] 创建基础目录结构。

### 1.5 GitLab / Plane

- [ ] 创建 GitLab 项目。
- [ ] 设置默认分支。
- [ ] 设置 protected branch。
- [ ] 设置 MR approval 规则。
- [ ] 设置 GitLab CI runner。
- [ ] 设置必要 token 和权限。
- [ ] Plane `Agent Platform` Project 已创建。
- [ ] Plane states / labels / custom properties 已配置。
- [ ] Plane API Key 已生成并放入 secret。
- [ ] Plane webhook secret 已生成。

## 2. P1 强烈建议完成

### 2.1 开发规范

- [ ] Python formatter / linter 已确定。
- [ ] import 排序规则已确定。
- [ ] commit message 规范已确定。
- [ ] branch 命名规范已确定。
- [ ] MR 模板已确定。
- [ ] Issue 模板已确定。

### 2.2 测试策略

- [ ] 单元测试目录已确定。
- [ ] 集成测试目录已确定。
- [ ] 契约测试目录已确定。
- [ ] Eval case 格式已确定。
- [ ] CI 最小门禁已确定。

### 2.3 安全与配置

- [ ] 密钥只进入 `.env` 或 secret manager。
- [ ] `.env` 不入库。
- [ ] manifest 禁止包含密钥。
- [ ] 工具权限策略已定义。
- [ ] 生产发布必须人工审批。

## 3. P2 可后置

- [ ] Plane MCP 配置。
- [ ] Plane OpenAPI 快照更新流程。
- [ ] Langfuse 接入。
- [ ] Hermes Adapter 设计。
- [ ] Web 管理后台设计。
- [ ] Shadow traffic 方案。
- [ ] Agent marketplace 方案。

## 4. 开工前建议目录

```text
agent-platform/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docs/
│   ├── README.md
│   ├── mvp.md
│   ├── agent-platform-design.md
│   ├── ai-human-vibecoding-rd-platform.md
│   ├── gitlab.md
│   ├── devflow-task-pack.md
│   ├── contracts/
│   │   ├── agent-request-response.md
│   │   └── agent-manifest-v1.md
│   └── adr/
│       └── 0001-architecture-baseline.md
├── src/
│   └── agent_platform/
├── agents/
│   └── myj/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
└── scripts/
```

## 5. 第一批开发任务建议

正式开工后，建议第一批任务拆成：

1. `project:init` 初始化 Python 项目骨架。
2. `contracts:models` 定义 Pydantic domain models。
3. `manifest:loader` 实现 manifest loader 和校验。
4. `registry:local` 实现本地 Agent Registry。
5. `router:basic` 实现基础 Agent Router。
6. `runtime:native` 实现 NativeRuntimeBackend。
7. `api:chat` 实现 `/api/v1/agent/chat`。
8. `agent:myj-demo` 增加 `myj` demo package。
9. `eval:runner` 实现 eval runner。
10. `ci:baseline` 增加 GitLab CI baseline。

## 6. 不应开工的信号

如果出现以下情况，不建议进入编码：

1. MVP 边界仍在频繁变化。
2. `AgentRequest / AgentResponse` 未确认。
3. Manifest 字段没有统一。
4. 不知道第一个验收 Agent 是什么。
5. GitLab / CI 决策未确认。
6. 想同时做生产平台、研发平台、Web 后台、Hermes fork。
7. 没有明确人工 review 和发布审批人。

## 7. 开工判断

可以正式开工的最低条件：

1. `docs/mvp.md` 已确认。
2. `docs/contracts/agent-request-response.md` 已确认。
3. `docs/contracts/agent-manifest-v1.md` 已确认。
4. `docs/devflow-task-pack.md` 已确认。
5. Git 仓库已初始化。
6. 第一批开发任务已拆分。
