"""Runner 适配器共享工具函数。"""

from __future__ import annotations

import os

_ALLOWED_ENV_KEYS = frozenset([
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "TZ", "EDITOR", "VISUAL",
    "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    "NODE_PATH", "NODE_ENV",
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
])


def build_safe_env() -> dict[str, str]:
    """构建仅包含白名单环境变量的安全字典。"""
    return {k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_KEYS}
