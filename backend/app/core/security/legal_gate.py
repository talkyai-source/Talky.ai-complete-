"""API-side enforcement of legal acceptance (the LEGAL_ACCEPTANCE_REQUIRED switch).

``get_current_user`` calls :func:`enforce_legal_acceptance` once, as its last
step. With the switch off (the production state) it returns before any read.
With the switch on, a tenant user who has not accepted the current Terms and
Privacy versions is refused with 403 ``legal_acceptance_required`` on every
route except the OPEN routes below (sign-in/account routes, actions that stop
something, billing reads/cancel/portal).

Not covered: the dialler and workers, webhooks, telephony callbacks, calls in
progress, and WebSocket routes (they authenticate separately).
"""
import logging
import time
from collections import OrderedDict

from fastapi import HTTPException, Request

from app.core import config
from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)

API = "/api/v1"

# Every route under these prefixes is open (sign-in, sign-out, refresh, "me",
# legal versions, the acceptance receipt itself, MFA, passkeys, ...).
OPEN_PREFIXES = (API + "/auth/",)

# (METHOD, route path template, reason). Matched on the route's template, never
# on the raw URL. tests/unit/test_legal_acceptance_gate.py asserts each exists.
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

_OPEN_EXACT = frozenset((method, path) for method, path, _ in OPEN_ROUTES)
# key (tenant_id, user_id, terms, privacy) -> monotonic expiry. Only "accepted".
_accepted: "OrderedDict[tuple, float]" = OrderedDict()
_unset_versions_logged = False


def _is_open(request: Request) -> bool:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return False  # unknown route: gated (fail closed)
    if template.startswith(OPEN_PREFIXES):
        return True
    return (request.method.upper(), template) in _OPEN_EXACT


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


async def enforce_legal_acceptance(request: Request, user, pool) -> None:
    """Raise 403/503 when the switch is on and this user may not proceed."""
    global _unset_versions_logged
    if not getattr(config.get_settings(), "legal_acceptance_required", False):
        return
    from app.core.security.rbac import UserRole, normalize_role

    if not user.tenant_id or normalize_role(user.role) == UserRole.PLATFORM_ADMIN:
        return
    from app.domain.services import legal_acceptance

    terms, privacy = legal_acceptance.current_versions()
    if not (terms and privacy):
        if not _unset_versions_logged:
            _unset_versions_logged = True
            logger.error("LEGAL_ACCEPTANCE_REQUIRED is on but the Terms/Privacy versions are not set; "
                         "legal acceptance is NOT enforced")
        return
    if _is_open(request):
        return
    key = (user.tenant_id, user.id, terms, privacy)
    if _cached_accepted(key):
        return
    try:
        async with acquire_with_tenant(pool, user.tenant_id) as conn:
            status = await legal_acceptance.acceptance_status(
                conn, tenant_id=user.tenant_id, user_id=user.id)
    except Exception as exc:
        logger.error("Legal acceptance lookup failed type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Account verification is temporarily unavailable") from exc
    if not status.get("legal_acceptance_required"):
        _remember_accepted(key)
        return
    raise HTTPException(status_code=403, detail={
        "code": LEGAL_CODE, "message": LEGAL_MESSAGE,
        "terms_version": terms, "privacy_version": privacy,
    })
