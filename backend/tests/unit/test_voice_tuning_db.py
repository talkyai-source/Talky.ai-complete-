"""Tests for the DB-backed per-tenant voice-tuning lookup (T4-C3).

These cover the new ``set_db_lookup`` + ``for_tenant_async`` surface on
``VoiceTuningResolver``. The actual DB query lives in app startup
wiring; here we exercise the resolver against a mock callback so the
contract (resolution priority, error tolerance, cache-bypass) is
locked without requiring a Postgres in CI.

Resolution priority is:

    DB lookup → env override → env default → code default

Each test cases one branch of that ladder.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services.voice_tuning import (
    VoiceTuning,
    VoiceTuningResolver,
)


@pytest.fixture(autouse=True)
def _scrub_env():
    """Each test starts from a known-clean env. The autouse conftest
    already resets singletons; this strips the two TUNING_* env vars
    so we can write each test against a known starting point."""
    keys = ["TELEPHONY_TUNING_DEFAULT_JSON", "TELEPHONY_TUNING_OVERRIDES_JSON"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def _async_lookup(payload: Optional[Dict[str, Any]]) -> AsyncMock:
    """Build an awaitable that returns the given payload — saves
    re-typing the same AsyncMock idiom in every test."""
    return AsyncMock(return_value=payload)


# ---------------------------------------------------------------------
# set_db_lookup wiring contract
# ---------------------------------------------------------------------


class TestSetDbLookup:
    def test_default_is_no_lookup(self):
        """A fresh resolver has no DB layer wired — env-only behaviour."""
        resolver = VoiceTuningResolver()
        assert resolver._db_lookup is None

    @pytest.mark.asyncio
    async def test_set_then_clear_restores_env_only_path(self):
        resolver = VoiceTuningResolver()
        lookup = _async_lookup({"stt_eot_timeout_ms": 1500})
        resolver.set_db_lookup(lookup)
        result = await resolver.for_tenant_async("tenant-A")
        assert result.stt_eot_timeout_ms == 1500
        lookup.assert_called_once_with("tenant-A")

        # Clearing reverts the resolver to env-only.
        resolver.set_db_lookup(None)
        result_after_clear = await resolver.for_tenant_async("tenant-A")
        assert result_after_clear == VoiceTuning()
        # Clearing didn't trigger another lookup call.
        assert lookup.call_count == 1


# ---------------------------------------------------------------------
# Resolution-priority ladder — DB beats env beats default
# ---------------------------------------------------------------------


class TestResolutionPriority:
    @pytest.mark.asyncio
    async def test_db_value_overrides_code_default(self):
        resolver = VoiceTuningResolver()
        resolver.set_db_lookup(_async_lookup({"stt_eot_timeout_ms": 1500}))
        result = await resolver.for_tenant_async("tenant-A")
        assert result.stt_eot_timeout_ms == 1500
        # Untouched fields stay at code default.
        assert result.stt_eot_threshold == 0.85

    @pytest.mark.asyncio
    async def test_db_value_overrides_env_global_default(self):
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_DEFAULT_JSON": '{"stt_eot_timeout_ms": 750}',
        }):
            resolver = VoiceTuningResolver()
            resolver.set_db_lookup(_async_lookup({"stt_eot_timeout_ms": 1500}))
            result = await resolver.for_tenant_async("tenant-A")
            assert result.stt_eot_timeout_ms == 1500

    @pytest.mark.asyncio
    async def test_db_value_overrides_env_per_tenant_for_same_field(self):
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_OVERRIDES_JSON":
                '{"tenant-A": {"stt_eot_timeout_ms": 1000}}',
        }):
            resolver = VoiceTuningResolver()
            resolver.set_db_lookup(_async_lookup({"stt_eot_timeout_ms": 1500}))
            result = await resolver.for_tenant_async("tenant-A")
            assert result.stt_eot_timeout_ms == 1500  # DB wins

    @pytest.mark.asyncio
    async def test_db_only_overrides_specified_fields(self):
        """Partial DB payloads layer onto env values for untouched fields."""
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_OVERRIDES_JSON":
                '{"tenant-A": {"stt_eot_timeout_ms": 1000, '
                '"turn_0_min_confidence": 0.6}}',
        }):
            resolver = VoiceTuningResolver()
            # DB payload only sets the timeout — confidence floor stays
            # at the env value (0.6), not the DB-implied default.
            resolver.set_db_lookup(_async_lookup({"stt_eot_timeout_ms": 2500}))
            result = await resolver.for_tenant_async("tenant-A")
            assert result.stt_eot_timeout_ms == 2500
            assert result.turn_0_min_confidence == 0.6


# ---------------------------------------------------------------------
# Empty / missing DB result falls through to env+defaults
# ---------------------------------------------------------------------


class TestDbFallthrough:
    @pytest.mark.asyncio
    async def test_db_returns_none_falls_back_to_env(self):
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_OVERRIDES_JSON":
                '{"tenant-A": {"stt_eot_timeout_ms": 999}}',
        }):
            resolver = VoiceTuningResolver()
            resolver.set_db_lookup(_async_lookup(None))
            result = await resolver.for_tenant_async("tenant-A")
            assert result.stt_eot_timeout_ms == 999

    @pytest.mark.asyncio
    async def test_db_returns_empty_dict_falls_back_to_defaults(self):
        resolver = VoiceTuningResolver()
        resolver.set_db_lookup(_async_lookup({}))
        result = await resolver.for_tenant_async("tenant-A")
        assert result == VoiceTuning()

    @pytest.mark.asyncio
    async def test_no_tenant_id_skips_lookup(self):
        """Resolution for ``None`` tenant_id skips the DB layer
        entirely — sessions without a tenant context get the global
        default. Avoids passing ``None`` to the SQL query."""
        lookup = _async_lookup({"stt_eot_timeout_ms": 1500})
        resolver = VoiceTuningResolver()
        resolver.set_db_lookup(lookup)
        result = await resolver.for_tenant_async(None)
        assert result == VoiceTuning()
        lookup.assert_not_called()


# ---------------------------------------------------------------------
# Failure isolation — a broken DB lookup must NOT block calls
# ---------------------------------------------------------------------


class TestDbErrorIsolation:
    @pytest.mark.asyncio
    async def test_lookup_raising_falls_back_to_env(self, caplog):
        async def broken_lookup(_tenant_id: str) -> Optional[Dict[str, Any]]:
            raise RuntimeError("simulated_db_outage")

        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_OVERRIDES_JSON":
                '{"tenant-A": {"stt_eot_timeout_ms": 1234}}',
        }):
            resolver = VoiceTuningResolver()
            resolver.set_db_lookup(broken_lookup)
            with caplog.at_level("WARNING", logger="app.domain.services.voice_tuning"):
                result = await resolver.for_tenant_async("tenant-A")
            assert result.stt_eot_timeout_ms == 1234
            # Operators must see the failure in logs.
            assert any(
                "voice_tuning_db_lookup_failed" in r.getMessage()
                for r in caplog.records
            )


# ---------------------------------------------------------------------
# Coercion on the way in — malformed DB rows can't break the pipeline
# ---------------------------------------------------------------------


class TestDbPayloadCoercion:
    @pytest.mark.asyncio
    async def test_unknown_field_in_db_payload_is_dropped(self):
        resolver = VoiceTuningResolver()
        resolver.set_db_lookup(_async_lookup({
            "stt_eot_timeout_ms": 1500,
            "fictional_future_field": "anything",
        }))
        result = await resolver.for_tenant_async("tenant-A")
        # Real field landed; fictional one ignored, no exception.
        assert result.stt_eot_timeout_ms == 1500

    @pytest.mark.asyncio
    async def test_wrong_type_in_db_payload_falls_back_to_default(self):
        resolver = VoiceTuningResolver()
        resolver.set_db_lookup(_async_lookup({
            "stt_eot_timeout_ms": "not-a-number",  # bogus
            "turn_0_min_confidence": 0.55,
        }))
        result = await resolver.for_tenant_async("tenant-A")
        assert result.stt_eot_timeout_ms == 500   # fell back to default
        assert result.turn_0_min_confidence == 0.55  # other field intact

    @pytest.mark.asyncio
    async def test_eager_threshold_can_be_disabled_via_null(self):
        resolver = VoiceTuningResolver()
        resolver.set_db_lookup(_async_lookup({"stt_eager_eot_threshold": None}))
        result = await resolver.for_tenant_async("tenant-A")
        assert result.stt_eager_eot_threshold is None


# ---------------------------------------------------------------------
# coerce_user_partial — public validator for /POST /config writes
# ---------------------------------------------------------------------


class TestCoerceUserPartial:
    """Validation entry point for the API endpoint. Same coercion the
    DB and env paths use, exposed publicly so the endpoint doesn't
    need to know about ``_coerce_partial``."""

    def test_drops_unknown_keys(self):
        resolver = VoiceTuningResolver()
        result = resolver.coerce_user_partial({
            "stt_eot_timeout_ms": 1500,
            "ignore_me": "x",
        })
        assert result == {"stt_eot_timeout_ms": 1500}

    def test_drops_wrong_typed_values_keeps_valid_ones(self):
        resolver = VoiceTuningResolver()
        result = resolver.coerce_user_partial({
            "stt_eot_timeout_ms": "not-a-number",
            "turn_0_min_confidence": 0.55,
        })
        assert result == {"turn_0_min_confidence": 0.55}

    def test_empty_input_returns_empty(self):
        resolver = VoiceTuningResolver()
        assert resolver.coerce_user_partial({}) == {}


# ---------------------------------------------------------------------
# Cache-bypass — DB edits land on the very next call
# ---------------------------------------------------------------------


class TestCacheBypass:
    @pytest.mark.asyncio
    async def test_db_value_change_takes_effect_immediately(self):
        """Operators editing voice tuning in the UI expect the change
        to land on the next call, not after a restart. The DB layer
        does NOT cache between calls — assert this contract."""
        latest = {"stt_eot_timeout_ms": 1000}

        async def changing_lookup(_tenant_id: str) -> Optional[Dict[str, Any]]:
            return dict(latest)

        resolver = VoiceTuningResolver()
        resolver.set_db_lookup(changing_lookup)

        first = await resolver.for_tenant_async("tenant-A")
        assert first.stt_eot_timeout_ms == 1000

        # Operator edits the value in the UI — the DB now returns
        # something new on the next lookup. No cache invalidation
        # call required.
        latest["stt_eot_timeout_ms"] = 2500
        second = await resolver.for_tenant_async("tenant-A")
        assert second.stt_eot_timeout_ms == 2500
