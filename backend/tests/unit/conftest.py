"""Shared test fixtures for unit tests.

Telephony settings reset (T4-C5)
================================
The TelephonySettings singleton caches a snapshot of env at first read.
Many existing unit tests monkeypatch env vars then call code that
reads from the snapshot — without resetting the cache, those tests
would see stale values.

The autouse fixture below clears the snapshot before AND after every
test, so:

* Tests that monkeypatch.setenv("TELEPHONY_*", ...) get a fresh read.
* Tests that don't touch env see the natural defaults.
* No test pollutes another via leftover env state.

Same idea applies to the ``VoiceTuningResolver`` singleton, which the
T3.9 tests reset explicitly. Centralised here so the responsibility
moves out of every individual test file.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_telephony_settings_singletons():
    """Clear cached settings before and after each test."""
    try:
        from app.core.telephony_settings import reset_telephony_settings
        reset_telephony_settings()
    except ImportError:
        pass
    try:
        from app.domain.services.voice_tuning import reset_voice_tuning_resolver
        reset_voice_tuning_resolver()
    except ImportError:
        pass

    yield

    try:
        from app.core.telephony_settings import reset_telephony_settings
        reset_telephony_settings()
    except ImportError:
        pass
    try:
        from app.domain.services.voice_tuning import reset_voice_tuning_resolver
        reset_voice_tuning_resolver()
    except ImportError:
        pass
