from agent_platform.domain.models import AgentError, OutputStatus, RuntimeRequest, RuntimeResponse
from agent_platform.runtime.native import NativeRuntimeBackend


class HermesRuntimeBackend:
    """Placeholder backend that preserves the Hermes adapter contract.

    The real implementation will translate AgentManifest/runtime config into
    Hermes AIAgent calls. Until then, this backend returns a standard failed
    AgentResponse shape through the native response mapper.
    """

    name = "hermes"

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        native_response = await NativeRuntimeBackend().run(request)
        response = native_response.response
        response.output.status = OutputStatus.FAILED
        response.error = AgentError(
            code="RUNTIME_NOT_IMPLEMENTED",
            message="HermesRuntimeBackend is not implemented yet",
            details={"agent_id": request.agent_spec.agent_id},
            retryable=False,
        )
        if response.debug is None:
            response.debug = {}
        response.debug["runtime_backend"] = self.name
        return RuntimeResponse(response=response)
