"""工具 JSON Schema 参数校验测试。"""

import pytest

from agent_platform.tools.schema_validator import (
    _validate_basic,
    validate_tool_input,
)


class TestValidateToolInput:
    """测试完整 JSON Schema 校验（使用 jsonschema 库）。"""

    def test_empty_schema_passes(self):
        assert validate_tool_input({}, {"any": "data"}) is None

    def test_valid_required_fields(self):
        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        assert validate_tool_input(schema, {"name": "Alice", "age": 30}) is None

    def test_missing_required_field(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        result = validate_tool_input(schema, {})
        assert result is not None
        assert "name" in result

    def test_wrong_type(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        result = validate_tool_input(schema, {"count": "not_a_number"})
        assert result is not None
        assert "count" in result or "integer" in result

    def test_string_min_length(self):
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 3}},
        }
        result = validate_tool_input(schema, {"query": "ab"})
        assert result is not None

    def test_string_max_length(self):
        schema = {
            "type": "object",
            "properties": {"code": {"type": "string", "maxLength": 5}},
        }
        result = validate_tool_input(schema, {"code": "toolong"})
        assert result is not None

    def test_number_minimum(self):
        schema = {
            "type": "object",
            "properties": {"amount": {"type": "number", "minimum": 0}},
        }
        result = validate_tool_input(schema, {"amount": -1})
        assert result is not None

    def test_number_maximum(self):
        schema = {
            "type": "object",
            "properties": {"score": {"type": "number", "maximum": 100}},
        }
        result = validate_tool_input(schema, {"score": 150})
        assert result is not None

    def test_enum_valid(self):
        schema = {
            "type": "object",
            "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
        }
        assert validate_tool_input(schema, {"color": "red"}) is None

    def test_enum_invalid(self):
        schema = {
            "type": "object",
            "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
        }
        result = validate_tool_input(schema, {"color": "yellow"})
        assert result is not None

    def test_pattern_valid(self):
        schema = {
            "type": "object",
            "properties": {"email": {"type": "string", "pattern": r"^.+@.+\..+$"}},
        }
        assert validate_tool_input(schema, {"email": "a@b.com"}) is None

    def test_pattern_invalid(self):
        schema = {
            "type": "object",
            "properties": {"email": {"type": "string", "pattern": r"^.+@.+\..+$"}},
        }
        result = validate_tool_input(schema, {"email": "not-email"})
        assert result is not None

    def test_array_items_valid(self):
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        assert validate_tool_input(schema, {"tags": ["a", "b"]}) is None

    def test_array_items_invalid(self):
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        result = validate_tool_input(schema, {"tags": ["a", 123]})
        assert result is not None

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "required": ["key"],
                    "properties": {"key": {"type": "string"}},
                },
            },
        }
        assert validate_tool_input(schema, {"config": {"key": "val"}}) is None

    def test_nested_object_missing_required(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "required": ["key"],
                    "properties": {"key": {"type": "string"}},
                },
            },
        }
        result = validate_tool_input(schema, {"config": {}})
        assert result is not None

    def test_additional_properties_false(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        }
        result = validate_tool_input(schema, {"name": "ok", "extra": "bad"})
        assert result is not None

    def test_additional_properties_allowed(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        assert validate_tool_input(schema, {"name": "ok", "extra": "fine"}) is None

    def test_null_type(self):
        schema = {
            "type": "object",
            "properties": {"value": {"type": ["string", "null"]}},
        }
        assert validate_tool_input(schema, {"value": None}) is None

    def test_complex_schema(self):
        """复杂 schema 综合测试。"""
        schema = {
            "type": "object",
            "required": ["action", "target"],
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "delete"]},
                "target": {"type": "string", "minLength": 1},
                "options": {
                    "type": "object",
                    "properties": {
                        "force": {"type": "boolean"},
                        "timeout": {"type": "integer", "minimum": 0, "maximum": 60000},
                    },
                },
            },
        }
        valid = {
            "action": "create", "target": "resource-1",
            "options": {"force": True, "timeout": 5000},
        }
        assert validate_tool_input(schema, valid) is None

        invalid = {"action": "invalid_action", "target": "resource-1"}
        assert validate_tool_input(schema, invalid) is not None


class TestBasicValidation:
    """测试退化校验路径（无 jsonschema 库时使用）。"""

    def test_empty_schema(self):
        assert _validate_basic({}, {"any": "data"}) is None

    def test_missing_required(self):
        schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
        result = _validate_basic(schema, {})
        assert result is not None
        assert "name" in result

    def test_type_mismatch(self):
        schema = {"properties": {"count": {"type": "integer"}}}
        result = _validate_basic(schema, {"count": "text"})
        assert result is not None

    def test_valid_payload(self):
        schema = {
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        }
        assert _validate_basic(schema, {"name": "Alice", "age": 30}) is None

    def test_additional_properties_false(self):
        schema = {
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        }
        result = _validate_basic(schema, {"name": "ok", "extra": "bad"})
        assert result is not None
        assert "extra" in result


class TestToolExecutorIntegration:
    """通过 ToolExecutor 验证 schema 校验集成。"""

    @pytest.fixture()
    def executor(self):
        from agent_platform.tools.executor import ToolExecutor
        from agent_platform.tools.registry import ToolDefinition, ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="test_tool",
                description="test",
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                },
                handler=lambda p: {"result": p.get("query", "")},
            )
        )
        return ToolExecutor(registry=registry)

    @pytest.mark.asyncio()
    async def test_valid_input_executes(self, executor):
        result = await executor.execute(
            "test_tool",
            {"query": "hello"},
            allowed_tools=["test_tool"],
        )
        assert result.trace.status == "success"

    @pytest.mark.asyncio()
    async def test_invalid_input_rejected(self, executor):
        result = await executor.execute(
            "test_tool",
            {},
            allowed_tools=["test_tool"],
        )
        assert result.trace.status == "failed"
        assert result.trace.error == "VALIDATION_ERROR"

    @pytest.mark.asyncio()
    async def test_type_violation_rejected(self, executor):
        result = await executor.execute(
            "test_tool",
            {"query": "ok", "limit": "not_int"},
            allowed_tools=["test_tool"],
        )
        assert result.trace.status == "failed"
        assert result.trace.error == "VALIDATION_ERROR"

    @pytest.mark.asyncio()
    async def test_min_length_violation(self, executor):
        result = await executor.execute(
            "test_tool",
            {"query": ""},
            allowed_tools=["test_tool"],
        )
        assert result.trace.status == "failed"

    @pytest.mark.asyncio()
    async def test_range_violation(self, executor):
        result = await executor.execute(
            "test_tool",
            {"query": "ok", "limit": 200},
            allowed_tools=["test_tool"],
        )
        assert result.trace.status == "failed"
