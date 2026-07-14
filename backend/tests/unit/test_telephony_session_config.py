"""
Unit tests for telephony_session_config module.
Tests the estimation agent config, greeting builder, and session config builder.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestAgentNames:
    def test_agent_names_pool_has_at_least_ten_names(self):
        from app.domain.services.telephony_session_config import AGENT_NAMES
        assert len(AGENT_NAMES) >= 10

    def test_all_names_are_non_empty_strings(self):
        from app.domain.services.telephony_session_config import AGENT_NAMES
        for name in AGENT_NAMES:
            assert isinstance(name, str) and len(name) > 0


class TestBuildTelephonyGreeting:
    """
    Consent-first opener contract (2026-04-22): the greeting introduces
    the agent by name and asks permission to continue. Company name and
    pitch are deferred to the system prompt's GREETING RESPONSE flow,
    which fires only after the callee agrees.
    """

    def test_greeting_contains_agent_name(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("John", "All States Estimation")
        assert "John" in result

    def test_greeting_asks_for_permission(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        # Sample many variants — every one must ask for time and end with "?".
        for _ in range(30):
            result = build_telephony_greeting("John", "All States Estimation")
            lower = result.lower()
            assert (
                "minute" in lower
                or "moment" in lower
                or "second" in lower
            ), f"Opener must ask for time: {result!r}"
            assert result.rstrip().endswith("?"), (
                f"Opener must end with a question: {result!r}"
            )

    def test_greeting_does_not_mention_company(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("John", "All States Estimation")
        assert "All States Estimation" not in result, (
            "Company name must be deferred until the callee agrees"
        )

    def test_greeting_does_not_pitch(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("Sarah", "TestCo")
        lower = result.lower()
        for pitch_word in ("estimate", "bidding", "takeoff", "contractor", "cold call"):
            assert pitch_word not in lower, (
                f"Opener must not pitch — found '{pitch_word}' in: {result!r}"
            )

    def test_greeting_is_short(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("Alex", "TestCo")
        assert len(result) < 80, f"Opener must be short; got {len(result)} chars"

    def test_greeting_is_a_non_empty_string(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("Alex", "TestCo")
        assert isinstance(result, str) and len(result) > 0


class TestBuildTelephonyInboundGreeting:
    """The inbound greeting answers like a real receptionist picking up
    the phone — company name first (so the caller knows they reached the
    right place), then the agent."""

    def test_inbound_greeting_names_company_first(self):
        from app.domain.services.telephony_session_config import (
            build_telephony_inbound_greeting,
        )
        result = build_telephony_inbound_greeting("Adam", "All States Estimation")
        # Company should appear before the agent in the line, the way a
        # real person picks up a phone.
        company_idx = result.find("All States Estimation")
        agent_idx = result.find("Adam")
        assert 0 <= company_idx < agent_idx

    def test_inbound_greeting_offers_help(self):
        """The hallmark of a receiver opener is offering help —
        outbound openers don't do that on turn 0. Sample many variants."""
        from app.domain.services.telephony_session_config import (
            build_telephony_inbound_greeting,
        )
        for _ in range(30):
            result = build_telephony_inbound_greeting("Sarah", "Acme Co")
            lower = result.lower()
            assert (
                "how can i help" in lower
                or "what can i do" in lower
            ), f"inbound variant must offer help: {result!r}"

    def test_inbound_greeting_does_not_ask_for_a_minute(self):
        """The outbound consent-first opener asks for a minute. The
        inbound opener must not — they called us."""
        from app.domain.services.telephony_session_config import (
            build_telephony_inbound_greeting,
        )
        for _ in range(30):
            result = build_telephony_inbound_greeting("Sarah", "Acme Co")
            assert "minute" not in result.lower()


class TestMuteDuringTtsDefault:
    """Echo suppression defaults: barge-in must keep working unless an
    operator explicitly opts into the mute-during-TTS trade-off."""

    def test_default_off(self):
        from app.domain.services.telephony_session_config import (
            _telephony_mute_during_tts_default,
        )
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("TELEPHONY_MUTE_DURING_TTS", None)
            assert _telephony_mute_during_tts_default() is False

    def test_explicit_opt_in(self):
        from app.domain.services.telephony_session_config import (
            _telephony_mute_during_tts_default,
        )
        for value in ("1", "true", "yes", "on", "TRUE", "On"):
            with patch.dict("os.environ", {"TELEPHONY_MUTE_DURING_TTS": value}):
                assert _telephony_mute_during_tts_default() is True

    def test_falsy_values_stay_off(self):
        from app.domain.services.telephony_session_config import (
            _telephony_mute_during_tts_default,
        )
        for value in ("0", "false", "no", "off", "", "garbage"):
            with patch.dict("os.environ", {"TELEPHONY_MUTE_DURING_TTS": value}):
                assert _telephony_mute_during_tts_default() is False


class TestDirectionEnum:
    """The Direction enum is the typed contract that replaced regex
    sniffing. Locking the enum surface here means downstream code can
    compare against either the enum value or its string form."""

    def test_values_are_lowercase_strings(self):
        from app.domain.services.voice_orchestrator import Direction
        # Lowercase string values are required for log/OTel attribute
        # cleanliness — no manual conversion needed at every emit site.
        assert Direction.OUTBOUND.value == "outbound"
        assert Direction.INBOUND.value == "inbound"

    def test_string_comparison_works(self):
        """String-backed enum so comparing with a plain string works.
        This keeps the migration low-risk for code that hasn't been
        updated to import the enum yet."""
        from app.domain.services.voice_orchestrator import Direction
        assert Direction.INBOUND == "inbound"
        assert Direction.OUTBOUND == "outbound"

    def test_from_first_speaker_user_maps_to_inbound(self):
        from app.domain.services.voice_orchestrator import Direction
        assert Direction.from_first_speaker("user") == Direction.INBOUND
        assert Direction.from_first_speaker("USER") == Direction.INBOUND
        assert Direction.from_first_speaker("  user  ") == Direction.INBOUND

    def test_from_first_speaker_agent_maps_to_outbound(self):
        from app.domain.services.voice_orchestrator import Direction
        assert Direction.from_first_speaker("agent") == Direction.OUTBOUND

    def test_from_first_speaker_unknown_defaults_to_outbound(self):
        """Defensive: malformed input must not crash the call setup.
        Outbound is the safer fallback because that's the historical
        default every existing campaign has been running with."""
        from app.domain.services.voice_orchestrator import Direction
        assert Direction.from_first_speaker(None) == Direction.OUTBOUND
        assert Direction.from_first_speaker("") == Direction.OUTBOUND
        assert Direction.from_first_speaker("garbage") == Direction.OUTBOUND


class TestBuildTelephonySessionConfigDirection:
    """A campaign-less / persona-less call now composes through the single
    layered persona system (knowledge-driven lead_gen) — the legacy hardcoded
    estimation prompt was retired. INBOUND calls get the canonical inbound
    directive at compose time so the LLM never sees outbound framing."""

    def test_outbound_campaign_less_uses_lead_gen_prompt(self):
        from app.domain.services.voice_orchestrator import Direction
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
            TELEPHONY_COMPANY_NAME,
        )
        cfg = build_telephony_session_config(
            gateway_type="telephony",
            campaign=None,
            direction=Direction.OUTBOUND,
        )
        assert cfg.direction == Direction.OUTBOUND
        # No hardcoded fallback anymore — defaults to lead_gen, composed via
        # the layered persona system (guardrails always present).
        assert cfg.persona_type == "lead_gen"
        assert "HARD RULES" in cfg.system_prompt
        assert TELEPHONY_COMPANY_NAME in cfg.system_prompt
        # Retired legacy markers must be gone for good.
        assert "Business Development Specialist" not in cfg.system_prompt
        assert "GREETING RESPONSE" not in cfg.system_prompt

    def test_inbound_uses_inbound_prompt(self):
        from app.domain.services.voice_orchestrator import Direction
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        from app.domain.services.telephony.modes.caller_first import (
            INBOUND_DIRECTIVE_SENTINEL,
        )
        cfg = build_telephony_session_config(
            gateway_type="telephony",
            campaign=None,
            direction=Direction.INBOUND,
        )
        assert cfg.direction == Direction.INBOUND
        # Inbound sentinel leads the prompt — the LLM weights this most.
        assert cfg.system_prompt.startswith(INBOUND_DIRECTIVE_SENTINEL)
        # Outbound persona markers are NOT in the inbound base prompt.
        assert "Business Development Specialist" not in cfg.system_prompt
        assert "GREETING RESPONSE" not in cfg.system_prompt

    def test_default_direction_is_outbound(self):
        """Backward-compat: callers that don't specify direction get
        the historical outbound behaviour."""
        from app.domain.services.voice_orchestrator import Direction
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        cfg = build_telephony_session_config(
            gateway_type="telephony", campaign=None,
        )
        assert cfg.direction == Direction.OUTBOUND


class TestBuildPersonaGreeting:
    """Per-persona × direction TTS opener (T4-A2).

    Each persona has multiple short variants picked randomly per call so
    consecutive dials don't sound identical. Missing combinations fall
    back to the generic builders.

    These tests deliberately sample many calls and check that:
    * every variant contains the agent_name and company_name slots
    * every variant is short and conversational
    rather than pinning a single exact string.
    """

    def test_lead_gen_outbound_has_sales_energy(self):
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
        )
        # Sample many times — random.choice should produce all variants.
        seen = set()
        for _ in range(50):
            out = build_persona_greeting(
                persona_type="lead_gen",
                agent_name="Adam",
                company_name="Acme",
                direction="outbound",
            )
            assert "Adam" in out
            assert "Acme" in out
            # Outbound openers should ask for time (conversational, not a pitch).
            lower = out.lower()
            assert (
                "second" in lower
                or "minute" in lower
                or "moment" in lower
            ), f"variant should ask for time: {out!r}"
            seen.add(out)
        # We have multiple variants — random.choice should hit at least 2 in 50 samples.
        assert len(seen) >= 2, f"expected variant rotation, got only: {seen}"

    def test_lead_gen_inbound_thanks_for_reaching_out(self):
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
        )
        # At least one lead-gen inbound variant should thank for reaching out.
        seen_thanks = False
        for _ in range(50):
            out = build_persona_greeting(
                persona_type="lead_gen",
                agent_name="Adam",
                company_name="Acme",
                direction="inbound",
            )
            assert "Adam" in out
            if "thanks for reaching out" in out.lower():
                seen_thanks = True
        assert seen_thanks, "lead_gen inbound should have a 'thanks for reaching out' variant"

    def test_customer_support_inbound_thanks_for_calling(self):
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
        )
        # At least one customer_support inbound variant should thank for calling.
        seen_thanks = False
        for _ in range(50):
            out = build_persona_greeting(
                persona_type="customer_support",
                agent_name="Sam",
                company_name="Acme",
                direction="inbound",
            )
            assert "Sam" in out
            if "Thanks for calling Acme" in out:
                seen_thanks = True
        assert seen_thanks, "customer_support inbound should have a 'Thanks for calling' variant"

    def test_customer_support_outbound_callback_framing(self):
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
        )
        # Sample many — at least one variant should reference the prior
        # inquiry / follow-up to feel like a real callback rather than a
        # cold dial. Every variant must include agent + company + support
        # framing.
        seen_callback_framing = False
        for _ in range(50):
            out = build_persona_greeting(
                persona_type="customer_support",
                agent_name="Sam",
                company_name="Acme",
                direction="outbound",
            )
            assert "Sam" in out
            assert "Acme" in out
            lower = out.lower()
            # Must NOT use the lead-gen "got a quick second" pattern.
            if "recent inquiry" in lower or "follow-up" in lower or "follow up" in lower:
                seen_callback_framing = True
        assert seen_callback_framing, (
            "at least one customer_support outbound variant should sound "
            "like a callback (recent inquiry / follow-up)"
        )

    def test_receptionist_inbound_warm(self):
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
        )
        # Sample many — every variant must mention agent + company.
        for _ in range(30):
            out = build_persona_greeting(
                persona_type="receptionist",
                agent_name="Maya",
                company_name="Acme",
                direction="inbound",
            )
            assert "Acme" in out
            assert "Maya" in out

    def test_receptionist_outbound_followup(self):
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
        )
        for _ in range(30):
            out = build_persona_greeting(
                persona_type="receptionist",
                agent_name="Maya",
                company_name="Acme",
                direction="outbound",
            )
            assert "Maya" in out
            assert "Acme" in out
            # Receptionist outbound is a follow-up — must NOT use the
            # inbound "Thank you for calling" framing.
            assert "Thank you for calling" not in out

    def test_unknown_persona_falls_back_to_generic(self):
        """Legacy estimation campaign has no persona — must still
        produce a grammatical greeting via the generic fallback."""
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
            build_telephony_greeting,
            build_telephony_inbound_greeting,
        )
        # Variant rotation makes == comparisons flaky — pin random.choice
        # to always pick the first item so both calls return identical strings.
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            out_outbound = build_persona_greeting(
                persona_type=None,
                agent_name="Alex",
                company_name="Acme",
                direction="outbound",
            )
            assert out_outbound == build_telephony_greeting("Alex", "Acme")

            out_inbound = build_persona_greeting(
                persona_type=None,
                agent_name="Alex",
                company_name="Acme",
                direction="inbound",
            )
            assert out_inbound == build_telephony_inbound_greeting("Alex", "Acme")

    def test_unknown_direction_falls_back_to_outbound_generic(self):
        """A typo in direction must not crash the call. Fallback to
        the outbound generic greeting matches the historical default."""
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
            build_telephony_greeting,
        )
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            out = build_persona_greeting(
                persona_type="lead_gen",
                agent_name="Alex",
                company_name="Acme",
                direction="sideways",
            )
            # 'sideways' isn't 'inbound' so the fallback path treats it as outbound.
            assert out == build_telephony_greeting("Alex", "Acme")

    def test_empty_direction_falls_back_to_outbound(self):
        from app.domain.services.telephony_session_config import (
            build_persona_greeting,
            build_telephony_greeting,
        )
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            out = build_persona_greeting(
                persona_type=None,
                agent_name="Alex",
                company_name="Acme",
                direction="",
            )
            assert out == build_telephony_greeting("Alex", "Acme")


class TestBuildCallGreetingPersonaDispatch:
    """``_build_call_greeting`` reads persona_type off ``session.config``
    and dispatches via build_persona_greeting. Tests the integration
    rather than the dispatch — the unit tests above cover dispatch."""

    def _session(
        self, *, agent_name="Alex", company_name="Acme", persona_type=None,
    ):
        from types import SimpleNamespace
        config = SimpleNamespace(persona_type=persona_type)
        return SimpleNamespace(
            agent_config=SimpleNamespace(
                agent_name=agent_name, company_name=company_name,
            ),
            config=config,
        )

    def test_persona_aware_outbound(self):
        from app.domain.services.telephony.config import _build_call_greeting
        session = self._session(agent_name="Adam", persona_type="lead_gen")
        # Sample many — every variant must reference Adam + Acme.
        for _ in range(30):
            out = _build_call_greeting(session, first_speaker="agent")
            assert "Adam" in out and "Acme" in out

    def test_caller_first_uses_outbound_greeting(self):
        """Caller-first OUTBOUND calls must STILL use the outbound
        greeting — we dialed them, even though the AI pauses 2s before
        speaking. The previous code mapped first_speaker=user to the
        inbound 'how can I help' opener, which sounded wrong because
        the callee had not initiated the call."""
        from app.domain.services.telephony.config import _build_call_greeting
        session = self._session(persona_type="customer_support")
        for _ in range(30):
            out = _build_call_greeting(session, first_speaker="user")
            # Must NOT use the inbound "Thanks for calling" / "How can I help" opener.
            assert "Thanks for calling" not in out
            # Must contain the company + agent — agent introduces themselves
            # like a real outbound caller.
            assert "Acme" in out and "Alex" in out

    def test_no_config_falls_back_gracefully(self):
        """Some legacy / browser sessions don't carry config at all.
        The dispatcher must read None safely and fall back."""
        from types import SimpleNamespace
        from app.domain.services.telephony.config import _build_call_greeting
        from app.domain.services.telephony_session_config import (
            build_telephony_greeting,
        )
        session = SimpleNamespace(
            agent_config=SimpleNamespace(
                agent_name="Alex", company_name="Acme",
            ),
            # no .config attribute at all
        )
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            out = _build_call_greeting(session, first_speaker="agent")
            assert out == build_telephony_greeting("Alex", "Acme")


class TestBuildCallGreeting:
    """The mode-aware dispatcher used by the pre-synth path.

    Bridge calls are ALWAYS outbound (we dialed) — the greeting is
    always the outbound greeting regardless of first_speaker. The
    first_speaker flag only controls TIMING (immediate vs 2s pause)
    in the lifecycle layer, not greeting content."""

    def _session(self, agent_name: str = "Sarah", company_name: str = "Acme"):
        from types import SimpleNamespace
        return SimpleNamespace(
            agent_config=SimpleNamespace(
                agent_name=agent_name, company_name=company_name,
            )
        )

    def test_user_first_uses_outbound(self):
        """Caller-first OUTBOUND must use the outbound greeting — we
        dialed them, so the AI introduces itself like any caller."""
        from app.domain.services.telephony.config import _build_call_greeting
        from app.domain.services.telephony_session_config import (
            build_telephony_greeting,
        )
        session = self._session()
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            result = _build_call_greeting(session, first_speaker="user")
            assert result == build_telephony_greeting("Sarah", "Acme")

    def test_agent_first_picks_outbound(self):
        from app.domain.services.telephony.config import _build_call_greeting
        from app.domain.services.telephony_session_config import (
            build_telephony_greeting,
        )
        session = self._session()
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            result = _build_call_greeting(session, first_speaker="agent")
            assert result == build_telephony_greeting("Sarah", "Acme")

    def test_unknown_first_speaker_defaults_to_outbound(self):
        """Defensive: a typo or future enum value must not crash. Falling
        back to outbound matches the historical behaviour for safety."""
        from app.domain.services.telephony.config import _build_call_greeting
        from app.domain.services.telephony_session_config import (
            build_telephony_greeting,
        )
        session = self._session()
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            result = _build_call_greeting(session, first_speaker="something_else")
            assert result == build_telephony_greeting("Sarah", "Acme")


class TestBuildTelephonySessionConfig:
    def _mock_global_config(self):
        cfg = MagicMock()
        cfg.tts_provider = "cartesia"
        cfg.tts_voice_id = "test-voice-id"
        cfg.tts_model = "sonic-3"
        cfg.llm_model = "llama-3.1-8b-instant"
        cfg.llm_temperature = 0.6
        cfg.llm_max_tokens = 90
        return cfg

    def test_returns_voice_session_config(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        from app.domain.services.voice_orchestrator import VoiceSessionConfig
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert isinstance(config, VoiceSessionConfig)

    def test_session_type_is_telephony(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.session_type == "telephony"

    def test_agent_name_is_set_and_non_empty(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config, AGENT_NAMES
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.agent_config.agent_name in AGENT_NAMES

    def test_agent_name_appears_in_system_prompt(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.agent_config.agent_name in config.system_prompt

    def test_company_name_appears_in_system_prompt(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config, TELEPHONY_COMPANY_NAME
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert TELEPHONY_COMPANY_NAME in config.system_prompt

    def test_uses_global_config_voice(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        mock_cfg = self._mock_global_config()
        mock_cfg.tts_voice_id = "my-custom-voice"
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=mock_cfg,
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.voice_id == "my-custom-voice"

    def test_gateway_type_browser_is_respected(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="browser")
        assert config.gateway_type == "browser"

    def test_two_calls_may_get_different_names(self):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            names = {
                build_telephony_session_config(gateway_type="telephony").agent_config.agent_name
                for _ in range(50)
            }
        assert len(names) > 1

    def test_persona_campaign_missing_required_slots_fails_closed_by_default(self, monkeypatch):
        from app.domain.services.telephony_session_config import build_telephony_session_config
        from app.services.scripts.prompts import PromptCompositionError

        campaign = {
            "id": "bad-campaign",
            "script_config": {
                "persona_type": "lead_gen",
                "company_name": "Acme",
                "agent_names": ["Alex"],
                "campaign_slots": {
                    "industry": "roofing",
                    # pricing_info intentionally missing
                },
            },
        }

        monkeypatch.delenv("TELEPHONY_PROMPT_STRICT_MODE", raising=False)
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            with pytest.raises(PromptCompositionError):
                build_telephony_session_config(campaign=campaign)

    def test_persona_missing_slots_non_strict_retries_knowledge_driven(self, monkeypatch):
        """Non-strict mode: a slot-based persona with incomplete slots no
        longer drops to a hardcoded legacy script — it retries the SAME
        persona in knowledge-driven (slot-free) mode, which always composes."""
        from app.domain.services.telephony_session_config import build_telephony_session_config

        campaign = {
            "id": "incomplete-slots-campaign",
            "script_config": {
                "persona_type": "lead_gen",
                "company_name": "Acme",
                "agent_names": ["Alex"],
                "campaign_slots": {
                    "industry": "roofing",
                    # required slots intentionally missing
                },
            },
        }

        monkeypatch.setenv("TELEPHONY_PROMPT_STRICT_MODE", "0")
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(campaign=campaign)

        assert config.persona_type == "lead_gen"
        assert "Acme" in config.system_prompt
        assert "Alex" in config.system_prompt
        assert "HARD RULES" in config.system_prompt
        # Retired legacy markers must never reappear.
        assert "Business Development Specialist" not in config.system_prompt
        assert "GREETING RESPONSE" not in config.system_prompt


class TestTenantPromptCap:
    """Uncapped tenant-authored additional_instructions (campaign ROLE/GOAL
    text) was observed at ~2.9k-7k tokens in production, injected into EVERY
    turn's system prompt. The cap bounds it before composition, truncating on
    a whitespace boundary and logging a WARNING with before/after size —
    normal, small prompts must pass through completely untouched."""

    _LOGGER_NAME = "app.domain.services.telephony_session_config"

    def _mock_global_config(self):
        cfg = MagicMock()
        cfg.tts_provider = "cartesia"
        cfg.tts_voice_id = "test-voice-id"
        cfg.tts_model = "sonic-3"
        cfg.llm_model = "llama-3.1-8b-instant"
        cfg.llm_temperature = 0.6
        cfg.llm_max_tokens = 90
        return cfg

    # ── unit tests on the helper directly ────────────────────────────────

    def test_default_budget_is_6000_chars(self):
        from app.domain.services.telephony_session_config import _tenant_prompt_char_budget
        assert _tenant_prompt_char_budget() == 6000

    def test_budget_is_env_overridable(self, monkeypatch):
        from app.domain.services.telephony_session_config import _tenant_prompt_char_budget
        monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "1234")
        assert _tenant_prompt_char_budget() == 1234

    def test_invalid_env_value_falls_back_to_default(self, monkeypatch):
        from app.domain.services.telephony_session_config import _tenant_prompt_char_budget
        monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "not-a-number")
        assert _tenant_prompt_char_budget() == 6000

    def test_none_and_empty_text_pass_through_untouched(self):
        from app.domain.services.telephony_session_config import (
            _cap_tenant_additional_instructions,
        )
        assert _cap_tenant_additional_instructions(None) is None
        assert _cap_tenant_additional_instructions("") == ""

    def test_small_prompt_is_untouched_and_does_not_warn(self, caplog, monkeypatch):
        import logging
        from app.domain.services.telephony_session_config import (
            _cap_tenant_additional_instructions,
        )
        monkeypatch.delenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", raising=False)
        text = "Ask about their roofing needs and offer a free estimate."
        caplog.set_level(logging.WARNING, logger=self._LOGGER_NAME)
        result = _cap_tenant_additional_instructions(text, campaign_id="c1")
        assert result == text
        assert not any(
            "telephony_tenant_prompt_capped" in r.message for r in caplog.records
        )

    def test_oversized_prompt_is_capped_at_word_boundary_and_warns(
        self, caplog, monkeypatch
    ):
        import logging
        from app.domain.services.telephony_session_config import (
            _cap_tenant_additional_instructions,
        )
        monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "50")
        text = "word " * 20  # 100 chars, well over the 50-char budget
        caplog.set_level(logging.WARNING, logger=self._LOGGER_NAME)
        result = _cap_tenant_additional_instructions(text, campaign_id="c2")

        assert len(result) <= 50
        # Boundary-safe: the capped text must be a clean prefix ending on a
        # whole word, never a word severed mid-token.
        assert text.startswith(result)
        assert not result.endswith("wor")

        warnings = [
            r for r in caplog.records if "telephony_tenant_prompt_capped" in r.message
        ]
        assert len(warnings) == 1
        assert "original_chars=100" in warnings[0].message
        assert "campaign=c2" in warnings[0].message
        assert "budget_chars=50" in warnings[0].message

    def test_seven_thousand_token_prompt_is_capped_and_logged(self, caplog):
        """~7000 tokens (~28,000 chars at ~4 chars/token) — the high end of
        the measured production range — must be capped to the default
        budget and logged with the original vs. capped size."""
        import logging
        from app.domain.services.telephony_session_config import (
            _cap_tenant_additional_instructions,
        )
        huge = "Please always mention our financing options and warranty terms. " * 400
        assert len(huge) > 20000
        caplog.set_level(logging.WARNING, logger=self._LOGGER_NAME)
        result = _cap_tenant_additional_instructions(huge, campaign_id="runaway")

        assert len(result) <= 6000
        warnings = [
            r for r in caplog.records if "telephony_tenant_prompt_capped" in r.message
        ]
        assert warnings
        assert f"original_chars={len(huge)}" in warnings[0].message

    # ── integration through build_telephony_session_config ────────────────

    def test_oversized_campaign_prompt_capped_in_composed_system_prompt(
        self, monkeypatch, caplog
    ):
        import logging
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "500")
        huge = (
            "Mention our roofing warranty and financing plan every single "
            "time you speak. "
        ) * 50
        assert len(huge) > 500
        campaign = {
            "id": "cap-integration-campaign",
            "script_config": {
                "persona_type": "lead_gen",
                "knowledge_driven": True,
                "company_name": "Acme",
                "agent_names": ["Alex"],
                "additional_instructions": huge,
            },
        }
        caplog.set_level(logging.WARNING, logger=self._LOGGER_NAME)
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(campaign=campaign)

        assert huge not in config.system_prompt
        warnings = [
            r for r in caplog.records if "telephony_tenant_prompt_capped" in r.message
        ]
        assert warnings

    def test_small_campaign_prompt_is_unaffected(self, monkeypatch, caplog):
        import logging
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        monkeypatch.delenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", raising=False)
        small = "Offer a free roof inspection and ask for a callback time."
        campaign = {
            "id": "normal-campaign",
            "script_config": {
                "persona_type": "lead_gen",
                "knowledge_driven": True,
                "company_name": "Acme",
                "agent_names": ["Alex"],
                "additional_instructions": small,
            },
        }
        caplog.set_level(logging.WARNING, logger=self._LOGGER_NAME)
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._mock_global_config(),
        ):
            config = build_telephony_session_config(campaign=campaign)

        assert small in config.system_prompt
        warnings = [
            r for r in caplog.records if "telephony_tenant_prompt_capped" in r.message
        ]
        assert not warnings


class TestPipelineModeThreading:
    """The realtime pipeline_mode + its knobs must survive from the tenant
    AI-Options config (and optional per-campaign override) all the way onto the
    VoiceSessionConfig that create_voice_session branches on. Without this wire,
    a real telephony call always runs cascaded and realtime never engages."""

    def _real_global_config(self, **overrides):
        # A REAL AIProviderConfig (not a MagicMock) so getattr defaults resolve
        # correctly — MagicMock auto-creates every attribute and would mask the
        # "cascaded" default.
        from app.domain.models.ai_config import AIProviderConfig
        return AIProviderConfig(**overrides)

    def test_default_global_config_is_cascaded(self):
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._real_global_config(),
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.pipeline_mode == "cascaded"
        # Realtime knobs carry their model defaults but are inert when cascaded.
        assert config.realtime_model == "gpt-realtime-2"
        assert config.realtime_voice == "marin"
        assert config.realtime_settings is None

    def test_tenant_global_config_realtime_threads_through(self):
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        gcfg = self._real_global_config(
            pipeline_mode="realtime",
            realtime_model="gpt-realtime-2",
            realtime_voice="cedar",
            realtime_settings={"turn_detection": "high", "noise_reduction": "far_field"},
        )
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=gcfg,
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.pipeline_mode == "realtime"
        assert config.realtime_voice == "cedar"
        assert config.realtime_model == "gpt-realtime-2"
        assert config.realtime_settings == {
            "turn_detection": "high",
            "noise_reduction": "far_field",
        }

    def test_per_campaign_script_config_overrides_tenant_default(self):
        """A cascaded tenant can still run a SINGLE campaign on realtime by
        setting pipeline_mode on the campaign's script_config."""
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        campaign = {
            "id": "realtime-campaign",
            "script_config": {
                # knowledge-driven (no persona_type) so composition always works
                "company_name": "Acme",
                "agent_names": ["Alex"],
                "pipeline_mode": "realtime",
                "realtime_voice": "sage",
            },
        }
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._real_global_config(),  # tenant default: cascaded
        ):
            config = build_telephony_session_config(campaign=campaign)
        assert config.pipeline_mode == "realtime"
        assert config.realtime_voice == "sage"

    def test_campaign_without_realtime_stays_cascaded(self):
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        campaign = {
            "id": "plain-campaign",
            "script_config": {"company_name": "Acme", "agent_names": ["Alex"]},
        }
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=self._real_global_config(),
        ):
            config = build_telephony_session_config(campaign=campaign)
        assert config.pipeline_mode == "cascaded"
