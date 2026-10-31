"""Regression coverage for startup provider-validation log severity."""

from __future__ import annotations

import logging

from app.core.validation import ProviderValidator


_REQUIRED_ENV = {
    "DEEPGRAM_API_KEY": "test-deepgram",
    "CARTESIA_API_KEY": "test-cartesia",
    "GROQ_API_KEY": "test-groq",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "JWT_SECRET": "test-jwt-secret",
    "REDIS_URL": "redis://localhost:6379/0",
}


def _set_required_environment(monkeypatch) -> None:
    for name, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SECRET_KEY", raising=False)


def _clear_optional_vonage_environment(monkeypatch) -> None:
    for name, _description in ProviderValidator.OPTIONAL_ENV_VARS["telephony"]:
        monkeypatch.delenv(name, raising=False)


def test_missing_optional_vonage_settings_stay_warnings_in_strict_mode(
    monkeypatch,
    caplog,
) -> None:
    _set_required_environment(monkeypatch)
    _clear_optional_vonage_environment(monkeypatch)

    validator = ProviderValidator(strict=True)
    all_valid, _results = validator.validate_all()

    with caplog.at_level(logging.WARNING, logger="app.core.validation"):
        validator.log_results()

    optional_records = [
        record for record in caplog.records if "Vonage" in record.getMessage()
    ]
    assert all_valid is True
    assert len(optional_records) == 4
    assert {record.levelno for record in optional_records} == {logging.WARNING}
    assert not any(
        record.getMessage() == "Provider configuration errors:"
        for record in caplog.records
    )
    assert validator.get_error_summary() is None


def test_missing_required_setting_still_emits_error_header(monkeypatch, caplog) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.delenv("GROQ_API_KEY")
    for name, _description in ProviderValidator.OPTIONAL_ENV_VARS["telephony"]:
        monkeypatch.setenv(name, f"test-{name.lower()}")

    validator = ProviderValidator(strict=True)
    all_valid, _results = validator.validate_all()

    with caplog.at_level(logging.WARNING, logger="app.core.validation"):
        validator.log_results()

    assert all_valid is False
    assert any(
        record.levelno == logging.ERROR
        and record.getMessage() == "Provider configuration errors:"
        for record in caplog.records
    )
    assert "GROQ_API_KEY" in (validator.get_error_summary() or "")
