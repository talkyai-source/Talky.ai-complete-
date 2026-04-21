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
    def test_greeting_contains_agent_name(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("John", "All States Estimation")
        assert "John" in result

    def test_greeting_contains_company_name(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("John", "All States Estimation")
        assert "All States Estimation" in result

    def test_greeting_mentions_estimate(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("Sarah", "TestCo")
        assert "estimate" in result.lower() or "repair" in result.lower()

    def test_greeting_is_a_non_empty_string(self):
        from app.domain.services.telephony_session_config import build_telephony_greeting
        result = build_telephony_greeting("Alex", "TestCo")
        assert isinstance(result, str) and len(result) > 0


class TestEstimationSystemPrompt:
    def test_system_prompt_template_has_agent_name_slot(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        assert "{agent_name}" in TELEPHONY_ESTIMATION_SYSTEM_PROMPT

    def test_system_prompt_template_has_company_name_slot(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        assert "{company_name}" in TELEPHONY_ESTIMATION_SYSTEM_PROMPT

    def test_system_prompt_forbids_ai_reveal(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        prompt_lower = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.lower()
        assert "ai" in prompt_lower or "robot" in prompt_lower

    def test_system_prompt_formats_cleanly(self):
        from app.domain.services.telephony_session_config import TELEPHONY_ESTIMATION_SYSTEM_PROMPT
        rendered = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
            agent_name="John", company_name="All States Estimation"
        )
        assert "John" in rendered
        assert "All States Estimation" in rendered
        assert "{" not in rendered  # no unfilled slots


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
