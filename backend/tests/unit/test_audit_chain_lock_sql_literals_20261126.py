"""The chain lock statement is constant text; its literals must equal the module constants."""
import inspect

from app.domain.services import audit_logger
from app.domain.services.audit_logger import AuditLogger


def test_lock_transaction_literals_match_the_module_constants():
    src = inspect.getsource(AuditLogger.lock_transaction)
    assert str(audit_logger._CHAIN_LOCK_KEY) in src
    assert "'%d'" % audit_logger._CHAIN_LOCK_TIMEOUT_MS in src
    assert 'f"' not in src.split('"""')[-1], "the statement must not be an f-string"
