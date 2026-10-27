"""Every writer of the existing global chain uses the same configured key."""
from types import SimpleNamespace

import pytest

from app.api.v1 import dependencies
from app.domain.services import audit_logger
from app.domain.services.suspension_service import SuspensionService
from app.services.connector_lifecycle import ConnectorActor, ConnectorLifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["development", "staging", "production"])
async def test_dedicated_audit_key_is_shared_by_admin_connectors_and_background(monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("AUDIT_LOG_SIGNING_KEY", "synthetic-dedicated-audit-key")
    monkeypatch.setenv("JWT_SECRET", "different-synthetic-jwt-key")
    monkeypatch.setattr(dependencies, "_audit_logger_service", None)
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        pool = object()
        direct = audit_logger.AuditLogger(pool)
        admin = await dependencies.get_audit_logger(pool)
        connector = ConnectorLifecycle(SimpleNamespace(pool=pool), actor=ConnectorActor.system("test"))
        background = await audit_logger.get_audit_logger(pool)
        suspension = SuspensionService(pool)._required_audit()
        assert direct.signing_key == admin.signing_key == connector.audit.signing_key == background.signing_key == suspension.signing_key == "synthetic-dedicated-audit-key"
    finally:
        get_settings.cache_clear()


def test_dotenv_settings_fallback_is_shared_and_production_without_key_fails_closed(monkeypatch):
    for name in ("AUDIT_LOG_SIGNING_KEY", "APP_AUDIT_LOG_SIGNING_KEY", "JWT_SECRET", "SECRET_KEY", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    settings = SimpleNamespace(environment="production", effective_jwt_secret="synthetic-dotenv-key")
    monkeypatch.setattr(audit_logger, "get_settings", lambda: settings)
    assert audit_logger.AuditLogger(object()).signing_key == "synthetic-dotenv-key"
    settings.effective_jwt_secret = None
    with pytest.raises(RuntimeError, match="must be configured"):
        audit_logger.AuditLogger(object())


def test_explicit_test_key_and_legacy_alias_remain_supported(monkeypatch):
    monkeypatch.delenv("AUDIT_LOG_SIGNING_KEY", raising=False)
    monkeypatch.setenv("APP_AUDIT_LOG_SIGNING_KEY", "synthetic-legacy-audit-key")
    monkeypatch.setenv("JWT_SECRET", "different-jwt-key")
    assert audit_logger.AuditLogger(object()).signing_key == "synthetic-legacy-audit-key"
    assert audit_logger.AuditLogger(object(), signing_key="isolated-test-key").signing_key == "isolated-test-key"
