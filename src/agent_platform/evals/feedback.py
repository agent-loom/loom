from __future__ import annotations

import logging

from agent_platform.evals.runner import EvalReport
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter

logger = logging.getLogger(__name__)


class EvalFeedback:
    def __init__(
        self,
        gitlab: GitLabAdapter | None = None,
        plane: PlaneAdapter | None = None,
    ):
        self.gitlab = gitlab
        self.plane = plane

    async def post_to_gitlab(
        self,
        report: EvalReport,
        project_id: str,
        mr_iid: int,
    ) -> None:
        if not self.gitlab:
            return
        body = self.format_report_markdown(report)
        await self.gitlab.comment_merge_request(project_id, mr_iid, body)
        logger.info("Eval report posted to MR %s/%s", project_id, mr_iid)

    async def update_plane_state(
        self,
        report: EvalReport,
        project_id: str,
        work_item_id: str,
        *,
        review_state_id: str,
    ) -> None:
        if not self.plane:
            return
        comment = self.format_report_markdown(report)
        await self.plane.add_comment(project_id, work_item_id, f"<pre>{comment}</pre>")
        if report.gate_passed:
            await self.plane.update_work_item_state(project_id, work_item_id, review_state_id)
            logger.info("Plane work item %s moved to Human Review", work_item_id)

    @staticmethod
    def format_report_markdown(report: EvalReport) -> str:
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
