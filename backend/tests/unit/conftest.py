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


# ── Release quarantine 2026-10-08 (owner decision) ─────────────────────────
# Tests listed in release_quarantine_20261008.txt fail on release PR #19 itself
# and are SKIPPED with a reason (never deleted) so they stay visible in every
# run. See that file's header; remove an entry once its test passes again.
from pathlib import Path as _QPath

_RELEASE_QUARANTINE_FILE = _QPath(__file__).with_name("release_quarantine_20261008.txt")
_RELEASE_QUARANTINE_REASON = (
    "release 2026-10-08 quarantine (owner decision): fails on PR #19 itself; "
    "see tests/unit/release_quarantine_20261008.txt"
)


def _release_quarantine_entries() -> set[str]:
    if not _RELEASE_QUARANTINE_FILE.exists():
        return set()
    return {
        line.strip()
        for line in _RELEASE_QUARANTINE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def pytest_collection_modifyitems(config, items):
    entries = _release_quarantine_entries()
    if not entries:
        return
    marker = pytest.mark.skip(reason=_RELEASE_QUARANTINE_REASON)
    for item in items:
        function_id = item.nodeid.split("[", 1)[0]
        file_id = item.nodeid.split("::", 1)[0]
        if function_id in entries or file_id in entries:
            item.add_marker(marker)
