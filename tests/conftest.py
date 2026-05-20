"""共用测试配置。

单元测试不依赖真实数据库和 API key 认证。
先触发 load_dotenv()（通过导入 config），再清除 DB/认证相关变量，
确保 app 以内存模式运行。
"""
from __future__ import annotations

import os

# 触发 config.py 顶层的 load_dotenv()，
# 然后立即清除 DB/认证变量，让测试使用内存模式。
import agent_platform.config  # noqa: F401 — 触发 load_dotenv

os.environ.pop("DATABASE_URL", None)
os.environ.pop("AGENT_PLATFORM_API_KEY", None)
# 清除真实 LLM key，避免 ModelGateway stub 测试意外调用真实 API
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

# 清除 settings 缓存，确保后续 get_settings() 重新读取（无 DB/API key）
from agent_platform.config import get_settings  # noqa: E402
get_settings.cache_clear()
