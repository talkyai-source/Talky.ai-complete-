"""Support settings: safe defaults, bounded numbers and a fail-closed pilot list."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.domain.services.support import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("SUPPORT_"):
            monkeypatch.delenv(name, raising=False)


def test_ships_switched_off():
    settings = config.load_settings()
    assert settings.enabled is False
    assert settings.enabled_for_tenant(str(uuid4())) is False


def test_enabled_with_empty_allowlist_admits_every_tenant(monkeypatch):
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    assert config.load_settings().enabled_for_tenant(str(uuid4())) is True


def test_allowlist_admits_only_listed_tenants(monkeypatch):
    listed, other = str(uuid4()), str(uuid4())
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_TENANT_ALLOWLIST", f" {listed.upper()} ,")
    settings = config.load_settings()
    assert settings.enabled_for_tenant(listed) is True
    assert settings.enabled_for_tenant(other) is False
    assert settings.enabled_for_tenant(None) is False


def test_malformed_allowlist_fails_closed_instead_of_opening_to_everyone(monkeypatch):
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_TENANT_ALLOWLIST", "not-a-uuid")
    settings = config.load_settings()
    assert settings.enabled_for_tenant(str(uuid4())) is False


def test_defaults_match_the_brief():
    settings = config.load_settings()
    assert settings.rate_limit_per_minute == 10
    assert settings.daily_message_cap_per_tenant == 300
    assert settings.max_turns == 20
    assert settings.attachment_max_bytes == 10 * 1024 * 1024
    assert settings.transcript_retention_days == 180
    assert settings.sla["urgent"] == (timedelta(hours=1), timedelta(hours=8))
    assert settings.sla["high"] == (timedelta(hours=4), timedelta(hours=24))
    assert settings.sla["normal"] == (timedelta(hours=24), timedelta(hours=72))
    assert settings.sla["low"] == (timedelta(hours=72), timedelta(days=7))


@pytest.mark.parametrize("value", ["0", "-3", "abc", "100000"])
def test_out_of_range_numbers_keep_the_safe_default(monkeypatch, value):
    monkeypatch.setenv("SUPPORT_MAX_TURNS", value)
    monkeypatch.setenv("SUPPORT_RATE_LIMIT_PER_MINUTE", value)
    settings = config.load_settings()
    assert settings.max_turns == 20
    assert settings.rate_limit_per_minute == 10


def test_sla_minutes_are_configurable(monkeypatch):
    monkeypatch.setenv("SUPPORT_SLA_URGENT_FIRST_RESPONSE_MINUTES", "30")
    monkeypatch.setenv("SUPPORT_SLA_URGENT_RESOLUTION_MINUTES", "240")
    assert config.load_settings().sla["urgent"] == (timedelta(minutes=30), timedelta(hours=4))


def test_resolution_deadline_never_precedes_first_response(monkeypatch):
    monkeypatch.setenv("SUPPORT_SLA_LOW_FIRST_RESPONSE_MINUTES", "600")
    monkeypatch.setenv("SUPPORT_SLA_LOW_RESOLUTION_MINUTES", "60")
    first, resolution = config.load_settings().sla["low"]
    assert resolution >= first


def test_unknown_bot_model_falls_back_to_an_allowed_assistant_model(monkeypatch):
    from app.infrastructure.assistant.model_config import DEFAULT_ASSISTANT_MODEL

    monkeypatch.setenv("SUPPORT_BOT_MODEL", "made-up/model")
    assert config.load_settings().bot_model == DEFAULT_ASSISTANT_MODEL


def test_rate_limit_string_uses_the_setting(monkeypatch):
    monkeypatch.setenv("SUPPORT_RATE_LIMIT_PER_MINUTE", "4")
    assert config.message_rate_limit() == "4/minute"
