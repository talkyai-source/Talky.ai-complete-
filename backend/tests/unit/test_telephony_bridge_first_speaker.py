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

    def test_caller_first_uses_same_greeting_path_as_agent_first(self):
        """Caller-first model is now: same greeting flow as agent-first,
        just delayed by 2 seconds. The retired silence safety net /
        predicted-response watcher / per-persona ack constants are gone.
        Verify the simplified module still exposes the LLM prewarm helper
        that the new-call lifecycle still depends on."""
        from app.domain.services.telephony.modes import user_first

        # The only public helper that survived simplification.
        assert hasattr(user_first, "prewarm_llm_pool")

        # Old machinery is gone — these names must NOT be importable
        # because that's what stops the dead code from quietly running.
        assert not hasattr(user_first, "_handle_user_first_silence")
        assert not hasattr(user_first, "_user_first_fallback_enabled")
        assert not hasattr(user_first, "_user_first_greet_on_pickup_enabled")
        assert not hasattr(user_first, "_user_first_open_seconds")

    def test_caller_first_prepends_directive_to_non_legacy_prompt(self):
        """Persona-composed and other custom prompts must receive a
        top-anchored inbound directive so the LLM picks up as the receiver.
        The persona's own body must remain intact below the directive."""
        from app.api.v1.endpoints.telephony_bridge import (
            _apply_caller_first_inbound_prompt,
        )
        from app.domain.services.telephony.modes.caller_first import (
            INBOUND_DIRECTIVE_SENTINEL,
        )

        persona_body = (
            "You are Sarah, a friendly customer support specialist at Acme.\n"
            "Listen carefully and answer questions about our products."
        )
        call_session = SimpleNamespace(
            system_prompt=persona_body,
            agent_config=SimpleNamespace(agent_name="Sarah", company_name="Acme"),
        )
        voice_session = SimpleNamespace(call_session=call_session, call_id="abc123")

        _apply_caller_first_inbound_prompt(voice_session)

        # Directive must land at position 0 to dominate early-token attention.
        assert call_session.system_prompt.startswith(INBOUND_DIRECTIVE_SENTINEL)
        # Persona body must survive verbatim below the directive.
        assert persona_body in call_session.system_prompt
        # Outbound opening must reference the campaign's actual values — the
        # agent introduces itself as "<agent> from <company>", not as a receiver.
        assert "Sarah from Acme" in call_session.system_prompt

    def test_caller_first_directive_prepend_is_idempotent(self):
        """Calling the prepend path twice must not double-prepend the
        directive — the sentinel guards against repeated injection."""
        from app.api.v1.endpoints.telephony_bridge import (
            _apply_caller_first_inbound_prompt,
        )
        from app.domain.services.telephony.modes.caller_first import (
            INBOUND_DIRECTIVE_SENTINEL,
        )

        persona_body = "You are Sarah at Acme. Be helpful."
        call_session = SimpleNamespace(
            system_prompt=persona_body,
            agent_config=SimpleNamespace(agent_name="Sarah", company_name="Acme"),
        )
        voice_session = SimpleNamespace(call_session=call_session, call_id="abc123")

        _apply_caller_first_inbound_prompt(voice_session)
        first = call_session.system_prompt
        _apply_caller_first_inbound_prompt(voice_session)

        assert call_session.system_prompt == first
        assert call_session.system_prompt.count(INBOUND_DIRECTIVE_SENTINEL) == 1

    def test_caller_first_handles_missing_call_session(self):
        """Defensive: teardown races can leave voice_session.call_session
        as None. The function must log-and-return rather than crash."""
        from app.api.v1.endpoints.telephony_bridge import (
            _apply_caller_first_inbound_prompt,
        )
        voice_session = SimpleNamespace(call_session=None, call_id="abc123")
        # Must not raise.
        _apply_caller_first_inbound_prompt(voice_session)

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
