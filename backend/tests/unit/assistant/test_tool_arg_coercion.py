"""LLM-emitted boolean args: schema tolerance + central dispatch coercion.

Live failure (2026-07-16 voice test): the model emitted `"confirm": "true"`
(a JSON string) for create_campaign; Groq's schema validation rejected the
call twice and the raw validation error surfaced in the voice transcript.
The fix accepts ["boolean", "string"] in the schemas and normalises exactly
once in dispatch_tool.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.infrastructure.assistant.tools import ALL_TOOLS
from app.infrastructure.assistant.tools.coercion import coerce_bool
from app.infrastructure.assistant.tools.dispatch import dispatch_tool
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS


class TestCoerceBool:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            (" YES ", True),
            ("1", True),
            ("apply", True),
            ("false", False),
            ("no", False),
            ("0", False),
            ("preview", False),
            ("", False),
            (None, False),
            (1, True),
            (0, False),
        ],
    )
    def test_table(self, value, expected):
        assert coerce_bool(value) is expected

    def test_unrecognised_string_falls_back_to_default(self):
        assert coerce_bool("maybe") is False
        assert coerce_bool("maybe", default=True) is True

    def test_none_uses_default(self):
        assert coerce_bool(None, default=True) is True


class TestSchemasTolerateStringBooleans:
    def test_no_strict_boolean_params_remain(self):
        """Every boolean-ish flag must accept a string so a quoted boolean
        from the model can never fail Groq-side validation."""
        strict = []

        def walk(name, node):
            if isinstance(node, dict):
                if node.get("type") == "boolean":
                    strict.append(name)
                for key, child in node.items():
                    walk(f"{name}.{key}", child)
            elif isinstance(node, list):
                for child in node:
                    walk(name, child)

        for schema in GROQ_TOOL_SCHEMAS:
            fn = schema["function"]
            walk(fn["name"], fn.get("parameters", {}))
        assert strict == []


class TestDispatchCoercion:
    @pytest.mark.asyncio
    async def test_string_confirm_reaches_tool_as_real_bool(self, monkeypatch):
        received = {}

        async def fake_tool(tenant_id, db_client, conversation_id=None, **kwargs):
            received.update(kwargs)
            return {"success": True}

        monkeypatch.setitem(
            ALL_TOOLS, "create_campaign", {"function": fake_tool, "description": "", "input_schema": None}
        )

        result = await dispatch_tool(
            "create_campaign", "t1", None, None, {"name": "x", "confirm": "true"}
        )

        assert result == {"success": True}
        assert received["confirm"] is True

    @pytest.mark.asyncio
    async def test_string_false_never_truthy_strings_into_apply(self, monkeypatch):
        received = {}

        async def fake_tool(tenant_id, db_client, conversation_id=None, **kwargs):
            received.update(kwargs)
            return {"success": True}

        monkeypatch.setitem(
            ALL_TOOLS, "create_campaign", {"function": fake_tool, "description": "", "input_schema": None}
        )

        await dispatch_tool(
            "create_campaign", "t1", None, None, {"name": "x", "confirm": "false"}
        )

        assert received["confirm"] is False

    @pytest.mark.asyncio
    async def test_caller_args_dict_is_not_mutated(self, monkeypatch):
        async def fake_tool(tenant_id, db_client, **kwargs):
            return {"success": True}

        monkeypatch.setitem(
            ALL_TOOLS, "read_emails", {"function": fake_tool, "description": "", "input_schema": None}
        )
        args = {"unread_only": "true"}

        await dispatch_tool("read_emails", "t1", None, None, args)

        assert args["unread_only"] == "true"
