"""Runner 适配器共享工具函数。"""

from __future__ import annotations

import os

_SECRET_ENV_KEYWORDS = frozenset([
    "PLANE_API_KEY", "GITLAB_TOKEN", "API_KEY",
    "SECRET", "PASSWORD", "CREDENTIAL",
])


def build_safe_env() -> dict[str, str]:
    """构建过滤敏感环境变量后的安全环境变量字典。"""
    safe_env = dict(os.environ)
    for key in list(safe_env.keys()):
        if any(kw in key.upper() for kw in _SECRET_ENV_KEYWORDS):
            del safe_env[key]
    return safe_env
