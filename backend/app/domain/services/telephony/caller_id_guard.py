"""Caller-ID ownership enforcement for outbound origination (T0.1).

Leaf module — depends only on TenantPhoneNumberService and the standard
library. Deliberately framework-free (no FastAPI import): it returns a
decision object and the calling endpoint translates a denial into the
HTTP 403. This keeps the telephony service package transport-agnostic,
matching config.py / modes/ / lifecycle.py.

Before any guard/originate work, an outbound call must prove the
``caller_id`` is registered AND verified under the dialing tenant. In
production we also require a STIR/SHAKEN attestation token on the DID row
(test-only numbers cannot dial real carriers).

Ramp-in knob — ``CALLER_ID_ENFORCEMENT_MODE = enforce | log | off``:
  * ``enforce`` (default in prod): violation → caller should return 403.
  * ``log``     (default in dev/staging): violation → WARN + allow.
  * ``off``     : disabled entirely. Use only for first-time bring-up.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.domain.services.tenant_phone_number_service import (
    TenantPhoneNumberService,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallerIdDecision:
    """Outcome of the caller-ID ownership check.

    ``allowed`` is False only when the number is unverified AND the
    enforcement mode is ``enforce`` — that's the single case where the
    endpoint must refuse origination with a 403. ``log`` and ``off`` modes
    always allow. The remaining fields let the endpoint build a 403 body
    identical to the pre-extraction handler.
    """
    allowed: bool
    enforcement_mode: str
    require_attestation: bool
    caller_id: str


def resolve_enforcement_mode(environment: str) -> str:
    """Resolve CALLER_ID_ENFORCEMENT_MODE with env-appropriate default.

    Prod defaults to ``enforce``; everything else to ``log``. An
    unrecognised value falls back to that default rather than failing
    open or closed unexpectedly.
    """
    default_mode = "enforce" if environment == "production" else "log"
    mode = os.getenv("CALLER_ID_ENFORCEMENT_MODE", default_mode).strip().lower()
    if mode not in {"enforce", "log", "off"}:
        mode = default_mode
    return mode


async def check_caller_id_ownership(
    db_pool,
    *,
    tenant_id: str,
    caller_id: str,
    environment: str,
) -> CallerIdDecision:
    """Check that ``caller_id`` is verified for ``tenant_id``.

    Never raises for a verification failure — returns a
    :class:`CallerIdDecision` the caller acts on. (The underlying service
    is itself fail-closed: any DB error yields "not verified" rather than
    a 500, so origination gets a clean denial.)
    """
    require_attestation = environment == "production"
    enforcement_mode = resolve_enforcement_mode(environment)

    if enforcement_mode == "off":
        return CallerIdDecision(
            allowed=True,
            enforcement_mode=enforcement_mode,
            require_attestation=require_attestation,
            caller_id=caller_id,
        )

    did_svc = TenantPhoneNumberService(db_pool)
    try:
        caller_id_ok = await did_svc.is_verified_for_tenant(
            tenant_id=str(tenant_id),
            e164=caller_id,
            require_attestation=require_attestation,
        )
    except Exception as did_exc:
        logger.error(
            "caller_id_verification_lookup_failed tenant=%s caller_id=%s err=%s",
            tenant_id, caller_id, did_exc,
        )
        caller_id_ok = False

    if not caller_id_ok:
        logger.warning(
            "caller_id_unauthorized tenant=%s caller_id=%s mode=%s "
            "environment=%s require_attestation=%s",
            tenant_id, caller_id, enforcement_mode,
            environment, require_attestation,
        )

    # `log` mode warns (above) but still allows; only `enforce` denies.
    allowed = caller_id_ok or enforcement_mode != "enforce"
    return CallerIdDecision(
        allowed=allowed,
        enforcement_mode=enforcement_mode,
        require_attestation=require_attestation,
        caller_id=caller_id,
    )
