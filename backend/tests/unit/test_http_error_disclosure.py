"""F2-07 HTTP boundary: exception contents stay in correlated server logs."""
import ast
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.core import error_handlers
from app.core.request_id_middleware import RequestIdMiddleware


API = Path(__file__).resolve().parents[2] / 'app/api'
# These sockets belong to Claude; their HTTP declarations contain no exception
# detail today. Do not edit them to remediate WebSocket payloads in this task.
FENCED = {'ask_ai_ws.py', 'campaign_test_ws.py'}
SECRET = 'synthetic-password=/private/fixture.sql SELECT * FROM tenants'


def exception_details(source):
    """Catch direct str/repr, formatted and structured exception text in detail.

    The guard tracks except bindings and exception-typed/name parameters. It
    intentionally does not classify ordinary str(row['status']) as an error.
    """
    tree = ast.parse(source)
    names = {'exc', 'exception', 'err', 'error'}
    names.update(node.name for node in ast.walk(tree)
                 if isinstance(node, ast.ExceptHandler) and node.name)
    findings = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        for keyword in call.keywords:
            if keyword.arg != 'detail':
                continue
            for node in ast.walk(keyword.value):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in {'str', 'repr'} and node.args
                        and isinstance(node.args[0], ast.Name) and node.args[0].id in names):
                    findings.append(node.lineno)
                elif (isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name)
                      and node.value.id in names):
                    findings.append(node.lineno)
    return sorted(set(findings))


@pytest.mark.parametrize('expression', ['str(failure)', 'f"Failed: {failure}"',
    '{"message": str(failure)}', 'repr(failure)', 'f"Failure: {str(failure)}"'])
def test_planted_exception_text_is_rejected_by_http_guard(expression):
    assert exception_details(f'try:\n    work()\nexcept Exception as failure:\n    raise HTTPException(500, detail={expression})')


def test_http_guard_preserves_nonexception_string_conversion():
    assert not exception_details('raise HTTPException(409, detail={"status": str(row["status"])})')


def test_mounted_http_source_does_not_put_exception_text_in_detail():
    failures = []
    for path in sorted(API.rglob('*.py')):
        if path.name in FENCED:
            continue
        failures.extend(f'{path.relative_to(API)}:{line}'
                        for line in exception_details(path.read_text(encoding='utf-8-sig')))
    assert not failures, 'Raw exception text in HTTP detail: ' + ', '.join(failures)


@pytest.mark.parametrize('status', [400, 422, 500, 502])
def test_fixed_http_failure_is_correlated_and_keeps_exception_only_in_server_log(caplog, status):
    safe_message = getattr(error_handlers, 'safe_exception_message', None)
    assert callable(safe_message), 'HTTP exception disclosure has no shared safe-message boundary'
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    error_handlers.register_error_handlers(app)

    @app.get('/synthetic')
    def endpoint():
        try:
            raise RuntimeError(SECRET)
        except RuntimeError as exc:
            raise HTTPException(status, detail=safe_message(exc, 'The operation could not be completed.')) from exc

    with TestClient(app) as client:
        response = client.get('/synthetic')
    assert response.status_code == status
    assert SECRET not in response.text
    assert response.json()['error']['message'] == 'The operation could not be completed.'
    request_id = response.headers['x-request-id']
    assert response.json()['error']['request_id'] == request_id
    assert request_id in caplog.text
    assert any(record.exc_info and str(record.exc_info[1]) == SECRET for record in caplog.records)


def test_password_policy_keeps_only_fixed_public_diagnostics():
    from app.core.security.password import PasswordValidationError
    safe_message = getattr(error_handlers, 'safe_exception_message', None)
    assert callable(safe_message), 'HTTP exception disclosure has no shared safe-message boundary'
    for message in ('Choose a password that is not commonly used.',
                    'Password must be at least 15 characters.',
                    'Password cannot exceed 128 characters.', 'Password cannot be empty.'):
        assert safe_message(PasswordValidationError(message), 'Invalid password.') == message
    assert safe_message(PasswordValidationError(SECRET), 'Invalid password.') == 'Invalid password.'
