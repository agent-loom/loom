from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from agent_platform.domain.models import AgentRequest, RuntimeRequest
from agent_platform.registry.registry import AgentNotFoundError
from agent_platform.router import AgentRouter
from agent_platform.runtime.manager import RuntimeManager

logger = logging.getLogger(__name__)


class AgentWebSocketManager:
    """Manages WebSocket connections for real-time agent communication."""

    def __init__(
        self,
        router: AgentRouter,
        runtime_manager: RuntimeManager,
    ):
        self.router = router
        self.runtime_manager = runtime_manager
        self._connections: dict[str, WebSocket] = {}

    async def handle(self, websocket: WebSocket, session_id: str | None = None) -> None:
        await websocket.accept()
        ws_id = session_id or f"ws_{id(websocket)}"
        self._connections[ws_id] = websocket
        logger.info("WebSocket connected: %s", ws_id)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                    response = await self._process_message(data, ws_id)
                    await websocket.send_json(response)
                except Exception as exc:
                    await websocket.send_json({
                        "type": "error",
                        "error": {"code": "PROCESSING_ERROR", "message": str(exc)},
                    })
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected: %s", ws_id)
        finally:
            self._connections.pop(ws_id, None)

    async def _process_message(self, data: dict[str, Any], ws_id: str) -> dict[str, Any]:
        msg_type = data.get("type", "chat")

        if msg_type == "ping":
            return {"type": "pong"}

        if msg_type == "chat":
            request = AgentRequest.model_validate(data.get("payload", data))
            if not request.session_id:
                request.session_id = ws_id

            try:
                route = self.router.route(request)
            except AgentNotFoundError as exc:
                return {
                    "type": "error",
                    "error": {"code": "AGENT_NOT_FOUND", "message": str(exc)},
                }

            runtime_request = RuntimeRequest(
                request=request,
                agent_spec=route.agent_spec,
                route_reason=route.reason,
                deployment_id=route.deployment_id,
            )

            runtime_response = await self.runtime_manager.run(runtime_request)
            return {
                "type": "response",
                "payload": runtime_response.response.model_dump(mode="json"),
            }

        return {
            "type": "error",
            "error": {
                "code": "UNKNOWN_TYPE",
                "message": f"unknown message type: {msg_type}",
            },
        }

    @property
    def active_connections(self) -> int:
        return len(self._connections)
