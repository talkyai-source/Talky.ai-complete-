"""
Tests that telephony_bridge first-speaker default is 'agent'.
"""
import asyncio
import os
import pytest
from types import SimpleNamespace
from unittest.mock import patch


class TestOutboundFirstSpeaker:
    def test_ai_message_intake_fallback_never_promises_a_tone(self):
        from app.domain.services.telephony.lifecycle import (
            _AI_MESSAGE_INTAKE_FALLBACK_GREETING,
        )

        greeting = _AI_MESSAGE_INTAKE_FALLBACK_GREETING.lower()
        assert "tone" not in greeting
        assert "beep" not in greeting
        assert "name" in greeting and "number" in greeting and "message" in greeting

    def test_inbound_duration_must_match_the_pinned_reservation(self):
        from app.domain.services.telephony.lifecycle import (
            _pinned_inbound_max_duration,
        )

        payload = {
            "config_snapshot": {
                "route": {
                    "max_call_duration_seconds": 600,
                    "reservation_seconds": 600,
                }
            }
        }
        assert _pinned_inbound_max_duration(payload) == 600
        payload["config_snapshot"]["route"]["reservation_seconds"] = 60
        with pytest.raises(RuntimeError, match="quota-backed duration"):
            _pinned_inbound_max_duration(payload)

    @pytest.mark.asyncio
    async def test_inbound_deadline_marks_reason_and_schedules_forced_hangup(
        self,
        monkeypatch,
    ):
        from app.domain.services.telephony import lifecycle

        session = SimpleNamespace()
        forced = []
        tasks = []

        monkeypatch.setattr(
            lifecycle,
            "_state",
            lambda: SimpleNamespace(get_voice_session=lambda _call_id: session),
        )

        async def force(call_id):
            forced.append(call_id)

        def track(coro):
            task = asyncio.create_task(coro)
            tasks.append(task)
            return task

        monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force)
        monkeypatch.setattr(lifecycle, "_track_task", track)
        loop = asyncio.get_running_loop()
        await lifecycle._enforce_inbound_deadline(
            "inbound-1",
            60,
            loop.time() - 61,
        )
        await asyncio.gather(*tasks)

        assert session._hangup_reason == "inbound_max_duration_reached"
        assert forced == ["inbound-1"]

    def test_pinned_inbound_prompt_and_voice_reach_live_session_builder(self, monkeypatch):
        from app.domain.services.telephony import lifecycle

        captured = {}

        def build(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(system_prompt="Composed base prompt")

        monkeypatch.setattr(lifecycle, "_build_telephony_session_config", build)
        monkeypatch.setattr(
            lifecycle,
            "_pinned_inbound_ai_config",
            lambda _payload: ("pinned-ai", "pinned-tuning"),
        )
        config, pinned = lifecycle._build_pinned_inbound_config(
            {
                "opening_mode": "caller_first",
                "config_snapshot": {
                    "campaign": {
                        "id": "campaign-1",
                        "voice_id": "base-voice",
                        "script_config": {"persona_type": "lead_gen"},
                    },
                    "inbound_config": {
                        "opening_mode": "caller_first",
                        "greeting": "Thanks for calling.",
                        "qualification_config": {
                            "system_prompt": "Answer only approved support questions.",
                            "voice_id": "inbound-voice",
                        }
                    },
                }
            },
            gateway_type="telephony",
            selected_action="agent",
        )

        assert captured["campaign"] is pinned
        assert pinned["voice_id"] == "inbound-voice"
        assert pinned["script_config"]["persona_type"] == "lead_gen"
        assert (
            pinned["script_config"]["additional_instructions"]
            == "INBOUND-SPECIFIC INSTRUCTIONS\n"
            "Answer only approved support questions."
        )
        assert captured["ai_config_override"] == "pinned-ai"
        assert captured["voice_tuning_override"] == "pinned-tuning"
        assert config.system_prompt.startswith(lifecycle._TRUE_INBOUND_DIRECTIVE)

    @pytest.mark.parametrize(
        ("opening_mode", "expected"),
        [("caller_first", "user"), ("agent_first", "agent")],
    )
    def test_true_inbound_uses_pinned_opening_mode(self, opening_mode, expected):
        from app.domain.services.telephony.lifecycle import _pinned_inbound_opening

        first_speaker, greeting = _pinned_inbound_opening(
            {
                "opening_mode": opening_mode,
                "config_snapshot": {
                    "inbound_config": {
                        "opening_mode": opening_mode,
                        "greeting": "Thanks for calling Acme.",
                    }
                },
            }
        )
        assert first_speaker == expected
        assert greeting == "Thanks for calling Acme."

    def test_true_inbound_rejects_opening_mode_snapshot_mismatch(self):
        from app.domain.services.telephony.lifecycle import _pinned_inbound_opening

        with pytest.raises(RuntimeError, match="inconsistent opening_mode"):
            _pinned_inbound_opening(
                {
                    "opening_mode": "agent_first",
                    "config_snapshot": {
                        "inbound_config": {"opening_mode": "caller_first"}
                    },
                }
            )

    def test_inbound_greeting_uses_pinned_custom_text_and_outbound_is_unchanged(self):
        from app.domain.services.telephony.config import _build_call_greeting
        from app.domain.services.voice_orchestrator import Direction

        agent = SimpleNamespace(agent_name="Maya", company_name="Acme")
        inbound_call_session = SimpleNamespace(
            agent_config=agent,
            _call_direction="inbound",
            _inbound_greeting="Welcome to Acme support.",
            _llm_opener_text="outbound-only opener",
        )
        outbound_call_session = SimpleNamespace(
            agent_config=agent,
            config=SimpleNamespace(direction=Direction.OUTBOUND),
            _llm_opener_text="outbound-only opener",
        )
        assert _build_call_greeting(
            inbound_call_session, first_speaker="agent"
        ) == "Welcome to Acme support."
        assert _build_call_greeting(
            outbound_call_session, first_speaker="agent"
        ) == "outbound-only opener"

    @pytest.mark.parametrize(
        ("opening_mode", "expected"),
        [("caller_first", "user"), ("agent_first", "agent")],
    )
    def test_true_inbound_uses_pinned_opening_mode(self, opening_mode, expected):
        from app.domain.services.telephony.lifecycle import _pinned_inbound_opening

        first_speaker, greeting = _pinned_inbound_opening(
            {
                "opening_mode": opening_mode,
                "config_snapshot": {
                    "inbound_config": {
                        "opening_mode": opening_mode,
                        "greeting": "Thanks for calling Acme.",
                    }
                },
            }
        )
        assert first_speaker == expected
        assert greeting == "Thanks for calling Acme."

    def test_true_inbound_rejects_opening_mode_snapshot_mismatch(self):
        from app.domain.services.telephony.lifecycle import _pinned_inbound_opening

        with pytest.raises(RuntimeError, match="inconsistent opening_mode"):
            _pinned_inbound_opening(
                {
                    "opening_mode": "agent_first",
                    "config_snapshot": {
                        "inbound_config": {"opening_mode": "caller_first"}
                    },
                }
            )

    def test_inbound_greeting_uses_pinned_custom_text_and_outbound_is_unchanged(self):
        from app.domain.services.telephony.config import _build_call_greeting
        from app.domain.services.voice_orchestrator import Direction

        agent = SimpleNamespace(agent_name="Maya", company_name="Acme")
        inbound = SimpleNamespace(
            agent_config=agent,
            config=SimpleNamespace(direction=Direction.INBOUND),
            _inbound_greeting="Welcome to Acme support.",
            _llm_opener_text="outbound-only opener",
        )
        outbound = SimpleNamespace(
            agent_config=agent,
            config=SimpleNamespace(direction=Direction.OUTBOUND),
            _llm_opener_text="outbound-only opener",
        )
        assert _build_call_greeting(inbound, first_speaker="agent") == "Welcome to Acme support."
        assert _build_call_greeting(outbound, first_speaker="agent") == "outbound-only opener"

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

    @pytest.mark.asyncio
    async def test_ringing_alias_moves_caller_first_prewarm_state(self):
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

            await bridge._alias_ringing_call_id(original_call_id, actual_call_id)

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
        fake_adapter = SimpleNamespace(name="asterisk")
        # Model the "reserved" condition directly on the state backend
        # (get_ringing_event returning non-None means a caller-first
        # prewarm already reserved this call_id) rather than relying on
        # the real process-global telephony_bridge dicts, which other
        # tests in the suite also mutate.
        fake_state = SimpleNamespace(
            has_ringing_warmup=lambda _cid: False,
            get_ringing_event=lambda _cid: object(),
            get_voice_session=lambda _cid: None,
        )

        monkeypatch.setattr(lifecycle, "get_adapter", lambda: fake_adapter)
        monkeypatch.setattr(lifecycle, "_state", lambda: fake_state)
        monkeypatch.setattr(lifecycle, "_MAX_TELEPHONY_SESSIONS", 10)
        monkeypatch.setattr(
            lifecycle,
            "_get_orchestrator",
            lambda: (_ for _ in ()).throw(AssertionError("must not warm up")),
        )

        # Must return early (idempotent/reserved) without ever reaching
        # the orchestrator warmup path — the AssertionError-raising lambda
        # above would propagate out of this call if it were.
        await lifecycle._on_ringing(call_id)

    @pytest.mark.asyncio
    async def test_asterisk_trunk_aliases_linked_origination_not_fifo(self):
        """Concurrent trunk calls are paired by linkedid, never FIFO order."""
        from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

        adapter = AsteriskAdapter()
        aliases = []
        started = []

        adapter.set_outbound_channel_alias_callback(
            lambda original, actual: aliases.append((original, actual))
        )

        async def fake_outbound_start(channel_id):
            started.append(channel_id)

        async def fake_ari(method, path, **kwargs):
            assert method == "GET"
            assert path == "/channels/actual-a/variable"
            return {"value": "planned-b"}

        adapter._ari = fake_ari
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

        assert aliases == [("planned-b", "actual-a")]
        assert started == ["actual-a"]
        assert "planned-b" not in adapter._originated_channels
        assert "planned-a" in adapter._originated_channels
