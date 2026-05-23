"""请求输入边界过滤守卫：拦截 XSS 与 SQL 注入等常见应用层边界威胁。

设计定位：
  API 服务边缘的最外层网络防线 (Input Sanitizer Middleware)。
  对应 docs/02-architecture/agent-platform-design.md 中的"边缘网关/安全层"组件。
  在 HTTP 请求到达核心鉴权与 API 路由层之前，对载荷大小 (Content-Length)、格式 (Content-Type)
  以及 JSON 输入内容进行静态正则特征检查，过滤高频明显的注入型跨站脚本 (XSS) 和数据库 SQL 注入模式。
"""

from __future__ import annotations

import logging
import re

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# TODO Design Gap:
# 静态正则扫描仅能过滤最泛滥、明显的 XSS 与 SQL 注入特征。由于性能限制，这里无法对输入进行语法分析树 (AST) 解析，
# 可能会对某些合法的 Python/JavaScript 示例代码造成误拦截，或者由于混淆编码发生逃逸绕过。
# 后续应考虑演进到在 Policy 决策层由隔离的沙箱或轻量安全大模型网关进行精细审计。
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

# TODO Design Gap:
# _SKIP_PREFIXES 设定豁免了 "/api/v1/integrations/" 整个路由命名空间的所有请求校验。
# 这种“粗放型白名单”容易造成外部 Webhook 接口成为 SQLI 或 XSS 注入盲区，后续应限定具体的三方鉴权规则或提供特定字段过滤。
_SKIP_PREFIXES = ("/api/v1/integrations/",)


def check_payload_safety(text: str) -> str | None:
    """静态检查文本内容中是否包含不安全的注入威胁特征。"""
    for pattern in _XSS_PATTERNS:
        if pattern.search(text):
            return "XSS"
    for pattern in _SQL_INJECTION_PATTERNS:
        if pattern.search(text):
            return "SQL_INJECTION"
    return None


class InputSanitizationMiddleware(BaseHTTPMiddleware):
    """边缘输入净化拦截中间件 (Input Sanitization Middleware)

    实施大小硬限制与载荷语义的安全检查拦截。
    处理管线：
      1. 跳过健康检查及注册排除的 API 前缀路径
      2. 校验 POST/PUT/PATCH 流量下的 Content-Length 头部大小限额
      3. /api/ 前缀下对 Content-Type 的规范性一致性拦截 (仅接受 JSON、文本与 Form Data)
      4. 提取 JSON body 进行威胁模式检测
    """

    def __init__(self, app, *, max_body_bytes: int = 10 * 1024 * 1024):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)
        if any(request.url.path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        # TODO Design Gap:
        # 1. 块传输校验缺陷：当客户端采用 Chunked Transfer Encoding 提交且不设置 Content-Length 头部时，
        #    目前的中间件机制可能会绕过 max_body_bytes 的阈值拦截。
        # 2. 目前对于 "multipart/form-data" 和 "text/plain" 的内容并未读取或执行 check_payload_safety 扫描，
        #    这可能导致恶意载荷通过非 JSON 管道渗透进系统。
        # 3. decode("utf-8", errors="replace") 时，对非法字符的 "replace" 行为可能意外破坏某些特定的混淆攻击载荷，
        #    从而也给逃逸带来可乘之机。
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
                    # TODO Design Gap:
                    # 如果由于网络读取异常或超时导致 body 提取失败，目前直接记日志并放行流量。
                    # 在极高安全环境或严格风控要求下，读取失败应被断言为不确定风险并就地返回 400。
                    logger.debug("请求体安全检查读取失败，放行", exc_info=True)

        return await call_next(request)
