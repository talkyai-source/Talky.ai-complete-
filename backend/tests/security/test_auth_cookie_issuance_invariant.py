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


def _discover_session_minting_files() -> list[Path]:
    """Every endpoint module that calls create_session().

    DISCOVERED, never enumerated. The first version of this test listed three
    files by hand and consequently missed `passkeys.py`, which had the exact
    defect the test exists to catch — repeating the mistake that caused the
    original bug, where a cross-cutting auth change was scoped to the `auth/`
    directory and skipped the MFA path living in `mfa/`.

    An auth path added in a fourth location tomorrow is covered automatically.

    Detection is by IMPORT, not by substring. `emergency_access.py` calls
    `emergency_access.create_session(...)` — a method on a different service
    that mints a break-glass bearer token returned in the body, with no browser
    session or cookie involved. A substring match flags it wrongly. Requiring
    the symbol to be imported from the auth session module identifies the real
    login sessions precisely, so no allowlist is needed.
    """
    found: list[Path] = []
    for path in sorted(_ENDPOINTS.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        imports_auth_create_session = any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "security.sessions" in node.module
            and any(alias.name == "create_session" for alias in node.names)
            for node in ast.walk(tree)
        )
        if imports_auth_create_session:
            found.append(path)
    return found


_SESSION_MINTING_FILES = _discover_session_minting_files()


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
