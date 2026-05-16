# Agent Package Artifact 与发布设计

> Status: Partially Implemented
> Stage: S2
> Owner: platform
> Last verified against code: 2026-05-16

> 当前实现校准：`ArtifactStoreProtocol`、`InMemoryArtifactStore`、`LocalArtifactStore` 和 manifest hash 相关代码已出现在工作树中，单元测试通过；但当前 ruff 与 manifest validate 质量门禁未通过，不能标记为生产发布闭环完成。S3/GitLab Registry 等远程后端仍未实现。

## 1. 背景与问题

当前 `scripts/package_agent.py` 将 `agents/<id>/` 目录树复制到 `dist/agents/<id>/`，产物是一个普通目录。`deploy_agent.py` 通过 REST 调用部署，`promote_agent.py` 做 canary 灰度。整个流程存在以下问题：

1. **无不可变产物**：产物是目录拷贝，无法保证 staging 和 prod 部署的是同一份文件。
2. **无校验**：没有 checksum，无法验证传输或存储过程中产物是否被篡改。
3. **无 manifest snapshot 绑定**：deployment 记录只有 `agent_id + version`，无法精确回溯到哪一份 manifest 和文件集合。
4. **无版本化存储**：`dist/` 目录是临时 CI artifact，没有长期可寻址的 artifact store。
5. **rollback 依赖内存 audit log**：`DeploymentAuditLog` 是内存态（见 `src/agent_platform/registry/deployment.py`），服务重启后丢失，无法作为生产回滚依据。

本文档回答 `next-stage-design-plan.md` P0-2 提出的六个问题，并给出可直接实现的设计方案。

### 1.1 当前代码现状

| 文件 | 当前行为 | 问题 |
| --- | --- | --- |
| `scripts/package_agent.py` | `shutil.copy2` 逐文件拷贝到 `dist/agents/<id>/` | 无 tarball、无 checksum、无 metadata |
| `scripts/deploy_agent.py` | `POST /api/v1/agent-packages/{agent_id}/versions/{version}/deploy` | 只传 `agent_id + version + channel`，不传 artifact 标识 |
| `scripts/promote_agent.py` | 同上，增加 `traffic_percent` | 同上 |
| `src/.../deployment.py` | `DeploymentAuditLog` 内存列表 | 重启丢失，`_rollback_targets` 只记录 version 不记录 artifact |
| `.gitlab-ci.yml` package 阶段 | `dist/` 作为 CI artifact，保留 7 天 | 无法长期寻址，无 checksum |

## 2. 设计决策

### 2.1 Artifact 格式：tar.gz

| 选项 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| 目录拷贝 | 简单 | 无完整性校验、不可传输、不可寻址 | 仅开发调试用 |
| tar.gz | 不可变、可校验、可传输、标准工具链支持 | 需要解压步骤 | **推荐** |
| wheel | Python 生态标准 | agent package 不是纯 Python 库，包含 yaml/md 等非代码资产 | 不适用 |
| OCI image | 可部署到 K8s | 当前阶段复杂度过高 | 后续阶段考虑 |

决策：使用 **tar.gz** 作为 artifact 格式。

### 2.2 Artifact 命名规则

```
<agent_id>-<version>-<sha256_prefix_8>.tar.gz
```

示例：

```
myj-0.1.0-a3b2c1d4.tar.gz
promo_recommendation-0.2.1-e5f6a7b8.tar.gz
```

其中 `sha256_prefix_8` 是 tar.gz 文件 SHA256 摘要的前 8 位十六进制字符。这个前缀用于快速区分同一 `agent_id + version` 的不同构建（例如 prompt 文件改动但未升版本号的情况）。完整 SHA256 记录在伴生 `.sha256` 文件和 `metadata.json` 中。

### 2.3 版本号约定

沿用 `agent-manifest-v1.md` 中的 SemVer 规则。版本号取自 `manifest.yaml` 的 `version.package_version` 字段，不由 CI 变量覆盖：

| 变更类型 | 版本位 | 示例 |
| --- | --- | --- |
| prompt 措辞微调、eval case 增加 | patch | 0.1.0 -> 0.1.1 |
| 工具参数兼容新增、输出能力兼容新增 | minor | 0.1.1 -> 0.2.0 |
| 删除字段、runtime backend 切换、breaking change | major | 0.2.0 -> 1.0.0 |

## 3. Artifact 内部结构

tar.gz 解压后的目录布局（以 `myj` agent 为例）：

```
myj-0.1.0/
  metadata.json          # 构建元数据（见 3.1）
  manifest.yaml          # manifest 快照（构建时冻结的精确拷贝）
  adapter.py             # runtime entrypoint
  __init__.py
  prompts/               # prompt 文件
    orchestrator.md
    direct_reply.md
    reply_style.md
  tools/                 # package-local 工具代码
    __init__.py
    goods_search.py
    goods_location.py
    promotion_lookup.py
    store_consult.py
  policies/              # routing、safety、output 策略文件
    routing.yaml
    safety.yaml
    output.yaml
  evals/                 # eval suite 定义
    golden.yaml
    intent_cases.yaml
  knowledge/             # knowledge source 配置（不含数据本身）
    sources.yaml
```

排除项（不打入 tar.gz）：

- `__pycache__/`
- `*.pyc`
- `tests/`（agent 内部测试不进入生产产物，tests 在 CI 阶段已执行完毕）
- `.DS_Store`

### 3.1 metadata.json

```json
{
  "artifact_id": "myj-0.1.0-a3b2c1d4",
  "agent_id": "myj",
  "version": "0.1.0",
  "manifest_sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "created_at": "2026-05-15T10:30:00Z",
  "created_by": "gitlab-ci",
  "git_commit": "8c33ccd1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7",
  "git_ref": "main",
  "mr_iid": 42,
  "eval_report_id": "eval-myj-0.1.0-20260515T103000",
  "platform_version": "0.1.0",
  "files": [
    "metadata.json",
    "manifest.yaml",
    "adapter.py",
    "__init__.py",
    "prompts/orchestrator.md",
    "prompts/direct_reply.md",
    "prompts/reply_style.md",
    "tools/__init__.py",
    "tools/goods_search.py",
    "tools/goods_location.py",
    "tools/promotion_lookup.py",
    "tools/store_consult.py",
    "policies/routing.yaml",
    "policies/safety.yaml",
    "policies/output.yaml",
    "evals/golden.yaml",
    "evals/intent_cases.yaml",
    "knowledge/sources.yaml"
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `artifact_id` | 是 | `<agent_id>-<version>-<sha256_prefix_8>`，全局唯一标识 |
| `agent_id` | 是 | 来自 `manifest.yaml` 的 `metadata.id` |
| `version` | 是 | 来自 `manifest.yaml` 的 `version.package_version` |
| `manifest_sha256` | 是 | `manifest.yaml` 文件的 SHA256 |
| `created_at` | 是 | ISO 8601 UTC 时间 |
| `created_by` | 是 | 构建者标识，CI 中为 `gitlab-ci`，本地为 `local` |
| `git_commit` | 是 | 构建时的 git commit SHA（完整 40 字符） |
| `git_ref` | 否 | 构建时的 git ref（branch 或 tag） |
| `mr_iid` | 否 | 触发构建的 Merge Request IID |
| `eval_report_id` | 否 | 关联的 eval report 标识；打包阶段可能尚未执行 eval，deploy 阶段回填 |
| `platform_version` | 否 | 构建时的平台版本，用于 `runtime_compat` 校验 |
| `files` | 是 | artifact 内所有文件的相对路径列表（用于完整性校验） |

注意：`package_sha256`（tar.gz 文件自身的 SHA256）不放在 `metadata.json` 内部，因为 `metadata.json` 本身要包含在 tar.gz 中，存在循环依赖。`package_sha256` 记录在伴生 `.sha256` 文件中（见 3.2）。

### 3.2 伴生 .sha256 文件

每个 tar.gz 产物附带一个同名 `.sha256` 文件，格式与 `sha256sum` 工具兼容：

```
a3b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2  myj-0.1.0-a3b2c1d4.tar.gz
```

校验方式：

```bash
cd dist/agents/
sha256sum -c myj-0.1.0-a3b2c1d4.sha256
```

### 3.3 Checksum 计算方法

manifest_sha256 计算：

```python
import hashlib
from pathlib import Path

def manifest_sha256(manifest_path: Path) -> str:
    content = manifest_path.read_bytes()
    return "sha256:" + hashlib.sha256(content).hexdigest()
```

package_sha256 计算：

```python
def file_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()
```

artifact_id 中 sha256 前缀的来源：

```python
def build_artifact_id(agent_id: str, version: str, package_sha256: str) -> str:
    sha_prefix = package_sha256.removeprefix("sha256:")[:8]
    return f"{agent_id}-{version}-{sha_prefix}"
```

## 4. 构建流程

### 4.1 改造 scripts/package_agent.py

当前 `package_agent()` 函数只做目录拷贝。改造后的核心逻辑：

```python
def package_agent(agent_id: str, output_dir: Path) -> ArtifactResult:
    """构建 agent artifact tar.gz 并生成 metadata。"""
    source = Path("agents") / agent_id
    manifest_path = source / "manifest.yaml"

    # 1. 校验 manifest
    spec = ManifestLoader().load_file(manifest_path)
    version = spec.version

    # 2. 计算 manifest SHA256
    m_sha256 = manifest_sha256(manifest_path)

    # 3. 收集文件（排除 __pycache__、*.pyc、tests/、.DS_Store）
    files = collect_artifact_files(source)

    # 4. 构建 metadata.json（package_sha256 暂未知）
    metadata = build_metadata(
        agent_id=agent_id,
        version=version,
        manifest_sha256=m_sha256,
        git_commit=get_git_commit(),
        git_ref=get_git_ref(),
        mr_iid=get_mr_iid(),
        files=[str(f.relative_to(source)) for f in files],
    )

    # 5. 创建 tar.gz（含 metadata.json）
    prefix = f"{agent_id}-{version}"
    temp_tarball = output_dir / f"{prefix}.tar.gz"
    create_tarball(source, files, metadata, temp_tarball, prefix=prefix)

    # 6. 计算 package SHA256
    p_sha256 = file_sha256(temp_tarball)

    # 7. 用 SHA256 前缀生成 artifact_id 并重命名
    sha_prefix = p_sha256.removeprefix("sha256:")[:8]
    artifact_id = f"{agent_id}-{version}-{sha_prefix}"
    final_name = f"{artifact_id}.tar.gz"
    final_path = output_dir / final_name
    temp_tarball.rename(final_path)

    # 8. 写伴生 .sha256 文件
    sha_file = output_dir / f"{artifact_id}.sha256"
    raw_hex = p_sha256.removeprefix("sha256:")
    sha_file.write_text(f"{raw_hex}  {final_name}\n")

    # 9. 写外部 metadata 副本（便于 CI 读取，不用解压 tar.gz）
    meta_file = output_dir / f"{artifact_id}.meta.json"
    meta_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

    return ArtifactResult(
        artifact_id=artifact_id,
        path=final_path,
        sha256_path=sha_file,
        meta_path=meta_file,
        manifest_sha256=m_sha256,
        package_sha256=p_sha256,
    )
```

文件收集规则：

```python
EXCLUDE_DIRS = {"__pycache__", "tests", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_NAMES = {".DS_Store", ".gitkeep"}

def collect_artifact_files(source: Path) -> list[Path]:
    files = []
    for item in source.rglob("*"):
        if item.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in item.relative_to(source).parts):
            continue
        if item.suffix in EXCLUDE_SUFFIXES:
            continue
        if item.name in EXCLUDE_NAMES:
            continue
        files.append(item)
    return sorted(files)
```

### 4.2 ArtifactResult 数据结构

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class ArtifactResult:
    artifact_id: str         # myj-0.1.0-a3b2c1d4
    path: Path               # dist/agents/myj-0.1.0-a3b2c1d4.tar.gz
    sha256_path: Path        # dist/agents/myj-0.1.0-a3b2c1d4.sha256
    meta_path: Path          # dist/agents/myj-0.1.0-a3b2c1d4.meta.json
    manifest_sha256: str     # sha256:e3b0c44...
    package_sha256: str      # sha256:a3b2c1d4...
```

### 4.3 CLI 接口

保持与当前命令行兼容，增加 `--verify` 子命令：

```bash
# 打包指定 agent（从 manifest.yaml 读取 version）
python scripts/package_agent.py --agent myj

# 只打包 git 变更的 agent（CI 模式）
python scripts/package_agent.py --agent changed

# 自定义输出目录
python scripts/package_agent.py --agent myj --output dist/agents

# 校验已有 artifact 完整性
python scripts/package_agent.py --verify dist/agents/myj-0.1.0-a3b2c1d4.tar.gz
```

`--verify` 流程：

1. 读取伴生 `.sha256` 文件。
2. 重新计算 tar.gz 的 SHA256，对比是否一致。
3. 解压 tar.gz，读取内部 `metadata.json`，取出 `manifest_sha256`。
4. 重新计算 tar.gz 内 `manifest.yaml` 的 SHA256，对比是否一致。
5. 全部通过返回 exit code 0，任一失败返回 exit code 1。

### 4.4 输出示例

```
$ python scripts/package_agent.py --agent myj
Packaging myj...
  manifest: agents/myj/manifest.yaml (sha256:e3b0c44...)
  version:  0.1.0
  files:    18
  artifact: dist/agents/myj-0.1.0-a3b2c1d4.tar.gz
  sha256:   sha256:a3b2c1d4e5f6a7b8...
  artifact_id: myj-0.1.0-a3b2c1d4
Packaged 1 agent(s) to dist/agents
```

## 5. Artifact 存储

### 5.1 阶段一：本地文件系统 + GitLab CI Artifact

产物输出结构：

```
dist/
  agents/
    myj-0.1.0-a3b2c1d4.tar.gz       # 不可变产物
    myj-0.1.0-a3b2c1d4.sha256        # 伴生校验文件
    myj-0.1.0-a3b2c1d4.meta.json     # 外部 metadata 副本
```

GitLab CI 的 `artifacts.paths` 已配置为 `dist/`，因此 tar.gz 会作为 CI artifact 保存。建议将 `expire_in` 从当前的 7 天延长到 30 天，以覆盖生产回滚窗口。

本地开发时，`dist/` 不纳入 git（已在 `.gitignore`）。

### 5.2 阶段二：ArtifactStore 接口

当需要跨环境（staging/prod）共享 artifact 或需要长期保留时，引入 `ArtifactStore` 抽象：

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

class ArtifactStore(ABC):
    """Agent artifact 存储接口。"""

    @abstractmethod
    def upload(self, artifact_id: str, tarball: Path, sha256_file: Path) -> str:
        """上传 artifact，返回可寻址 URI。"""
        ...

    @abstractmethod
    def download(self, artifact_id: str, dest_dir: Path) -> Path:
        """下载 artifact 到本地目录，返回 tar.gz 路径。"""
        ...

    @abstractmethod
    def exists(self, artifact_id: str) -> bool:
        """检查 artifact 是否存在。"""
        ...

    @abstractmethod
    def get_metadata(self, artifact_id: str) -> dict[str, Any]:
        """获取 artifact 的 metadata.json 内容（无需下载完整 tar.gz）。"""
        ...

    @abstractmethod
    def list_versions(self, agent_id: str) -> list[str]:
        """列出指定 agent 的所有 artifact_id。"""
        ...
```

实现：

| 实现 | 适用场景 | 依赖 |
| --- | --- | --- |
| `LocalArtifactStore` | 本地开发、单机测试 | 无 |
| `S3ArtifactStore` | 生产环境、多实例部署 | boto3 / MinIO client |
| `GitLabPackageRegistryStore` | 使用 GitLab 基础设施 | GitLab API |

远程存储路径规范：

```
s3://agent-artifacts/<agent_id>/<version>/<artifact_id>.tar.gz
s3://agent-artifacts/<agent_id>/<version>/<artifact_id>.sha256
s3://agent-artifacts/<agent_id>/<version>/<artifact_id>.meta.json
```

阶段一不需要实现远程 store，但 `package_agent.py` 的输出结构已与远程 store 的路径规范兼容。

## 6. Deployment 记录与 Artifact 绑定

### 6.1 改造 deploy_agent.py

当前 `deploy_agent.py` 只发送 `agent_id + version + channel`。改造后增加 artifact 绑定参数：

```python
def deploy_agent(
    base_url: str,
    agent_id: str,
    version: str,
    env: str,
    artifact_id: str,
    manifest_sha256: str,
    package_sha256: str,
    eval_report_id: str | None = None,
) -> dict:
    url = f"{base_url}/api/v1/agent-packages/{agent_id}/versions/{version}/deploy"
    payload = {
        "channel": env,
        "artifact_id": artifact_id,
        "manifest_sha256": manifest_sha256,
        "package_sha256": package_sha256,
        "eval_report_id": eval_report_id,
    }
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()
```

CI 中从 package 阶段的输出读取这些值：

```bash
# deploy_staging job 中
ARTIFACT_META=$(cat dist/agents/*.meta.json)
ARTIFACT_ID=$(echo "$ARTIFACT_META" | jq -r '.artifact_id')
MANIFEST_SHA=$(echo "$ARTIFACT_META" | jq -r '.manifest_sha256')
PKG_SHA=$(cat dist/agents/*.sha256 | awk '{print "sha256:"$1}')

python scripts/deploy_agent.py \
  --agent "$AGENT_ID" \
  --env staging \
  --artifact-id "$ARTIFACT_ID" \
  --manifest-sha256 "$MANIFEST_SHA" \
  --package-sha256 "$PKG_SHA"
```

### 6.2 改造 promote_agent.py

`promote_agent.py` 做 canary 灰度时，必须传递 artifact_id，保证灰度（traffic=5）和全量（traffic=100）部署的是同一份产物：

```python
def promote_agent(
    base_url: str,
    agent_id: str,
    version: str,
    traffic: int,
    artifact_id: str,
    manifest_sha256: str,
    package_sha256: str,
) -> dict:
    url = f"{base_url}/api/v1/agent-packages/{agent_id}/versions/{version}/deploy"
    payload = {
        "channel": "prod",
        "traffic_percent": traffic,
        "artifact_id": artifact_id,
        "manifest_sha256": manifest_sha256,
        "package_sha256": package_sha256,
    }
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()
```

### 6.3 DeploymentEvent 扩展

改造当前 `src/agent_platform/registry/deployment.py` 中的 `DeploymentEvent`，增加 artifact 绑定字段：

```python
class DeploymentEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str               # deploy | rollback | promote
    agent_id: str
    version: str
    channel: str
    traffic_percent: int = 100
    status: AgentDeploymentStatus

    # --- 新增 artifact 绑定 ---
    artifact_id: str = ""                    # myj-0.1.0-a3b2c1d4
    manifest_sha256: str = ""                # sha256:e3b0c44...
    package_sha256: str = ""                 # sha256:a3b2c1d4...
    eval_report_id: str | None = None

    previous_version: str | None = None
    previous_artifact_id: str | None = None  # 新增：rollback 溯源
    actor: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)
```

新增字段默认值为空字符串，保证与当前代码的已有调用兼容。

### 6.4 Deployment DB Schema（与持久化设计对齐）

`persistence-storage-design.md`（同阶段 S2 设计）定义了 `AgentDeployment` 和 `DeploymentAuditEvent` 的 DB schema。本文档的 artifact 绑定字段应同步写入这些表：

```sql
-- 表 agent_deployments
CREATE TABLE agent_deployments (
    id                UUID PRIMARY KEY,
    agent_id          VARCHAR NOT NULL,
    version           VARCHAR NOT NULL,
    channel           VARCHAR NOT NULL,       -- staging | prod
    artifact_id       VARCHAR NOT NULL,       -- 新增
    manifest_sha256   VARCHAR NOT NULL,       -- 新增
    package_sha256    VARCHAR NOT NULL,       -- 新增
    eval_report_id    VARCHAR,               -- 新增
    traffic_percent   INT DEFAULT 100,
    status            VARCHAR NOT NULL,
    actor             VARCHAR NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 表 deployment_audit_events
CREATE TABLE deployment_audit_events (
    id                   UUID PRIMARY KEY,
    deployment_id        UUID REFERENCES agent_deployments(id),
    event_type           VARCHAR NOT NULL,   -- deploy | rollback | promote
    artifact_id          VARCHAR NOT NULL,   -- 新增
    manifest_sha256      VARCHAR NOT NULL,   -- 新增
    previous_artifact_id VARCHAR,            -- 新增
    actor                VARCHAR NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata             JSONB
);

-- 索引：按 agent_id + channel 查询最新部署
CREATE INDEX idx_deployments_agent_channel ON agent_deployments(agent_id, channel, created_at DESC);

-- 索引：按 artifact_id 查询部署历史
CREATE INDEX idx_deployments_artifact ON agent_deployments(artifact_id);
```

在持久化设计尚未落地前，内存态的 `DeploymentAuditLog` 同样要记录 artifact_id 和 manifest_sha256。

## 7. 发布 Gate 检查

### 7.1 Gate 检查清单

deploy 到 staging 或 prod 前，平台 API 必须依次执行以下检查：

| 序号 | 检查项 | staging | prod | 检查方式 |
| --- | --- | --- | --- | --- |
| 1 | artifact 存在 | 必须 | 必须 | artifact store 查询或本地文件存在性检查 |
| 2 | package_sha256 校验通过 | 必须 | 必须 | 重新计算 tar.gz SHA256，与传入值对比 |
| 3 | manifest_sha256 校验通过 | 必须 | 必须 | 解压 tar.gz 中 manifest.yaml，重新计算 SHA256 |
| 4 | manifest 合法性校验通过 | 必须 | 必须 | `ManifestLoader().load_file()` |
| 5 | runtime_compat 兼容当前平台 | 必须 | 必须 | SemVer range 校验 |
| 6 | eval gate 通过 | 必须 | 必须 | `EvalRunner` 服务端执行，不信任客户端传入 |
| 7 | eval pass_rate >= required_pass_rate | 必须 | 必须 | 来自 manifest `evals.required_pass_rate` |
| 8 | MR 已 approve | 可选 | 必须 | GitLab API 查询 MR approval 状态 |
| 9 | 人工签收 | 可选 | 必须 | GitLab CI `when: manual` 或平台 API sign-off |
| 10 | staging 运行 >= N 小时 | 不适用 | 推荐 | 查询 staging deployment 的 created_at |

### 7.2 Gate 检查结果结构

```python
@dataclass
class GateCheck:
    name: str            # 检查项名称
    passed: bool         # 是否通过
    required: bool       # 是否必须通过
    detail: str          # 详情或错误原因

@dataclass
class ReleaseGateResult:
    passed: bool                # 全部必须项是否通过
    checks: list[GateCheck]     # 每项检查的详细结果
    eval_report_id: str | None  # eval report 标识（如果执行了 eval）
```

deploy API 返回 `ReleaseGateResult`，使 CI 和人工审批者能看到每项检查的结果。

### 7.3 Gate 检查流程

```
deploy API 收到请求
  |
  +-> 1. 验证 artifact_id 对应的产物存在
  +-> 2. 验证 package_sha256 与实际文件一致
  +-> 3. 解压 tar.gz 中的 manifest.yaml
  +-> 4. 验证 manifest_sha256 一致
  +-> 5. ManifestLoader 校验 manifest 合法性
  +-> 6. 验证 runtime_compat 与当前平台兼容
  +-> 7. 执行 EvalRunner（服务端执行，不信任客户端传入的 eval_passed）
  +-> 8. 验证 pass_rate >= required_pass_rate
  +-> 9. [prod only] 查询 GitLab API 验证 MR approval
  +-> 10. [prod only] 验证 human sign-off
  |
  +-> 全部必须项通过:
  |     -> 创建 AgentDeployment 记录
  |     -> 记录 DeploymentAuditEvent
  |     -> 返回 ReleaseGateResult(passed=true)
  |
  +-> 任一必须项失败:
        -> 不创建 deployment
        -> 返回 ReleaseGateResult(passed=false) + 失败详情
```

## 8. Rollback 机制

### 8.1 设计原则

rollback 不是"撤销变更"，而是 **重新部署历史 artifact_id 对应的产物**。这要求：

1. 每次 deployment 必须记录 `artifact_id`、`manifest_sha256`、`package_sha256`。
2. artifact store 中的历史产物不能被删除（至少在保留期内）。
3. rollback 走与正常 deploy 相同的代码路径，只是 artifact_id 指向历史版本。

### 8.2 Rollback 流程

```
1. 查询目标 agent_id + channel 的历史 deployment 记录
2. 获取上一个成功部署的 artifact_id（或使用指定的 target_artifact_id）
3. 从 artifact store 获取该 artifact
4. 校验 package_sha256 一致
5. 执行 deploy（跳过 eval gate，因为历史版本已通过 eval）
6. 记录 rollback event：
   - event_type = "rollback"
   - artifact_id = 目标 artifact_id
   - previous_artifact_id = 当前 artifact_id
7. 标记当前 deployment 状态为 ROLLED_BACK
```

### 8.3 改造 rollback API

当前 `POST /api/v1/deployments/rollback` 改造：

```python
# 请求
{
    "agent_id": "myj",
    "channel": "prod",
    "target_artifact_id": "myj-0.1.0-a3b2c1d4"  # 可选；不填则回退到上一个成功版本
}

# 响应
{
    "deployment_id": "uuid-...",
    "agent_id": "myj",
    "channel": "prod",
    "from_artifact_id": "myj-0.2.0-x9y8z7w6",
    "to_artifact_id": "myj-0.1.0-a3b2c1d4",
    "status": "rolled_back",
    "gate_result": {
        "passed": true,
        "checks": [
            {"name": "artifact_exists", "passed": true, "required": true, "detail": "ok"},
            {"name": "package_sha256", "passed": true, "required": true, "detail": "ok"}
        ]
    }
}
```

### 8.4 Rollback 与 eval gate 的关系

rollback 到历史 artifact **不再重新执行 eval**，原因：

1. 该 artifact 在首次 deploy 时已通过 eval gate。
2. 生产紧急回滚不应被 eval 阻塞。
3. rollback event 记录原始 deploy 的 eval_report_id，可追溯。

如果需要对历史 artifact 重新评估（例如 eval case 更新后），可通过 `scripts/run_agent_eval.py` 单独触发，不阻塞 rollback 流程。

### 8.5 Rollback 对 DeploymentAuditLog 的改造

```python
def record_rollback(
    self,
    agent_id: str,
    channel: str,
    from_version: str,
    to_version: str,
    from_artifact_id: str,      # 新增
    to_artifact_id: str,        # 新增
    to_manifest_sha256: str,    # 新增
    actor: str = "system",
) -> DeploymentEvent:
    event = DeploymentEvent(
        event_type="rollback",
        agent_id=agent_id,
        version=to_version,
        channel=channel,
        status=AgentDeploymentStatus.ROLLED_BACK,
        artifact_id=to_artifact_id,
        manifest_sha256=to_manifest_sha256,
        previous_version=from_version,
        previous_artifact_id=from_artifact_id,
        actor=actor,
    )
    self._events.append(event)
    return event
```

## 9. CI/CD Pipeline 集成

### 9.1 改造后的 .gitlab-ci.yml

```yaml
# --- Stage: package ---
package_agents:
  stage: package
  script:
    - uv run python scripts/package_agent.py --agent changed
    - |
      echo "--- Artifact Summary ---"
      for f in dist/agents/*.sha256; do
        [ -f "$f" ] && cat "$f"
      done
  artifacts:
    paths:
      - dist/
    expire_in: 30 days    # 从 7 天延长到 30 天，覆盖回滚窗口
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'

# --- Stage: deploy ---
deploy_staging:
  stage: deploy
  script:
    - |
      for meta in dist/agents/*.meta.json; do
        [ -f "$meta" ] || continue
        AGENT_ID=$(jq -r '.agent_id' "$meta")
        ARTIFACT_ID=$(jq -r '.artifact_id' "$meta")
        MANIFEST_SHA=$(jq -r '.manifest_sha256' "$meta")
        PKG_SHA_FILE="dist/agents/${ARTIFACT_ID}.sha256"
        PKG_SHA="sha256:$(awk '{print $1}' "$PKG_SHA_FILE")"

        uv run python scripts/deploy_agent.py \
          --agent "$AGENT_ID" \
          --env staging \
          --artifact-id "$ARTIFACT_ID" \
          --manifest-sha256 "$MANIFEST_SHA" \
          --package-sha256 "$PKG_SHA"
      done
  environment:
    name: staging
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      when: manual

# --- Stage: promote ---
promote_canary:
  stage: promote
  script:
    - |
      for meta in dist/agents/*.meta.json; do
        [ -f "$meta" ] || continue
        AGENT_ID=$(jq -r '.agent_id' "$meta")
        ARTIFACT_ID=$(jq -r '.artifact_id' "$meta")
        MANIFEST_SHA=$(jq -r '.manifest_sha256' "$meta")
        PKG_SHA_FILE="dist/agents/${ARTIFACT_ID}.sha256"
        PKG_SHA="sha256:$(awk '{print $1}' "$PKG_SHA_FILE")"

        uv run python scripts/promote_agent.py \
          --agent "$AGENT_ID" \
          --channel prod \
          --traffic 5 \
          --artifact-id "$ARTIFACT_ID" \
          --manifest-sha256 "$MANIFEST_SHA" \
          --package-sha256 "$PKG_SHA"
      done
  environment:
    name: production
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      when: manual

promote_full:
  stage: promote
  script:
    - |
      # 使用与 promote_canary 完全相同的 artifact_id
      for meta in dist/agents/*.meta.json; do
        [ -f "$meta" ] || continue
        AGENT_ID=$(jq -r '.agent_id' "$meta")
        ARTIFACT_ID=$(jq -r '.artifact_id' "$meta")
        MANIFEST_SHA=$(jq -r '.manifest_sha256' "$meta")
        PKG_SHA_FILE="dist/agents/${ARTIFACT_ID}.sha256"
        PKG_SHA="sha256:$(awk '{print $1}' "$PKG_SHA_FILE")"

        uv run python scripts/promote_agent.py \
          --agent "$AGENT_ID" \
          --channel prod \
          --traffic 100 \
          --artifact-id "$ARTIFACT_ID" \
          --manifest-sha256 "$MANIFEST_SHA" \
          --package-sha256 "$PKG_SHA"
      done
  environment:
    name: production
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      when: manual
  needs:
    - promote_canary
```

### 9.2 CI 环境变量

`package_agent.py` 从以下环境变量获取 CI 上下文：

| 环境变量 | 来源 | 写入 metadata 字段 |
| --- | --- | --- |
| `CI_COMMIT_SHA` | GitLab CI 自动注入 | `git_commit` |
| `CI_COMMIT_REF_NAME` | GitLab CI 自动注入 | `git_ref` |
| `CI_MERGE_REQUEST_IID` | GitLab CI 自动注入 | `mr_iid` |
| `CI_PIPELINE_ID` | GitLab CI 自动注入 | `metadata.ci_pipeline_id`（可选） |
| `AGENT_PLATFORM_VERSION` | 项目配置或 `pyproject.toml` | `platform_version` |

本地构建时，从 `git rev-parse HEAD` 和 `git rev-parse --abbrev-ref HEAD` 获取 commit 和 ref，`mr_iid` 为空。

## 10. 数据流全景

```
开发者修改 agents/myj/ 下的文件
  |
  v
git push -> GitLab MR
  |
  v
CI: lint -> test -> contract -> eval
  |                              |
  |                              v
  |                        eval-report.json
  |                        (eval_report_id = eval-myj-0.1.0-20260515T103000)
  |
  v
CI: package
  |
  +-> scripts/package_agent.py --agent changed
  |     1. ManifestLoader 校验 manifest.yaml
  |     2. 计算 manifest_sha256
  |     3. 收集文件（排除 __pycache__、tests/、*.pyc）
  |     4. 生成 metadata.json
  |     5. 打 tar.gz
  |     6. 计算 package_sha256
  |     7. 生成 artifact_id = myj-0.1.0-a3b2c1d4
  |     8. 输出:
  |         dist/agents/myj-0.1.0-a3b2c1d4.tar.gz
  |         dist/agents/myj-0.1.0-a3b2c1d4.sha256
  |         dist/agents/myj-0.1.0-a3b2c1d4.meta.json
  |
  v
CI: deploy_staging (manual trigger)
  |
  +-> scripts/deploy_agent.py --artifact-id myj-0.1.0-a3b2c1d4 --env staging
        -> POST /api/v1/.../deploy
        -> 平台服务端:
             [1] 校验 artifact 存在
             [2] 校验 package_sha256
             [3] 校验 manifest_sha256
             [4] ManifestLoader 校验 manifest
             [5] 校验 runtime_compat
             [6] 执行 EvalRunner
             [7] 校验 pass_rate >= required_pass_rate
             -> 通过 -> 创建 AgentDeployment (staging, artifact_id=myj-0.1.0-a3b2c1d4)
  |
  v
CI: promote_canary (manual trigger)
  |
  +-> scripts/promote_agent.py --artifact-id myj-0.1.0-a3b2c1d4 --traffic 5
        -> POST /api/v1/.../deploy (channel=prod, traffic=5)
        -> 平台服务端:
             [1-7] 同上
             [8] 查询 GitLab MR approval
             [9] 验证 human sign-off
             -> 通过 -> 创建 AgentDeployment (prod, canary, 5%)
  |
  v
CI: promote_full (manual trigger, needs promote_canary)
  |
  +-> scripts/promote_agent.py --artifact-id myj-0.1.0-a3b2c1d4 --traffic 100
        -> 使用同一个 artifact_id（保证灰度与全量是同一份产物）
        -> 创建 AgentDeployment (prod, stable, 100%)
```

## 11. 实现计划

### 11.1 第一批：最小可用（无外部依赖）

| 序号 | 任务 | 涉及文件 | 验收标准 |
| --- | --- | --- | --- |
| 1 | `package_agent.py` 生成 tar.gz + .sha256 + metadata.json | `scripts/package_agent.py` | 运行后 `dist/` 下出现 `<artifact_id>.tar.gz` 和 `<artifact_id>.sha256`；`sha256sum -c` 校验通过 |
| 2 | `package_agent.py --verify` 校验 artifact 完整性 | `scripts/package_agent.py` | 对合法 artifact 返回 exit 0；对篡改过的 artifact 返回 exit 1 |
| 3 | `DeploymentEvent` 增加 artifact 绑定字段 | `src/agent_platform/registry/deployment.py` | `DeploymentEvent` 包含 `artifact_id`、`manifest_sha256`、`package_sha256`、`eval_report_id`、`previous_artifact_id` |
| 4 | `deploy_agent.py` 传递 artifact 绑定参数 | `scripts/deploy_agent.py` | CLI 接受 `--artifact-id`、`--manifest-sha256`、`--package-sha256`；deploy API 请求包含这些字段 |
| 5 | `promote_agent.py` 传递 artifact 绑定参数 | `scripts/promote_agent.py` | canary 和 full promote 使用同一个 artifact_id |
| 6 | rollback 使用 artifact_id | `src/agent_platform/registry/deployment.py`、deploy API | rollback API 接受 `target_artifact_id`；event 记录 `from_artifact_id` / `to_artifact_id` |

### 11.2 第二批：依赖持久化设计落地

| 序号 | 任务 | 依赖 |
| --- | --- | --- |
| 7 | `agent_deployments` DB 表增加 artifact 字段 | `persistence-storage-design.md` |
| 8 | `deployment_audit_events` DB 表增加 artifact 字段 | `persistence-storage-design.md` |
| 9 | `ArtifactStore` 接口和 `LocalArtifactStore` 实现 | 无 |
| 10 | deploy API gate 检查增加 artifact SHA256 校验 | 第一批 #1-3 完成 |
| 11 | prod deploy gate 增加 MR approval 校验 | GitLab adapter |
| 12 | `.gitlab-ci.yml` package/deploy/promote 阶段按本文档改造 | 第一批 #4-5 完成 |

### 11.3 第三批：规模化扩展

| 序号 | 任务 | 依赖 |
| --- | --- | --- |
| 13 | `S3ArtifactStore` / `MinIOArtifactStore` 实现 | S3/MinIO 基础设施 |
| 14 | GitLab Package Registry 集成 | GitLab 配置 |
| 15 | artifact 签名（GPG 或 cosign） | `security-tenant-policy-design.md` |
| 16 | artifact 过期清理策略 | artifact store |
| 17 | deploy gate 增加 staging 运行时长检查 | 持久化 deployment 记录 |

## 12. 与其他设计文档的关系

| 文档 | 关系 |
| --- | --- |
| `01-contracts/agent-manifest-v1.md` | manifest 是 artifact 的核心组成部分；`manifest_sha256` 是 artifact 身份的一部分；`version.package_version` 是 artifact 版本号来源 |
| `05-production/persistence-storage-design.md` | deployment record 的 DB schema 需要增加 artifact 绑定字段（见 6.4） |
| `05-production/security-tenant-policy-design.md` | artifact 签名、prod deploy 审批属于安全治理范畴 |
| `05-production/observability-eval-feedback-design.md` | `eval_report_id` 绑定 artifact，eval report 作为发布 gate 输入 |
| `04-devflow/gitlab.md` | CI pipeline 集成、MR approval 校验 |
| `adr/0003-package-artifact-release.md` | 本文档的决策应同步记录到该 ADR |

## 13. 验收标准

对应 `next-stage-design-plan.md` P0-2 的三条验收标准：

1. **`scripts/package_agent.py` 生成可校验 artifact**：运行 `--agent myj` 后产出 `myj-<version>-<sha_prefix>.tar.gz` + `.sha256` + `.meta.json`；运行 `--verify` 校验通过；`sha256sum -c` 校验通过。
2. **deploy 记录 artifact id 和 manifest hash**：`DeploymentEvent` 和 deploy API 响应中包含 `artifact_id`、`manifest_sha256`、`package_sha256`。
3. **rollback 使用历史 deployment 的 artifact id**：rollback API 接受 `target_artifact_id`，从历史 deployment record 获取 artifact 标识并重新部署；rollback event 记录 `from_artifact_id` 和 `to_artifact_id`。

额外验收：

4. artifact 文件被篡改后，deploy 时 `package_sha256` 校验失败，部署被拒绝。
5. `metadata.json` 包含 `git_commit`、`mr_iid`、`eval_report_id`，可追溯到构建来源。
6. canary promote 和 full promote 使用同一个 `artifact_id`。
