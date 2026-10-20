"""auth-2: a forged leftmost X-Forwarded-For must not change the resolved IP."""
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.auth._shared import _real_client_ip, get_client_ip
from app.api.v1.endpoints.secrets import _validate_client_ip
from app.core.session_security_middleware import SessionSecurityMiddleware


def _req(peer, headers=None):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None,
        headers={k: v for k, v in (headers or {}).items()},
    )


def _session_ip(request):
    return SessionSecurityMiddleware._get_client_ip(object.__new__(SessionSecurityMiddleware), request)


RESOLVERS = {
    "session": _session_ip,
    "secrets": _validate_client_ip,
    "auth_real": _real_client_ip,
    "auth_get": get_client_ip,
}


@pytest.mark.parametrize("name", list(RESOLVERS))
def test_forged_leftmost_xff_ignored_behind_proxy(name):
    fn = RESOLVERS[name]
    honest = _req("127.0.0.1", {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"})
    forged = _req("127.0.0.1", {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert fn(honest) == fn(forged) == "203.0.113.9"


@pytest.mark.parametrize("name", list(RESOLVERS))
def test_forged_xff_without_real_ip_uses_rightmost_untrusted_hop(name):
    fn = RESOLVERS[name]
    req = _req("10.0.0.5", {"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert fn(req) == "203.0.113.9"


@pytest.mark.parametrize("name", list(RESOLVERS))
def test_direct_public_peer_headers_ignored(name):
    fn = RESOLVERS[name]
    req = _req("198.51.100.7", {"X-Real-IP": "6.6.6.6", "X-Forwarded-For": "6.6.6.6"})
    assert fn(req) == "198.51.100.7"


@pytest.mark.parametrize("name", list(RESOLVERS))
def test_no_client_is_unknown(name):
    assert RESOLVERS[name](_req(None, {"X-Forwarded-For": "6.6.6.6"})) == "unknown"


def test_rate_limit_dependency_ignores_forged_xff():
    import asyncio
    from unittest.mock import patch

    from app.core.security import api_security

    seen = {}

    class _Stop(Exception):
        pass

    class _Limiter:
        def __getattr__(self, _):
            async def _f(*a, **kw):
                seen["args"] = (a, kw)
                raise _Stop()
            return _f

    def run(headers):
        seen.clear()
        req = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers=headers,
            state=SimpleNamespace(),
            url=SimpleNamespace(path="/x"),
            method="GET",
        )
        with patch.object(api_security, "get_api_rate_limiter", return_value=_Limiter()), \
             patch("app.core.container.get_container", return_value=SimpleNamespace(redis=None, is_initialized=False)):
            try:
                asyncio.run(api_security.rate_limit_dependency(req))
            except _Stop:
                pass
        return repr(seen.get("args"))

    a = run({"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"})
    b = run({"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert "6.6.6.6" not in b
    assert a == b
