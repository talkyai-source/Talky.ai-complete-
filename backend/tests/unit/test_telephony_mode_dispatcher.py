"""Dispatcher resolves first_speaker per voice_session, falling back to env."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.services.telephony.modes import resolve_first_speaker


def test_per_call_attr_wins():
    sess = SimpleNamespace(_first_speaker="user")
    with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "agent"}):
        assert resolve_first_speaker(sess) == "user"


def test_falls_back_to_env_when_attr_missing():
    sess = SimpleNamespace()
    with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
        assert resolve_first_speaker(sess) == "user"


def test_falls_back_to_env_when_attr_none():
    sess = SimpleNamespace(_first_speaker=None)
    with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
        assert resolve_first_speaker(sess) == "user"


def test_unknown_value_clamps_to_agent():
    sess = SimpleNamespace(_first_speaker="bogus")
    with patch.dict(os.environ, {}, clear=True):
        assert resolve_first_speaker(sess) == "agent"
