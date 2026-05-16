"""评测结果反馈，将报告推送到 GitLab MR 和 Plane 工作项。"""

from __future__ import annotations

import logging

from agent_platform.evals.runner import EvalReport
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.scm.protocol import ScmAdapter
from agent_platform.persistence.repositories import EvalRunRepository

logger = logging.getLogger(__name__)


class EvalFeedback:
    """将评测报告发布到 GitLab、Plane 并持久化。"""

    def __init__(
        self,
        gitlab: ScmAdapter | None = None,
        plane: PlaneAdapter | None = None,
        eval_repo: EvalRunRepository | None = None,
    ):
        """初始化评测反馈服务。"""
        self.gitlab = gitlab
        self.plane = plane
        self.eval_repo = eval_repo

    async def post_to_gitlab(
        self,
        report: EvalReport,
        project_id: str,
        mr_iid: int,
        *,
        commit_sha: str | None = None,
    ) -> None:
        """将评测报告作为评论发布到 GitLab MR。"""
        if not self.gitlab:
            return
        body = self.format_report_markdown(report)
        await self.gitlab.comment_merge_request(project_id, mr_iid, body)
        logger.info("Eval report posted to MR %s/%s", project_id, mr_iid)

        if commit_sha:
            state = "success" if report.gate_passed else "failed"
            description = f"pass_rate={report.pass_rate:.1%}"
            try:
                await self.gitlab.update_commit_status(
                    project_id, commit_sha, state,
                    description=description,
                )
            except Exception:
                logger.warning("Failed to set commit status for %s", commit_sha)

    async def persist(self, report: EvalReport, *, trigger: str = "ci") -> None:
        """将评测报告持久化到数据库。"""
        if not self.eval_repo:
            return
        await self.eval_repo.record(
            agent_id=report.agent_id,
            agent_version=report.agent_version,
            total=report.total,
            passed=report.passed,
            pass_rate=report.pass_rate,
            required_pass_rate=report.required_pass_rate,
            gate_passed=report.gate_passed,
            results=[r.model_dump(mode="json") for r in report.results],
            trigger=trigger,
        )

    async def update_plane_state(
        self,
        report: EvalReport,
        project_id: str,
        work_item_id: str,
        *,
        review_state_id: str,
    ) -> None:
        """将评测结果同步到 Plane 工作项并更新状态。"""
        if not self.plane:
            return
        comment = self.format_report_markdown(report)
        await self.plane.add_comment(project_id, work_item_id, f"<pre>{comment}</pre>")
        if report.gate_passed:
            await self.plane.update_work_item_state(project_id, work_item_id, review_state_id)
            logger.info("Plane work item %s moved to Human Review", work_item_id)

    @staticmethod
    def format_report_markdown(report: EvalReport) -> str:
        """将评测报告格式化为 Markdown 文本。"""
        status = "PASSED" if report.gate_passed else "FAILED"
        lines = [
            f"## Eval Report: {report.agent_id}",
            "",
            f"- **Status**: {status}",
            f"- **Pass rate**: {report.pass_rate:.1%} (required: {report.required_pass_rate:.1%})",
            f"- **Passed**: {report.passed}/{report.total}",
            "",
        ]
        failed = [r for r in report.results if not r.passed]
        if failed:
            lines.append("### Failed Cases")
            lines.append("")
            for r in failed:
                lines.append(f"- `{r.id}`: {r.reason}")
            lines.append("")

        return "\n".join(lines)
