"""请求输入消毒中间件：防止 XSS、SQL 注入等常见攻击。"""

from __future__ import annotations

import logging
import re

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_XSS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),
    re.compile(r"<iframe[\s>]", re.IGNORECASE),
    re.compile(r"<object[\s>]", re.IGNORECASE),
]

_SQL_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r";\s*DROP\s+TABLE", re.IGNORECASE),
    re.compile(r";\s*DELETE\s+FROM", re.IGNORECASE),
    re.compile(r"UNION\s+SELECT", re.IGNORECASE),
    re.compile(r"'\s*OR\s+'1'\s*=\s*'1", re.IGNORECASE),
    re.compile(r"--\s*$", re.MULTILINE),
]

_SKIP_PATHS = {"/health", "/health/ready", "/metrics", "/docs", "/openapi.json", "/redoc"}

_SKIP_PREFIXES = ("/api/v1/integrations/",)


def check_payload_safety(text: str) -> str | None:
    """检查文本中是否包含危险模式，返回匹配到的威胁类型或 None。"""
    for pattern in _XSS_PATTERNS:
        if pattern.search(text):
            return "XSS"
    for pattern in _SQL_INJECTION_PATTERNS:
        if pattern.search(text):
            return "SQL_INJECTION"
    return None


class InputSanitizationMiddleware(BaseHTTPMiddleware):
    """请求体安全检查中间件。

    功能：
    - 限制请求体大小
    - Content-Type 检查
    - 对字符串内容做 XSS/SQL 注入模式检测
    """

    def __init__(self, app, *, max_body_bytes: int = 10 * 1024 * 1024):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)
        if any(request.url.path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > self.max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "PAYLOAD_TOO_LARGE",
                            "message": f"request body exceeds {self.max_body_bytes} bytes",
                        }
                    },
                )

            content_type = request.headers.get("content-type", "")
            if request.url.path.startswith("/api/") and content_length:
                if content_length != "0" and not any(
                    ct in content_type
                    for ct in ("application/json", "multipart/form-data", "text/plain")
                ):
                    return JSONResponse(
                        status_code=415,
                        content={
                            "error": {
                                "code": "UNSUPPORTED_MEDIA_TYPE",
                                "message": "expected application/json",
                            }
                        },
                    )

            if "application/json" in content_type:
                try:
                    body = await request.body()
                    if body:
                        text = body.decode("utf-8", errors="replace")
                        threat = check_payload_safety(text)
                        if threat:
                            logger.warning(
                                "请求体安全检查命中 %s: path=%s",
                                threat, request.url.path,
                            )
                            return JSONResponse(
                                status_code=400,
                                content={
                                    "error": {
                                        "code": "UNSAFE_INPUT",
                                        "message": (
                                    "request body contains potentially"
                                    f" dangerous content ({threat})"
                                ),
                                    }
                                },
                            )
                except Exception:
                    logger.debug("请求体安全检查读取失败，放行", exc_info=True)

        return await call_next(request)
