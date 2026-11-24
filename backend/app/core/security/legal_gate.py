"""API-side enforcement of legal acceptance (the LEGAL_ACCEPTANCE_REQUIRED switch).

``get_current_user`` calls :func:`enforce_legal_acceptance` once, after it has
built the user and BEFORE it publishes that user to the request. With the
switch off (the production state) it returns before any read. With the switch
on, a tenant user who has not accepted the current Terms and Privacy versions
is refused with 403 ``legal_acceptance_required`` on every route except the
OPEN routes below (sign-in/account routes, actions that stop something,
billing reads/cancel/portal).

The open list is matched against the real request path and method (the ASGI
``path`` with any ``root_path`` removed), never against the route object's own
path, which for a route of an included router lacks the outer prefixes.

:func:`acceptance_missing` takes a user and a pool and no Request. The
WebSocket handshakes through which a signed-in tenant user can START something
(assistant chat, assistant voice, campaign test call) call it.

Not covered: the dialler and workers, webhooks, telephony callbacks, calls in
progress, stopping/hang-up/transfer of a live call, other WebSocket routes, and
HTTP routes that authenticate without ``get_current_user``.
"""
import logging
import time
from collections import OrderedDict

from fastapi import HTTPException, Request
from starlette.routing import compile_path

from app.core import config
from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)

API = "/api/v1"

# Every route under these prefixes is open (sign-in, sign-out, refresh, "me",
# legal versions, the acceptance receipt itself, MFA, passkeys, ...).
OPEN_PREFIXES = (API + "/auth/",)

# (METHOD, path template, reason). Each template is compiled once and
# full-matched against the request path. tests/unit/test_legal_acceptance_gate.py
# asserts each exists in the real application.
OPEN_ROUTES = (
    ("GET", API + "/sessions/active", "session management"),
    ("GET", API + "/sessions/security-status", "session management"),
    ("POST", API + "/sessions/verify", "session management"),
    ("DELETE", API + "/sessions/{session_id}", "session management: ending a session"),
    # The page needs to know who the person is and what they may see before it
    # can draw anything, including the dialog that asks them to accept.
    ("GET", API + "/rbac/users/me/permissions", "who am I: the page cannot draw without it"),
    ("GET", API + "/rbac/users/me/tenants", "who am I: the page cannot draw without it"),
    ("POST", API + "/campaigns/{campaign_id}/pause", "stopping must never be blocked"),
    ("POST", API + "/campaigns/{campaign_id}/stop", "stopping must never be blocked"),
    ("POST", API + "/inbound-campaigns/{config_id}/pause", "stopping must never be blocked"),
    ("POST", API + "/calls/{call_id}/hangup", "hanging up must never be blocked"),
    ("POST", API + "/sip/telephony/hangup/{call_id}", "hanging up must never be blocked"),
    ("POST", API + "/sip/telephony/stop", "stopping must never be blocked"),
    ("POST", API + "/dnc/caller-opt-out", "a caller's opt-out stops calls to them"),
    ("GET", API + "/billing/adjustments", "billing read"),
    ("GET", API + "/billing/checkout-attempts/{request_id}", "billing read"),
    ("GET", API + "/billing/config", "billing read"),
    ("GET", API + "/billing/invoices", "billing read"),
    ("GET", API + "/billing/invoices/{invoice_id}", "billing read"),
    ("GET", API + "/billing/overage-alerts", "billing read"),
    ("GET", API + "/billing/paypal/subscriptions/{request_id}", "billing read"),
    ("GET", API + "/billing/plans", "billing read"),
    ("GET", API + "/billing/subscription", "billing read"),
    ("GET", API + "/billing/topups/balance", "billing read"),
    ("GET", API + "/billing/topups/ledger", "billing read"),
    ("GET", API + "/billing/topups/orders", "billing read"),
    ("GET", API + "/billing/topups/packages", "billing read"),
    ("GET", API + "/billing/topups/{order_id}", "billing read"),
    ("GET", API + "/billing/usage", "billing read"),
    ("GET", API + "/billing/usage/daily", "billing read"),
    ("POST", API + "/billing/cancel", "cancelling a plan must never be blocked"),
    ("POST", API + "/billing/portal", "billing portal (manage/cancel payment)"),
)

ACCEPTED_CACHE_MAX = 2048
ACCEPTED_CACHE_TTL_SECONDS = 60.0

LEGAL_CODE = "legal_acceptance_required"
LEGAL_MESSAGE = "Accept the current Terms and Privacy Policy to continue"

_OPEN_COMPILED = tuple((method, compile_path(path)[0]) for method, path, _ in OPEN_ROUTES)
# key (tenant_id, user_id, terms, privacy) -> monotonic expiry. Only "accepted".
_accepted: "OrderedDict[tuple, float]" = OrderedDict()
_unset_versions_logged = False


def _request_path(request: Request) -> str:
    """The path as Starlette routes it: scope path minus any root_path prefix."""
    path = request.scope.get("path") or ""
    root = request.scope.get("root_path") or ""
    if root and path.startswith(root):
        path = path[len(root):] or "/"
    return path


def _is_open(request: Request) -> bool:
    path = _request_path(request)
    if not path:
        return False  # unknown path: gated (fail closed)
    if path.startswith(OPEN_PREFIXES):
        return True
    method = request.method.upper()
    return any(m == method and regex.fullmatch(path) for m, regex in _OPEN_COMPILED)


def _cached_accepted(key: tuple) -> bool:
    expiry = _accepted.get(key)
    if expiry is None:
        return False
    if expiry <= time.monotonic():
        _accepted.pop(key, None)
        return False
    return True


def _remember_accepted(key: tuple) -> None:
    _accepted[key] = time.monotonic() + ACCEPTED_CACHE_TTL_SECONDS
    _accepted.move_to_end(key)
    while len(_accepted) > ACCEPTED_CACHE_MAX:
        _accepted.popitem(last=False)


async def _missing_versions(user, pool):
    """None when nothing is enforced for this user, else the current versions to check against.

    Makes no read. None for: the switch off, a platform admin, a user without
    a tenant, or versions that are not set (logged once). Otherwise
    (terms, privacy, the legal_acceptance module); whether THIS user has
    accepted them is decided by ``_owed``.
    """
    global _unset_versions_logged
    if not getattr(config.get_settings(), "legal_acceptance_required", False):
        return None
    from app.core.security.rbac import UserRole, normalize_role

    if not user.tenant_id or normalize_role(user.role) == UserRole.PLATFORM_ADMIN:
        return None
    from app.domain.services import legal_acceptance

    terms, privacy = legal_acceptance.current_versions()
    if not (terms and privacy):
        if not _unset_versions_logged:
            _unset_versions_logged = True
            logger.error("LEGAL_ACCEPTANCE_REQUIRED is on but the Terms/Privacy versions are not set; "
                         "legal acceptance is NOT enforced")
        return None
    return terms, privacy, legal_acceptance


async def _owed(user, pool, terms, privacy, legal_acceptance) -> bool:
    key = (user.tenant_id, user.id, terms, privacy)
    if _cached_accepted(key):
        return False
    async with acquire_with_tenant(pool, user.tenant_id) as conn:
        status = await legal_acceptance.acceptance_status(
            conn, tenant_id=user.tenant_id, user_id=user.id)
    if not status.get("legal_acceptance_required"):
        _remember_accepted(key)
        return False
    return True


async def acceptance_missing(user, pool) -> bool:
    """True when the switch is on and this tenant user has not accepted. No Request needed.

    For WebSocket handshakes. Same switch, cache and read as the HTTP gate; a
    failed read raises (callers must refuse), it is never treated as accepted.
    """
    found = await _missing_versions(user, pool)
    if found is None:
        return False
    terms, privacy, legal_acceptance = found
    return await _owed(user, pool, terms, privacy, legal_acceptance)


async def enforce_legal_acceptance(request: Request, user, pool) -> None:
    """Raise 403/503 when the switch is on and this user may not proceed."""
    found = await _missing_versions(user, pool)
    if found is None:
        return
    terms, privacy, legal_acceptance = found
    if _is_open(request):
        return
    try:
        owed = await _owed(user, pool, terms, privacy, legal_acceptance)
    except Exception as exc:
        logger.error("Legal acceptance lookup failed type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Account verification is temporarily unavailable") from exc
    if owed:
        raise HTTPException(status_code=403, detail={
            "code": LEGAL_CODE, "message": LEGAL_MESSAGE,
            "terms_version": terms, "privacy_version": privacy,
        })
