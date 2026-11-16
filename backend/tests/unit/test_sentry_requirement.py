"""The error-tracking library the backend imports is listed in its requirements.

Frontend list item D5-4: `app/core/sentry_init.py` imports `sentry_sdk`, but
the package was in no requirements file, so backend error tracking silently
switched itself off on every install. This is a guard over the file, plus one
behaviour check: with the package installed and a DSN set, start-up no longer
reports that it was skipped for a missing library.
"""

from __future__ import annotations

import importlib.metadata
import logging
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
PIN = re.compile(r"^sentry-sdk==(\d+\.\d+\.\d+)\s*$", re.MULTILINE)


def _pinned_version() -> str:
    match = PIN.search((BACKEND / "requirements.txt").read_text(encoding="utf-8"))
    assert match, "requirements.txt must pin sentry-sdk to one exact version"
    return match.group(1)


def test_requirements_pin_the_error_tracking_library():
    assert _pinned_version()
    source = (BACKEND / "app" / "core" / "sentry_init.py").read_text(encoding="utf-8")
    assert "import sentry_sdk" in source


def test_the_installed_library_is_the_pinned_one():
    try:
        installed = importlib.metadata.version("sentry-sdk")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("sentry-sdk is not installed in this environment")
    assert installed == _pinned_version()


def test_start_up_does_not_skip_for_a_missing_library(monkeypatch, caplog):
    sentry_sdk = pytest.importorskip("sentry_sdk")
    import app.core.sentry_init as mod

    started = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: started.update(kwargs))
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/0")
    with caplog.at_level(logging.INFO):
        assert mod.init_sentry() is True
    assert started["dsn"].startswith("https://")
    assert "sentry_sdk_not_installed" not in caplog.text
    assert "sentry_skipped" not in caplog.text
