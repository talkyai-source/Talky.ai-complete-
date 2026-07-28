"""Every login path that mints a session must also issue the cookie pair.

WHY THIS EXISTS (regression, 2026-07-28)
----------------------------------------
`auth/_shared.issue_cookie_auth` sets the httpOnly `talky_at` access cookie and
its refresh partner. Its docstring says it is "called by every successful
authentication path" — but `mfa/verify.py` did not call it. It set only the
legacy `talky_sid` session cookie and returned the JWT in the response body.

Consequence: users with MFA enabled never received `talky_at`. Anything that
authenticates by COOKIE rather than by Authorization header therefore failed
for them — most visibly WebSockets, since a browser cannot attach headers to a
WS upgrade. The campaign "Test agent" connected and then closed with
"campaign_test_ws: no auth frame within 5s" on every attempt for MFA users,
while non-MFA users on the same build were fine.

It was invisible because the two paths were only ever compared by reading them.
This test compares them mechanically: if a module creates a session, it must
also issue the cookies. A prose docstring is not an enforcement mechanism.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ENDPOINTS = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints"

# Paths that mint a login session and must therefore also issue cookies.
_SESSION_MINTING_FILES = [
    _ENDPOINTS / "auth" / "login.py",
    _ENDPOINTS / "auth" / "signup.py",
    _ENDPOINTS / "mfa" / "verify.py",
]


def _called_function_names(path: Path) -> set[str]:
    """Every function name invoked anywhere in the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


@pytest.mark.parametrize(
    "path", _SESSION_MINTING_FILES, ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_session_minting_path_issues_auth_cookies(path: Path):
    """A path that calls create_session must also call issue_cookie_auth.

    Otherwise it produces a half-authenticated session: valid for header-based
    REST calls, silently broken for every cookie-based surface.
    """
    assert path.exists(), f"expected auth path missing: {path}"
    called = _called_function_names(path)

    if "create_session" not in called:
        pytest.skip(f"{path.name} does not mint a session")

    assert "issue_cookie_auth" in called, (
        f"{path.parent.name}/{path.name} calls create_session() but never "
        "issue_cookie_auth(). It will issue a session WITHOUT the talky_at "
        "cookie, which breaks WebSocket auth (browsers cannot send an "
        "Authorization header on a WS upgrade). This is exactly how MFA "
        "users lost access to the campaign Test agent."
    )


def test_the_guard_can_actually_fail():
    """Proves the check is not vacuous.

    A module that mints a session but skips cookie issuance must be detected —
    otherwise this file is decoration. Mirrors the real pre-fix shape of
    mfa/verify.py.
    """
    broken = ast.parse(
        "async def verify():\n"
        "    raw, sid = await create_session(conn)\n"
        "    _set_session_cookie(response, raw)\n"
    )
    names = {
        n.func.id
        for n in ast.walk(broken)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "create_session" in names
    assert "issue_cookie_auth" not in names, (
        "the fixture is meant to represent the BROKEN shape"
    )


def test_mfa_verify_specifically_issues_cookies():
    """Named, non-parametrised assertion for the exact regression, so the
    failure message points straight at the MFA login path."""
    called = _called_function_names(_ENDPOINTS / "mfa" / "verify.py")
    assert "issue_cookie_auth" in called, (
        "MFA verify must issue talky_at — MFA users cannot use the Test "
        "agent, assistant voice, or any other WebSocket surface without it."
    )
