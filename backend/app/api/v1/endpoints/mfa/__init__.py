"""MFA (Multi-Factor Authentication) Endpoints — TOTP + Recovery Codes.

Two-step login flow (when MFA is enabled):
  1. POST /auth/login        → password verified → returns mfa_challenge_token (not a JWT)
  2. POST /auth/mfa/verify   → TOTP code + challenge_token → returns full JWT + session cookie

MFA management flow (requires a full auth JWT):
  POST /auth/mfa/setup       → generates secret + QR code (MFA not yet active)
  POST /auth/mfa/confirm     → user enters first valid TOTP code → activates MFA
                                                                  → returns recovery codes (once)
  GET  /auth/mfa/status      → returns whether MFA is enabled for current user
  POST /auth/mfa/disable     → requires current password → disables MFA + deletes recovery codes
  POST /auth/mfa/recovery-codes/regenerate
                             → requires valid TOTP → replaces all recovery codes (once)

Public surface mirrors the previous single-file `mfa.py` — `router`,
`create_mfa_challenge`, `resolve_mfa_challenge`, and all schemas are
re-exported so existing imports keep working unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter

# Sub-modules carrying their own routers
from . import (
    recovery as _recovery_mod,
    setup as _setup_mod,
    status as _status_mod,
    verify as _verify_mod,
)

# Helpers used by auth/login and other modules
from .challenge import (
    consume_mfa_challenge,
    create_mfa_challenge,
    resolve_mfa_challenge,
)

# Schemas (re-exported for convenience)
from .schemas import (
    MFAChallengeVerifyRequest,
    MFAChallengeVerifyResponse,
    MFAConfirmRequest,
    MFAConfirmResponse,
    MFADisableRequest,
    MFARegenerateCodesRequest,
    MFARegenerateCodesResponse,
    MFASetupResponse,
    MFAStatusResponse,
)

# Aggregate router carries the /auth/mfa prefix; sub-routers add their paths.
router = APIRouter(prefix="/auth/mfa", tags=["mfa"])
router.include_router(_setup_mod.router)
router.include_router(_verify_mod.router)
router.include_router(_status_mod.router)
router.include_router(_recovery_mod.router)


__all__ = [
    "router",
    # Helpers used by auth.py login flow
    "consume_mfa_challenge",
    "create_mfa_challenge",
    "resolve_mfa_challenge",
    # Schemas
    "MFAChallengeVerifyRequest",
    "MFAChallengeVerifyResponse",
    "MFAConfirmRequest",
    "MFAConfirmResponse",
    "MFADisableRequest",
    "MFARegenerateCodesRequest",
    "MFARegenerateCodesResponse",
    "MFASetupResponse",
    "MFAStatusResponse",
]
