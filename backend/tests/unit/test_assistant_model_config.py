"""
Unit tests for app.infrastructure.assistant.model_config
"""
from __future__ import annotations

import pytest

from app.infrastructure.assistant.model_config import (
    DEFAULT_ASSISTANT_MODEL,
    ALLOWED_ASSISTANT_MODEL_IDS,
    available_models,
    normalize_model,
    get_tenant_assistant_model,
)


# ---------------------------------------------------------------------------
# normalize_model
# ---------------------------------------------------------------------------

def test_normalize_model_valid_id_passes_through():
    """A recognised Groq model id is returned unchanged."""
    valid_id = DEFAULT_ASSISTANT_MODEL
    assert normalize_model(valid_id) == valid_id


def test_normalize_model_unknown_returns_default():
    """An unrecognised model id falls back to the default."""
    assert normalize_model("totally-unknown-model") == DEFAULT_ASSISTANT_MODEL


def test_normalize_model_none_returns_default():
    """None (unset) falls back to the default."""
    assert normalize_model(None) == DEFAULT_ASSISTANT_MODEL


def test_normalize_model_other_valid_id():
    """Any other id in the allowed set passes through."""
    # Pick a second model that is not the default
    other = next(m for m in ALLOWED_ASSISTANT_MODEL_IDS if m != DEFAULT_ASSISTANT_MODEL)
    assert normalize_model(other) == other


# ---------------------------------------------------------------------------
# available_models
# ---------------------------------------------------------------------------

def test_available_models_is_non_empty():
    models = available_models()
    assert len(models) > 0


def test_available_models_have_id_and_name():
    for m in available_models():
        assert "id" in m
        assert "name" in m
        assert isinstance(m["id"], str) and m["id"]
        assert isinstance(m["name"], str) and m["name"]


def test_available_models_includes_default():
    ids = {m["id"] for m in available_models()}
    assert DEFAULT_ASSISTANT_MODEL in ids


# ---------------------------------------------------------------------------
# get_tenant_assistant_model  (async, uses a tiny fake db_client)
# ---------------------------------------------------------------------------

class _FakeExecute:
    def __init__(self, rows):
        self.data = rows


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def execute(self):
        return _FakeExecute(self._rows)


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQuery(self._rows)


class _RaisingClient:
    """Simulates a DB client that raises on any call."""
    def table(self, _name):
        raise RuntimeError("DB unavailable")


@pytest.mark.asyncio
async def test_get_tenant_assistant_model_returns_stored_value():
    valid_id = DEFAULT_ASSISTANT_MODEL  # definitely in the allowed set
    client = _FakeClient([{"assistant_model": valid_id}])
    result = await get_tenant_assistant_model(client, "tenant-abc")
    assert result == valid_id


@pytest.mark.asyncio
async def test_get_tenant_assistant_model_empty_rows_returns_default():
    client = _FakeClient([])
    result = await get_tenant_assistant_model(client, "tenant-abc")
    assert result == DEFAULT_ASSISTANT_MODEL


@pytest.mark.asyncio
async def test_get_tenant_assistant_model_null_value_returns_default():
    client = _FakeClient([{"assistant_model": None}])
    result = await get_tenant_assistant_model(client, "tenant-abc")
    assert result == DEFAULT_ASSISTANT_MODEL


@pytest.mark.asyncio
async def test_get_tenant_assistant_model_unknown_stored_value_returns_default():
    client = _FakeClient([{"assistant_model": "some-random-model"}])
    result = await get_tenant_assistant_model(client, "tenant-abc")
    assert result == DEFAULT_ASSISTANT_MODEL


@pytest.mark.asyncio
async def test_get_tenant_assistant_model_db_raises_returns_default():
    result = await get_tenant_assistant_model(_RaisingClient(), "tenant-abc")
    assert result == DEFAULT_ASSISTANT_MODEL
