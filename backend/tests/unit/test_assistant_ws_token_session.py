"""The assistant socket token carries the caller's session (QA F11-07, QA-PERF-004).

The socket opens only with a session-bound token: ``load_session_principal``
reads its ``sid`` and checks that the login session is still live. The
fallback token from ``GET /assistant/ws-token`` was minted without one, so it
could never open the socket and the assistant showed "session ended" while
the rest of the app stayed signed in.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.assistant_config import assistant_ws_token
from app.core import jwt_security
from app.core.jwt_security import decode_and_validate_token


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    # Patch only the secret lookup: clearing the shared settings cache here
    # changes what later tests in the same process read.
    monkeypatch.setattr(jwt_security, "_require_secret",
                        lambda: "unit-test-secret-with-minimum-length-48-bytes-0001")
    monkeypatch.setattr(jwt_security, "_resolve_algorithm", lambda: "HS256")


def _user():
    return CurrentUser(id=str(uuid4()), email="owner@example.invalid", tenant_id=str(uuid4()),
                       role="tenant_admin")


@pytest.mark.asyncio
async def test_the_socket_token_is_bound_to_the_verified_session():
    session = str(uuid4())
    request = SimpleNamespace(state=SimpleNamespace(authenticated_session_id=session))

    claims = decode_and_validate_token((await assistant_ws_token(request, current_user=_user()))["token"])

    # What the socket's load_session_principal parses before checking the session.
    assert str(UUID(str(claims.get("sid")))) == session


@pytest.mark.asyncio
async def test_no_token_is_minted_without_a_verified_session():
    request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(HTTPException) as refused:
        await assistant_ws_token(request, current_user=_user())
    assert refused.value.status_code == 401
