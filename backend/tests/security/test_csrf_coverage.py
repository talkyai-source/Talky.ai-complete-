"""
AH-Phase-G structural CI guard — CSRF middleware coverage.

The CSRFMiddleware in `app.core.security.csrf` is mounted globally
on every state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`).
It exempts a small set of path prefixes via `_EXEMPT_PATH_PREFIXES`
— login, refresh, forgot-password, etc., which use their own
defenses (rate limits, single-use tokens, captcha).

This test locks in two invariants:

1. The middleware is actually wired in `configure_middleware`. A
   future refactor that forgets to mount it would silently drop the
   defense from every cookie-authed POST. This test fails if the
   import or `add_middleware` call disappears.

2. The exempt list contains ONLY the documented exemptions. A future
   contributor adding a new prefix to the list without thinking about
   it would silently open a hole; this test fails with a diff so the
   review comment is concrete.

The middleware itself is unit-tested in `test_csrf_middleware.py`;
this test guards the *coverage* shape, not the per-request behavior.
"""
from __future__ import annotations

import pathlib


def _read(relative: str) -> str:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return (repo_root / relative).read_text(encoding="utf-8")


def test_csrf_middleware_is_mounted_in_app_bootstrap():
    """configure_middleware must add CSRFMiddleware to the FastAPI app."""
    src = _read("app/core/app_bootstrap.py")
    assert "from app.core.security.csrf import CSRFMiddleware" in src, (
        "app_bootstrap must import CSRFMiddleware"
    )
    assert "add_middleware(CSRFMiddleware" in src, (
        "app_bootstrap must call app.add_middleware(CSRFMiddleware, ...). "
        "Without this, every state-changing request bypasses CSRF defense."
    )


# The set of path prefixes that legitimately bypass CSRF. Each entry
# carries a rationale that should be present in csrf.py near the
# definition. Adding a new prefix here in this test is a no-op until
# the same prefix is added in csrf.py — the actual loophole would be
# adding to csrf.py without updating this test.
EXPECTED_EXEMPT_PREFIXES = {
    "/api/v1/auth/login",          # rate-limited + captcha; cookies issued, not consumed
    "/api/v1/auth/signup",         # same shape as login
    "/api/v1/auth/register",       # same shape as login
    "/api/v1/auth/refresh",        # single-use rotation, family-revocation on reuse
    "/api/v1/auth/forgot-password",# rate-limited by email; emits a single-use code
    "/api/v1/auth/reset-password", # consumes the single-use code from above
    "/api/v1/auth/verify-email",   # one-shot signed link from email
    "/api/v1/auth/passkey",        # WebAuthn challenge response is not forgeable
}


def test_csrf_exempt_list_matches_expected_set():
    """
    The csrf.py _EXEMPT_PATH_PREFIXES tuple must equal the documented
    set above. Any drift surfaces as a diff so the review comment is
    concrete.
    """
    src = _read("app/core/security/csrf.py")
    # Extract every quoted "/api/v1/..." string between the
    # `_EXEMPT_PATH_PREFIXES = (` opening and the next `)`. Brittle
    # to formatting changes but adequate as a structural guard — and
    # any future refactor that breaks this regex also gets a review.
    import re
    match = re.search(
        r"_EXEMPT_PATH_PREFIXES\s*=\s*\(\s*(.*?)\)",
        src,
        re.DOTALL,
    )
    assert match is not None, "could not locate _EXEMPT_PATH_PREFIXES tuple in csrf.py"
    body = match.group(1)
    found = set(re.findall(r'"(/api/v1/[^"]*)"', body))
    extra = found - EXPECTED_EXEMPT_PREFIXES
    missing = EXPECTED_EXEMPT_PREFIXES - found
    assert not extra, (
        f"csrf.py exempts paths not in the expected set: {sorted(extra)}. "
        f"Every exemption needs a documented rationale. Update "
        f"EXPECTED_EXEMPT_PREFIXES in this test ONLY if you understand why "
        f"the new path is safe to bypass CSRF."
    )
    assert not missing, (
        f"csrf.py is missing expected exemptions: {sorted(missing)}. "
        f"These endpoints will start returning 403 unless updated."
    )
