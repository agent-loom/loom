"""JSON Schema 校验器：对工具调用参数进行完整 JSON Schema 校验。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import jsonschema
    from jsonschema import ValidationError as _JVE

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAS_JSONSCHEMA = False
    _JVE = Exception  # type: ignore[assignment,misc]


class SchemaValidationError(Exception):
    """工具参数校验失败异常。"""

    def __init__(self, message: str, path: str = "", schema_path: str = ""):
        super().__init__(message)
        self.path = path
        self.schema_path = schema_path


def validate_tool_input(
    schema: dict[str, Any],
    payload: dict[str, Any],
) -> str | None:
    """校验工具调用参数是否符合 JSON Schema。

    返回 None 表示校验通过，否则返回错误描述字符串。
    当 jsonschema 库不可用时退化为基础校验（required + type）。
    """
    if not schema:
        return None

    if _HAS_JSONSCHEMA:
        return _validate_with_jsonschema(schema, payload)
    return _validate_basic(schema, payload)


def _validate_with_jsonschema(
    schema: dict[str, Any],
    payload: dict[str, Any],
) -> str | None:
    """使用 jsonschema 库执行完整校验。"""
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except _JVE as exc:
        path = ".".join(str(p) for p in exc.absolute_path) if exc.absolute_path else "(root)"
        return f"schema validation failed at '{path}': {exc.message}"
    except jsonschema.SchemaError as exc:
        logger.warning("工具 schema 自身无效: %s", exc.message)
        return f"invalid tool schema: {exc.message}"
    return None


def _validate_basic(
    schema: dict[str, Any],
    payload: dict[str, Any],
) -> str | None:
    """无 jsonschema 库时的退化校验：仅检查 required 和顶层 type。"""
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for field in required:
        if field not in payload:
            return f"missing required field: {field}"

    for key, value in payload.items():
        if key in properties:
            prop_spec = properties[key]
            expected_type = prop_spec.get("type")
            if expected_type and not _check_type(value, expected_type):
                return (
                    f"field '{key}' expected type "
                    f"'{expected_type}', got "
                    f"'{type(value).__name__}'"
                )

    # additionalProperties 检查
    additional = schema.get("additionalProperties")
    if additional is False and properties:
        extra = set(payload.keys()) - set(properties.keys())
        if extra:
            return f"unexpected fields: {sorted(extra)}"

    return None


def _check_type(value: Any, expected: str) -> bool:
    """基础类型映射检查。"""
    type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    expected_types = type_map.get(expected)
    if expected_types is None:
        return True
    return isinstance(value, expected_types)
