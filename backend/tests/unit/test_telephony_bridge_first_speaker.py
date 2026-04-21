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
