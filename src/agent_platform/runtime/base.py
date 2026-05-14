from typing import Protocol

from agent_platform.domain.models import RuntimeRequest, RuntimeResponse


class RuntimeBackend(Protocol):
    name: str

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        ...

