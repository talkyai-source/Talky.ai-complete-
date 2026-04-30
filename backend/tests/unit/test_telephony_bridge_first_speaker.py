"""
Tests that telephony_bridge first-speaker default is 'agent'.
"""
import os
import pytest
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
