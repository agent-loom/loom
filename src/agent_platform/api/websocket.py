"""WebSocket 实时通信管理器，含鉴权、背压和优雅断连。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from agent_platform.domain.models import AgentRequest, RuntimeRequest
from agent_platform.registry.registry import AgentNotFoundError
from agent_platform.router import AgentRouter
from agent_platform.runtime.manager import RuntimeManager

logger = logging.getLogger(__name__)

MAX_PENDING_MESSAGES = 32
MAX_MESSAGE_SIZE = 65536
REPLAY_BUFFER_SIZE = 50


class AgentWebSocketManager:
    """Manages WebSocket connections with auth, backpressure, and graceful shutdown."""

    def __init__(
        self,
        router: AgentRouter,
        runtime_manager: RuntimeManager,
        *,
        api_key: str | None = None,
        key_store=None,
        max_connections: int = 100,
    ):
        self.router = router
        self.runtime_manager = runtime_manager
        self._api_key = api_key
        self._key_store = key_store
        self._max_connections = max_connections
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, int] = {}
        self._replay_buffers: dict[str, deque] = {}
        self._last_seq: dict[str, int] = {}

    async def handle(self, websocket: WebSocket, session_id: str | None = None) -> None:
        if len(self._connections) >= self._max_connections:
            await websocket.close(code=1013, reason="server at capacity")
            return

        auth_identity = await self._authenticate(websocket)
        if auth_identity is None:
            return

        await websocket.accept()
        ws_id = session_id or f"ws_{id(websocket)}"
        self._connections[ws_id] = websocket
        self._pending[ws_id] = 0
        if ws_id not in self._replay_buffers:
            self._replay_buffers[ws_id] = deque(maxlen=REPLAY_BUFFER_SIZE)
            self._last_seq[ws_id] = 0
        logger.info(
            "WebSocket connected: %s (auth=%s, total=%d)",
            ws_id, auth_identity.get("subject", "unknown"), len(self._connections),
        )

        last_seen_seq = websocket.query_params.get("last_seq")
        if last_seen_seq is not None:
            await self._replay_missed(websocket, ws_id, int(last_seen_seq))

        try:
            while True:
                if self._pending.get(ws_id, 0) >= MAX_PENDING_MESSAGES:
                    await websocket.send_json({
                        "type": "error",
                        "error": {
                            "code": "BACKPRESSURE",
                            "message": "too many pending messages, slow down",
                        },
                    })
                    await asyncio.sleep(0.5)
                    continue

                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=300,
                    )
                except TimeoutError:
                    await websocket.send_json({"type": "ping"})
                    continue

                if len(raw) > MAX_MESSAGE_SIZE:
                    await websocket.send_json({
                        "type": "error",
                        "error": {
                            "code": "MESSAGE_TOO_LARGE",
                            "message": "message exceeds size limit",
                        },
                    })
                    continue

                self._pending[ws_id] = self._pending.get(ws_id, 0) + 1
                try:
                    data = json.loads(raw)
                    response = await self._process_message(data, ws_id, auth_identity)
                    self._last_seq[ws_id] = self._last_seq.get(ws_id, 0) + 1
                    response["seq"] = self._last_seq[ws_id]
                    self._replay_buffers.setdefault(
                        ws_id, deque(maxlen=REPLAY_BUFFER_SIZE),
                    ).append(response)
                    await websocket.send_json(response)
                except Exception as exc:
                    await websocket.send_json({
                        "type": "error",
                        "error": {"code": "PROCESSING_ERROR", "message": str(exc)},
                    })
                finally:
                    self._pending[ws_id] = max(0, self._pending.get(ws_id, 1) - 1)

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected: %s", ws_id)
        except Exception:
            logger.exception("WebSocket error: %s", ws_id)
        finally:
            self._connections.pop(ws_id, None)
            self._pending.pop(ws_id, None)

    async def _authenticate(self, websocket: WebSocket) -> dict[str, Any] | None:
        if not self._api_key and self._key_store is None:
            return {"subject": "anonymous", "role": "platform_admin"}

        query_params = websocket.query_params
        token = query_params.get("token")

        if not token:
            headers = dict(websocket.headers)
            auth_header = headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            if not token:
                token = headers.get("x-api-key")

        if not token:
            await websocket.close(code=4001, reason="authentication required")
            return None

        if self._key_store is not None:
            if hasattr(self._key_store, "verify_async"):
                record = await self._key_store.verify_async(token)
            else:
                record = self._key_store.verify(token)
            if record is not None:
                return {
                    "subject": record.created_by,
                    "role": record.role,
                    "tenant_id": record.tenant_id,
                    "key_id": record.key_id,
                }

        if self._api_key and token == self._api_key:
            return {"subject": "api-key-user", "role": "platform_admin"}

        await websocket.close(code=4001, reason="invalid credentials")
        return None

    async def _process_message(
        self, data: dict[str, Any], ws_id: str, auth: dict[str, Any],
    ) -> dict[str, Any]:
        msg_type = data.get("type", "chat")

        if msg_type == "ping":
            return {"type": "pong"}

        if msg_type == "chat":
            request = AgentRequest.model_validate(data.get("payload", data))
            if not request.session_id:
                request.session_id = ws_id

            try:
                route = await self.router.route(request)
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

    async def close_all(self, reason: str = "server shutting down") -> None:
        for _ws_id, ws in list(self._connections.items()):
            try:
                await ws.close(code=1001, reason=reason)
            except Exception:
                pass
        self._connections.clear()
        self._pending.clear()

    async def _replay_missed(
        self, websocket: WebSocket, ws_id: str, last_seen_seq: int,
    ) -> None:
        buf = self._replay_buffers.get(ws_id)
        if not buf:
            return
        missed = [msg for msg in buf if msg.get("seq", 0) > last_seen_seq]
        if missed:
            await websocket.send_json({
                "type": "replay",
                "messages": missed,
                "count": len(missed),
            })
