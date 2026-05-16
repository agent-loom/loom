"""SCM (Source Code Management) adapter abstraction."""

from agent_platform.integrations.scm.protocol import MergeRequestResult, ScmAdapter

__all__ = ["ScmAdapter", "MergeRequestResult"]
