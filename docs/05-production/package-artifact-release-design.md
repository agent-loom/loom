# Agent Package Artifact 与发布设计

> Status: Draft
> Stage: S2
> Owner: platform
> Last verified against code: 2026-05-15

## 1. 背景

当前 `scripts/package_agent.py` 将 `agents/<id>/` 目录直接拷贝到 `dist/agents/<id>/`。没有 tarball、checksum、签名或版本化 artifact 存储。部署通过 REST API 调用，但 deployment 记录不绑定 artifact。回滚时无法保证回到可复现版本。

## 2. Artifact 格式

选择 **tar.gz**。理由：

| 格式 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 目录拷贝 | 简单 | 无完整性校验、不可传输 | 仅开发调试 |
| tar.gz | 标准、可校验、可传输 | 需要打包脚本 | **推荐** |
| wheel | Python 生态标准 | Agent 不是 Python 库 | 不适用 |
| OCI image | 可部署到 K8s | 复杂度过高 | 后续阶段 |

## 3. Artifact 内部结构

```
myj-0.1.0-a3f7c2.tar.gz
└── myj-0.1.0/
    ├── metadata.json        # 构建元数据
    ├── manifest.yaml        # manifest 快照（构建时冻结）
    ├── prompts/
    │   └── *.md
    ├── tools/
    │   └── *.py
    ├── policies/
    │   └── *.yaml
    ├── evals/
    │   ├── cases/
    │   └── eval_config.yaml
    ├── knowledge/
    │   └── *.yaml
    └── tests/
        └── *.py
```

## 4. 构建元数据

`metadata.json` 在打包时自动生成：

```json
{
  "artifact_id": "myj-0.1.0-a3f7c2",
  "agent_id": "myj",
  "version": "0.1.0",
  "manifest_sha256": "sha256:abcdef1234...",
  "package_sha256": "sha256:fedcba4321...",
  "created_by": "gitlab-ci",
  "created_at": "2026-05-15T10:30:00Z",
  "git_commit": "a3f7c2d",
  "git_branch": "feature/myj-update",
  "mr_iid": 42,
  "eval_report_id": "eval_run_001",
  "eval_pass_rate": 0.95,
  "files": ["manifest.yaml", "prompts/system.md", "tools/goods_search.py"]
}
```

## 5. Checksum 计算

```python
import hashlib, tarfile, io, json

def compute_manifest_sha256(manifest_path: str) -> str:
    with open(manifest_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def compute_package_sha256(tar_path: str) -> str:
    with open(tar_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def build_artifact_id(agent_id: str, version: str, git_commit: str) -> str:
    return f"{agent_id}-{version}-{git_commit[:6]}"
```

## 6. 打包流程

```
scripts/package_agent.py --agent myj --version 0.1.0

1. 读取 agents/myj/manifest.yaml，校验通过
2. 计算 manifest_sha256
3. 收集 manifest 引用的所有文件
4. 排除 __pycache__、.pyc、.DS_Store
5. 打包为 dist/myj-0.1.0-<commit>.tar.gz
6. 计算 package_sha256
7. 生成 metadata.json 写入 tar.gz
8. 输出 artifact_id 和 sha256
```

## 7. Artifact 存储

| 阶段 | 存储方式 | 路径 |
|---|---|---|
| MVP | 本地文件系统 | `dist/artifacts/<agent_id>/<artifact_id>.tar.gz` |
| 生产 | S3 / MinIO | `s3://agent-artifacts/<agent_id>/<artifact_id>.tar.gz` |
| 生产 | GitLab Package Registry | 利用现有 GitLab 基础设施 |

## 8. 部署绑定

Deployment 记录必须包含 artifact 信息（与持久化设计对齐）：

```python
class AgentDeployment:
    deployment_id: str
    agent_id: str
    version: str
    artifact_id: str            # 绑定具体 artifact
    manifest_sha256: str        # 可校验 manifest 未被篡改
    environment: str            # dev / staging / prod
    channel: str
    traffic_percent: int
    deployed_at: datetime
    deployed_by: str
```

## 9. 发布 Gate

### 9.1 Staging Gate（自动）

| 检查项 | 来源 | 必须 |
|---|---|---|
| manifest 校验通过 | ManifestLoader | 是 |
| artifact checksum 匹配 | package_agent.py | 是 |
| 单元测试通过 | CI pipeline | 是 |
| Eval pass rate >= 阈值 | EvalRunner | 是 |
| 无 P0 安全规则违反 | PolicyEngine | 是 |

### 9.2 Production Gate（人工 + 自动）

| 检查项 | 来源 | 必须 |
|---|---|---|
| Staging gate 全部通过 | 上游 | 是 |
| Staging 环境运行 >= N 小时 | 部署记录 | 是 |
| MR 已获 approval | GitLab API | 是 |
| 人工审批 | Plane Work Item 状态 | 是 |
| 回滚方案已确认 | 部署 checklist | 是 |

## 10. 回滚流程

```
scripts/deploy_agent.py --agent myj --rollback

1. 查询当前 active deployment 的 previous_deployment_id
2. 获取 previous deployment 的 artifact_id
3. 校验 artifact 存在且 checksum 匹配
4. 创建新 deployment 记录，指向旧 artifact_id
5. 写入 DeploymentAuditEvent（action=rollback）
6. 回写 Plane comment
```

回滚不是"撤销"，而是用历史 artifact 创建新的 deployment。

## 11. CI 集成

```yaml
# .gitlab-ci.yml
package:
  stage: build
  script:
    - python scripts/package_agent.py --agent $AGENT_ID --version $VERSION
  artifacts:
    paths:
      - dist/artifacts/

deploy-staging:
  stage: deploy
  script:
    - python scripts/deploy_agent.py --agent $AGENT_ID --artifact $ARTIFACT_ID --env staging
  when: manual
  needs: [package, test, eval]

deploy-prod:
  stage: deploy
  script:
    - python scripts/deploy_agent.py --agent $AGENT_ID --artifact $ARTIFACT_ID --env prod
  when: manual
  needs: [deploy-staging]
```

## 12. 版本号规范

```
<agent_id>-<semver>-<git_short_sha>
```

- `semver`：`MAJOR.MINOR.PATCH`
- MAJOR：协议不兼容变更
- MINOR：新功能，向后兼容
- PATCH：bug 修复

版本号在 `manifest.yaml` 的 `version` 字段声明，打包时读取。

## 13. 验收标准

1. `scripts/package_agent.py` 生成 tar.gz artifact，包含 `metadata.json` 和 checksum。
2. deploy 操作记录 `artifact_id` 和 `manifest_sha256`。
3. rollback 使用历史 deployment 的 `artifact_id` 创建新 deployment。
4. artifact 文件被篡改后，deploy 时 checksum 校验失败。
5. metadata.json 包含 git commit、MR iid、eval report id。
