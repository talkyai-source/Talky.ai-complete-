"""
Tests that telephony_bridge first-speaker default is 'agent'.
"""
import asyncio
import os
import pytest
from types import SimpleNamespace
from unittest.mock import patch


class TestOutboundFirstSpeaker:
    def test_default_is_agent(self):
        """TELEPHONY_FIRST_SPEAKER not set → default must be 'agent'."""
        from app.api.v1.endpoints.telephony_bridge import _outbound_first_speaker
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEPHONY_FIRST_SPEAKER", None)
            assert _outbound_first_speaker() == "agent"

    def test_env_override_user(self):
        """Setting TELEPHONY_FIRST_SPEAKER=user overrides the default."""
        from app.api.v1.endpoints.telephony_bridge import _outbound_first_speaker
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
            assert _outbound_first_speaker() == "user"

    def test_env_override_agent_explicit(self):
        """Setting TELEPHONY_FIRST_SPEAKER=agent returns 'agent'."""
        from app.api.v1.endpoints.telephony_bridge import _outbound_first_speaker
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "agent"}):
            assert _outbound_first_speaker() == "agent"

    def test_user_first_open_window_defaults_to_safety_net(self):
        """User-first fallback should not race normal caller pickup speech."""
        from app.api.v1.endpoints.telephony_bridge import _user_first_open_seconds
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEPHONY_USER_FIRST_OPEN_S", None)
            assert _user_first_open_seconds() == 5.0

    def test_user_first_open_window_clamps_subsecond_values(self):
        """A sub-second fallback opener recreates the first-turn delay."""
        from app.api.v1.endpoints.telephony_bridge import _user_first_open_seconds
        with patch.dict(os.environ, {"TELEPHONY_USER_FIRST_OPEN_S": "0.3"}):
            assert _user_first_open_seconds() == 2.0

    def test_user_first_fallback_disabled_by_default(self):
        """Caller-first mode must remain silent unless fallback is explicit."""
        from app.api.v1.endpoints.telephony_bridge import _user_first_fallback_enabled
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEPHONY_USER_FIRST_FALLBACK_ENABLED", None)
            assert _user_first_fallback_enabled() is False

    def test_user_first_fallback_opt_in(self):
        """Fallback greeting is available only as an explicit safety net."""
        from app.api.v1.endpoints.telephony_bridge import _user_first_fallback_enabled
        for value in ("1", "true", "yes", "on"):
            with patch.dict(os.environ, {"TELEPHONY_USER_FIRST_FALLBACK_ENABLED": value}):
                assert _user_first_fallback_enabled() is True

    def test_caller_first_prompt_is_inbound_style(self):
        """Caller-first must answer like an inbound call, not an outbound opener."""
        from app.api.v1.endpoints.telephony_bridge import (
            _apply_caller_first_inbound_prompt,
        )

        call_session = SimpleNamespace(
            system_prompt="BASE PROMPT",
            agent_config=SimpleNamespace(
                agent_name="Sarah",
                company_name="All States Estimation",
            ),
        )
        voice_session = SimpleNamespace(call_session=call_session)

        _apply_caller_first_inbound_prompt(voice_session)

        assert "CALLER-FIRST INBOUND MODE" in call_session.system_prompt
        assert "caller called us" in call_session.system_prompt
        assert "do not ask whether they have a minute" in call_session.system_prompt
        assert "Hi, this is Sarah from All States Estimation." in call_session.system_prompt

    def test_caller_first_prompt_is_idempotent(self):
        from app.api.v1.endpoints.telephony_bridge import (
            _apply_caller_first_inbound_prompt,
        )

        call_session = SimpleNamespace(
            system_prompt="BASE PROMPT",
            agent_config=SimpleNamespace(agent_name="Sarah", company_name="Acme"),
        )
        voice_session = SimpleNamespace(call_session=call_session)

        _apply_caller_first_inbound_prompt(voice_session)
        _apply_caller_first_inbound_prompt(voice_session)

        assert call_session.system_prompt.count("CALLER-FIRST INBOUND MODE") == 1

    def test_ringing_alias_moves_caller_first_prewarm_state(self):
        """Asterisk trunk channel IDs must consume the planned caller-first session."""
        from app.api.v1.endpoints import telephony_bridge as bridge

        original_call_id = "talky-out-planned"
        actual_call_id = "asterisk-trunk-actual"
        warmup = object()
        evt = asyncio.Event()

        try:
            bridge._ringing_warmups[original_call_id] = (warmup, None)
            bridge._ringing_warmup_created_at[original_call_id] = 123.0
            bridge._ringing_events[original_call_id] = evt

            bridge._alias_ringing_call_id(original_call_id, actual_call_id)

            assert original_call_id not in bridge._ringing_warmups
            assert original_call_id not in bridge._ringing_warmup_created_at
            assert original_call_id not in bridge._ringing_events
            assert bridge._ringing_warmups[actual_call_id] == (warmup, None)
            assert bridge._ringing_warmup_created_at[actual_call_id] == 123.0
            assert bridge._ringing_events[actual_call_id] is evt
        finally:
            bridge._ringing_warmups.pop(original_call_id, None)
            bridge._ringing_warmups.pop(actual_call_id, None)
            bridge._ringing_warmup_created_at.pop(original_call_id, None)
            bridge._ringing_warmup_created_at.pop(actual_call_id, None)
            bridge._ringing_events.pop(original_call_id, None)
            bridge._ringing_events.pop(actual_call_id, None)

    @pytest.mark.asyncio
    async def test_on_ringing_respects_reserved_call_id(self, monkeypatch):
        """A reserved caller-first event must block default agent-first warmup."""
        from app.domain.services.telephony import lifecycle

        call_id = "reserved-call"
        fake_bridge = SimpleNamespace(
            _adapter=SimpleNamespace(name="asterisk"),
            _ringing_warmups={},
            _ringing_events={call_id: asyncio.Event()},
            _telephony_sessions={},
            _MAX_TELEPHONY_SESSIONS=10,
        )

        monkeypatch.setattr(lifecycle, "_bridge", lambda: fake_bridge)
        monkeypatch.setattr(
            lifecycle,
            "_get_orchestrator",
            lambda: (_ for _ in ()).throw(AssertionError("must not warm up")),
        )

        await lifecycle._on_ringing(call_id)

        assert fake_bridge._ringing_warmups == {}
        assert call_id in fake_bridge._ringing_events

    @pytest.mark.asyncio
    async def test_asterisk_trunk_aliases_oldest_originated_channel(self):
        """Concurrent trunk calls should not depend on unordered set iteration."""
        from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

        adapter = AsteriskAdapter()
        aliases = []
        started = []

        adapter.set_outbound_channel_alias_callback(
            lambda original, actual: aliases.append((original, actual))
        )

        async def fake_outbound_start(channel_id):
            started.append(channel_id)

        adapter._on_outbound_stasis_start = fake_outbound_start
        adapter._track_originated_channel("planned-a")
        adapter._track_originated_channel("planned-b")

        await adapter._handle_ari_event({
            "type": "StasisStart",
            "channel": {
                "id": "actual-a",
                "name": "PJSIP/1002-00000001",
            },
            "args": [],
        })
        await asyncio.sleep(0)

        assert aliases == [("planned-a", "actual-a")]
        assert started == ["actual-a"]
        assert "planned-a" not in adapter._originated_channels
        assert "planned-b" in adapter._originated_channels
