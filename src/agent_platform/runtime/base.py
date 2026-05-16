"""运行时后端抽象层，定义所有后端实现的协议接口。"""

from typing import Protocol

from agent_platform.domain.models import RuntimeRequest, RuntimeResponse


class RuntimeBackend(Protocol):
    """运行时后端协议，所有后端实现必须遵循此接口。"""

    name: str

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        """执行运行时请求并返回响应。"""
        ...

